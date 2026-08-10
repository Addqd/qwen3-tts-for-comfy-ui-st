from __future__ import annotations

import argparse
import gc
import json
import math
import os
import random
import shutil
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from accelerate import Accelerator
from huggingface_hub import snapshot_download
from peft import LoraConfig, PeftModel, get_peft_model
from torch.optim import AdamW
from torch.utils.data import DataLoader

from qwen_tts.inference.qwen3_tts_model import Qwen3TTSModel
from training.russian_adaptation.common import PROJECT_ROOT, iter_jsonl, load_plan, project_path, write_json_atomic
from training.russian_adaptation.dataset import RussianAdaptationDataset


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def freeze_base(model: nn.Module) -> None:
    for parameter in model.parameters():
        parameter.requires_grad = False


def validate_trainable_parameters(model: nn.Module) -> dict[str, Any]:
    trainable = [(name, parameter.numel()) for name, parameter in model.named_parameters() if parameter.requires_grad]
    if not trainable:
        raise RuntimeError("LoRA injection produced no trainable parameters")
    forbidden = (
        "speaker_encoder",
        "code_predictor",
        "text_embedding",
        "codec_embedding",
        "text_projection",
        "codec_head",
        "speech_tokenizer",
    )
    bad = [name for name, _ in trainable if "lora_" not in name or any(part in name for part in forbidden)]
    if bad:
        raise RuntimeError(f"Unsafe trainable parameter set: {bad[:5]}")
    return {
        "trainable_parameter_count": sum(count for _, count in trainable),
        "trainable_tensor_count": len(trainable),
        "sample_names": [name for name, _ in trainable[:8]],
    }


class SelectiveTrainingModel(nn.Module):
    def __init__(self, adapter_model: PeftModel, subtalker_weight: float):
        super().__init__()
        self.adapter_model = adapter_model
        self.subtalker_weight = subtalker_weight

    def forward(self, batch: dict[str, torch.Tensor]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        core = self.adapter_model.get_base_model()
        input_ids = batch["input_ids"]
        codec_ids = batch["codec_ids"]
        ref_mels = batch["ref_mels"]
        text_embedding_mask = batch["text_embedding_mask"]
        codec_embedding_mask = batch["codec_embedding_mask"]
        attention_mask = batch["attention_mask"]
        codec_0_labels = batch["codec_0_labels"]
        codec_mask = batch["codec_mask"]

        with torch.no_grad():
            speaker_embedding = core.speaker_encoder(ref_mels.to(dtype=core.dtype)).detach()
        input_text_ids = input_ids[:, :, 0]
        input_codec_ids = input_ids[:, :, 1]
        input_text_embedding = core.talker.model.text_embedding(input_text_ids) * text_embedding_mask
        input_codec_embedding = core.talker.model.codec_embedding(input_codec_ids) * codec_embedding_mask
        input_codec_embedding[:, 7, :] = speaker_embedding
        input_embeddings = input_text_embedding + input_codec_embedding
        for index in range(1, 16):
            codec_embedding = core.talker.code_predictor.get_input_embeddings()[index - 1](codec_ids[:, :, index])
            input_embeddings = input_embeddings + codec_embedding * codec_mask.unsqueeze(-1)

        outputs = core.talker(
            inputs_embeds=input_embeddings[:, :-1, :],
            attention_mask=attention_mask[:, :-1],
            labels=codec_0_labels[:, 1:],
            output_hidden_states=True,
        )
        hidden_states = outputs.hidden_states[0][-1]
        talker_hidden_states = hidden_states[codec_mask[:, :-1]]
        talker_codec_ids = codec_ids[codec_mask]
        _, subtalker_loss = core.talker.forward_sub_talker_finetune(talker_codec_ids, talker_hidden_states)
        total_loss = outputs.loss + self.subtalker_weight * subtalker_loss
        return total_loss, outputs.loss.detach(), subtalker_loss.detach()


def latest_resume_checkpoint(resume_root: Path) -> Path | None:
    checkpoints = sorted(
        (path for path in resume_root.glob("step-*") if (path / "trainer_state.json").is_file()),
        key=lambda path: int(path.name.split("-")[-1]),
    )
    return checkpoints[-1] if checkpoints else None


def save_resume_checkpoint(
    accelerator: Accelerator,
    model: nn.Module,
    optimizer: Any,
    resume_root: Path,
    micro_step: int,
    optimizer_step: int,
) -> None:
    accelerator.wait_for_everyone()
    if not accelerator.is_main_process:
        return
    destination = resume_root / f"step-{optimizer_step:06d}"
    destination.mkdir(parents=True, exist_ok=True)
    unwrapped = accelerator.unwrap_model(model)
    unwrapped.adapter_model.save_pretrained(destination / "adapter", safe_serialization=True)
    torch.save(optimizer.state_dict(), destination / "optimizer.pt")
    if accelerator.scaler is not None:
        torch.save(accelerator.scaler.state_dict(), destination / "scaler.pt")
    write_json_atomic(
        destination / "trainer_state.json",
        {"epoch": 0, "micro_step": micro_step, "optimizer_step": optimizer_step, "saved_at": utc_now()},
    )


def append_metric(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


@torch.no_grad()
def evaluate(model: nn.Module, dataloader: DataLoader, accelerator: Accelerator) -> dict[str, float]:
    model.eval()
    losses: list[float] = []
    primary: list[float] = []
    subtalker: list[float] = []
    for batch in dataloader:
        total, first_loss, sub_loss = model(batch)
        losses.append(float(accelerator.gather(total.detach()).mean().cpu()))
        primary.append(float(accelerator.gather(first_loss).mean().cpu()))
        subtalker.append(float(accelerator.gather(sub_loss).mean().cpu()))
    model.train()
    return {
        "eval_loss": sum(losses) / len(losses),
        "eval_first_code_loss": sum(primary) / len(primary),
        "eval_subtalker_loss": sum(subtalker) / len(subtalker),
    }


def copy_non_weight_assets(source: Path, destination: Path) -> None:
    weight_suffixes = (".safetensors", ".bin", ".pt", ".pth")
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not path.name.endswith(weight_suffixes) and path.name != "model.safetensors.index.json":
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def save_final_checkpoint(
    accelerator: Accelerator,
    model: nn.Module,
    base_snapshot: Path,
    output_root: Path,
    model_spec: dict[str, Any],
) -> Path:
    accelerator.wait_for_everyone()
    final_path = output_root / "checkpoint-epoch-1"
    if not accelerator.is_main_process:
        return final_path
    if final_path.exists():
        raise FileExistsError(f"Refusing to overwrite incomplete final checkpoint: {final_path}")
    temporary = output_root / ".checkpoint-epoch-1.incomplete"
    if temporary.exists():
        raise FileExistsError(f"Preserved incomplete checkpoint requires inspection: {temporary}")
    temporary.mkdir(parents=True)
    copy_non_weight_assets(base_snapshot, temporary)

    unwrapped = accelerator.unwrap_model(model)
    unwrapped.adapter_model.to("cpu")
    merged = unwrapped.adapter_model.merge_and_unload(safe_merge=True)
    if getattr(merged.config, "tts_model_type", None) != "base":
        raise RuntimeError("Merged checkpoint unexpectedly lost Base model type")
    merged.save_pretrained(temporary, safe_serialization=True, max_shard_size="2GB")
    with (temporary / "config.json").open("r", encoding="utf-8") as handle:
        saved_config = json.load(handle)
    if saved_config.get("tts_model_type") != "base":
        raise RuntimeError("Saved checkpoint is not a Base voice-cloning model")
    if saved_config.get("tts_model_size") != model_spec["expected_tts_model_size"]:
        raise RuntimeError("Saved checkpoint model size mismatch")
    temporary.replace(final_path)
    return final_path


def train(model_key: str) -> dict[str, Any]:
    plan = load_plan()
    training = plan["training"]
    model_spec = plan["models"][model_key]
    output_root = project_path(model_spec["output_dir"])
    output_root.mkdir(parents=True, exist_ok=True)
    success_path = output_root / "SUCCESS.json"
    if success_path.is_file():
        with success_path.open("r", encoding="utf-8") as handle:
            result = json.load(handle)
        print(f"Already complete: {success_path}")
        return result

    seed = int(plan["dataset"]["seed"]) + (0 if model_key == "0.6b" else 1)
    seed_everything(seed)
    accelerator = Accelerator(
        gradient_accumulation_steps=int(training["gradient_accumulation_steps"]),
        mixed_precision="fp16",
    )
    metrics_path = output_root / "training_metrics.jsonl"
    resume_root = output_root / "resume"
    resume_checkpoint = latest_resume_checkpoint(resume_root)

    wrapper = Qwen3TTSModel.from_pretrained(
        model_spec["repo_id"],
        cache_dir=project_path("model_cache"),
        torch_dtype=torch.float16,
        attn_implementation="sdpa",
        low_cpu_mem_usage=True,
    )
    core = wrapper.model
    if core.config.tts_model_type != "base":
        raise RuntimeError("Selective adaptation requires an untouched Base checkpoint")
    freeze_base(core)
    if training["gradient_checkpointing"]:
        core.gradient_checkpointing_enable()
        core.enable_input_require_grads()
        core.config.use_cache = False

    lora = LoraConfig(
        r=int(model_spec["lora_rank"]),
        lora_alpha=int(model_spec["lora_alpha"]),
        lora_dropout=float(model_spec["lora_dropout"]),
        bias="none",
        target_modules=training["target_module_pattern"],
    )
    if resume_checkpoint is None:
        adapter_model = get_peft_model(core, lora)
        resume_state = {"micro_step": -1, "optimizer_step": 0}
    else:
        adapter_model = PeftModel.from_pretrained(core, resume_checkpoint / "adapter", is_trainable=True)
        with (resume_checkpoint / "trainer_state.json").open("r", encoding="utf-8") as handle:
            resume_state = json.load(handle)
    trainable_summary = validate_trainable_parameters(adapter_model)
    model = SelectiveTrainingModel(adapter_model, float(training["subtalker_loss_weight"]))

    manifest_dir = project_path("training_data/golos_balalaika/manifests")
    train_rows = list(iter_jsonl(manifest_dir / "train_with_codes.jsonl"))
    eval_rows = list(iter_jsonl(manifest_dir / "eval_with_codes.jsonl"))
    if {row["id"] for row in train_rows} & {row["id"] for row in eval_rows}:
        raise RuntimeError("Train/eval manifest overlap")
    train_dataset = RussianAdaptationDataset(train_rows, wrapper.processor, core.config)
    eval_dataset = RussianAdaptationDataset(eval_rows, wrapper.processor, core.config)
    generator = torch.Generator().manual_seed(seed)
    train_loader = DataLoader(
        train_dataset,
        batch_size=int(training["per_device_batch_size"]),
        shuffle=True,
        generator=generator,
        num_workers=int(training["dataloader_workers"]),
        collate_fn=train_dataset.collate_fn,
    )
    eval_loader = DataLoader(
        eval_dataset,
        batch_size=1,
        shuffle=False,
        num_workers=0,
        collate_fn=eval_dataset.collate_fn,
    )
    trainable_parameters = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer = AdamW(
        trainable_parameters,
        lr=float(model_spec["learning_rate"]),
        weight_decay=float(training["weight_decay"]),
    )
    total_optimizer_steps = math.ceil(len(train_loader) / int(training["gradient_accumulation_steps"]))
    warmup_steps = max(1, round(total_optimizer_steps * float(training["warmup_ratio"])))

    def lr_lambda(step: int) -> float:
        if step < warmup_steps:
            return (step + 1) / warmup_steps
        remaining = max(total_optimizer_steps - step, 0)
        return remaining / max(total_optimizer_steps - warmup_steps, 1)

    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)
    if resume_checkpoint is not None:
        optimizer.load_state_dict(torch.load(resume_checkpoint / "optimizer.pt", map_location="cpu", weights_only=True))
        for _ in range(int(resume_state["optimizer_step"])):
            scheduler.step()

    model, optimizer, train_loader, eval_loader = accelerator.prepare(model, optimizer, train_loader, eval_loader)
    if resume_checkpoint is not None and accelerator.scaler is not None and (resume_checkpoint / "scaler.pt").is_file():
        accelerator.scaler.load_state_dict(torch.load(resume_checkpoint / "scaler.pt", map_location="cpu", weights_only=True))

    model.train()
    optimizer_step = int(resume_state["optimizer_step"])
    start_micro_step = int(resume_state["micro_step"])
    for micro_step, batch in enumerate(train_loader):
        if micro_step <= start_micro_step:
            continue
        with accelerator.accumulate(model):
            loss, first_loss, subtalker_loss = model(batch)
            accelerator.backward(loss)
            if accelerator.sync_gradients:
                accelerator.clip_grad_norm_(trainable_parameters, float(training["max_grad_norm"]))
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            if accelerator.sync_gradients:
                scheduler.step()
                optimizer_step += 1
                metric = {
                    "at": utc_now(),
                    "epoch": 0,
                    "micro_step": micro_step,
                    "optimizer_step": optimizer_step,
                    "loss": float(loss.detach().cpu()),
                    "first_code_loss": float(first_loss.cpu()),
                    "subtalker_loss": float(subtalker_loss.cpu()),
                    "learning_rate": scheduler.get_last_lr()[0],
                }
                if accelerator.is_main_process:
                    append_metric(metrics_path, metric)
                accelerator.print(json.dumps(metric, ensure_ascii=False))
                if optimizer_step % int(training["checkpoint_optimizer_steps"]) == 0:
                    save_resume_checkpoint(accelerator, model, optimizer, resume_root, micro_step, optimizer_step)

    evaluation = evaluate(model, eval_loader, accelerator)
    if accelerator.is_main_process:
        append_metric(metrics_path, {"at": utc_now(), "epoch": 0, **evaluation})
    final_adapter = output_root / "adapter-epoch-1"
    accelerator.wait_for_everyone()
    if accelerator.is_main_process:
        accelerator.unwrap_model(model).adapter_model.save_pretrained(final_adapter, safe_serialization=True)
    base_snapshot = Path(
        snapshot_download(repo_id=model_spec["repo_id"], cache_dir=project_path("model_cache"), local_files_only=True)
    )
    final_checkpoint = save_final_checkpoint(accelerator, model, base_snapshot, output_root, model_spec)
    result = {
        "status": "success",
        "completed_at": utc_now(),
        "model": model_key,
        "base_model": model_spec["repo_id"],
        "base_model_type_preserved": "base",
        "epochs": 1,
        "train_samples": len(train_rows),
        "eval_samples": len(eval_rows),
        "adapter": final_adapter.relative_to(PROJECT_ROOT).as_posix(),
        "checkpoint": final_checkpoint.relative_to(PROJECT_ROOT).as_posix(),
        "metrics": evaluation,
        **trainable_summary,
    }
    accelerator.wait_for_everyone()
    if accelerator.is_main_process:
        write_json_atomic(success_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run conservative one-epoch Qwen3-TTS Base LoRA adaptation")
    parser.add_argument("--model-key", choices=("0.6b", "1.7b"), required=True)
    args = parser.parse_args()
    plan = load_plan()
    output_root = project_path(plan["models"][args.model_key]["output_dir"])
    try:
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is required for the user-run training step")
        result = train(args.model_key)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except BaseException as exc:
        output_root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(
            output_root / "FAILED.json",
            {
                "status": "failed",
                "failed_at": utc_now(),
                "model": args.model_key,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        raise
    finally:
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


if __name__ == "__main__":
    raise SystemExit(main())
