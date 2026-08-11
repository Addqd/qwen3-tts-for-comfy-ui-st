from __future__ import annotations

import argparse
import gc
import json
import math
import os
import random
import re
import shutil
import traceback
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from accelerate import Accelerator
from huggingface_hub import snapshot_download
from peft import LoraConfig, PeftModel, get_peft_model
from torch.optim import AdamW
from torch.utils.data import DataLoader

from qwen_tts.inference.qwen3_tts_model import Qwen3TTSModel
from training.russian_adaptation.common import PROJECT_ROOT, iter_jsonl, load_plan, project_path, write_json_atomic
from training.russian_adaptation.dataset import RussianAdaptationDataset


TRAINING_SEMANTICS_VERSION = "qwen3-tts-selective-lora-v2-aligned-loss"
ROOT_WEIGHT_SUFFIXES = (".safetensors", ".bin", ".pt", ".pth")
ROOT_MODEL_WEIGHT_PATTERN = re.compile(
    r"^(?:model(?:-\d+-of-\d+)?\.safetensors|model\.safetensors\.index\.json|"
    r"pytorch_model(?:-\d+-of-\d+)?\.bin|pytorch_model\.bin\.index\.json)$"
)


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


def enable_talker_gradient_checkpointing(core: nn.Module) -> None:
    core.gradient_checkpointing_enable()
    core.talker.enable_input_require_grads()
    core.config.use_cache = False


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


def ensure_finite_tensor(name: str, value: torch.Tensor) -> None:
    if not bool(torch.isfinite(value.detach()).all()):
        raise RuntimeError(f"Non-finite {name} detected; refusing to continue before backward/optimizer step")


def configure_subtalker_precision(talker: nn.Module) -> None:
    if any(parameter.requires_grad for parameter in talker.code_predictor.parameters()):
        raise RuntimeError("Code predictor must remain frozen during selective LoRA training")
    talker.code_predictor.to(dtype=torch.float32)


def aligned_subtalker_cross_entropy(logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
    if logits.ndim != 3 or targets.ndim != 2 or logits.shape[:2] != targets.shape:
        raise RuntimeError(
            f"Subtalker logits/target shape mismatch: logits={tuple(logits.shape)}, targets={tuple(targets.shape)}"
        )
    if targets.shape[1] != 15:
        raise RuntimeError(f"Expected all 15 subtalker codebook targets, got {targets.shape[1]}")
    ensure_finite_tensor("subtalker logits", logits)
    loss = F.cross_entropy(logits.float().reshape(-1, logits.shape[-1]), targets.reshape(-1))
    ensure_finite_tensor("subtalker loss", loss)
    return loss


def forward_aligned_subtalker(
    talker: nn.Module, codec_ids: torch.Tensor, talker_hidden_states: torch.Tensor
) -> tuple[torch.Tensor, torch.Tensor]:
    predictor = talker.code_predictor
    predictor_dtype = next(predictor.parameters()).dtype
    with torch.autocast(device_type=talker_hidden_states.device.type, enabled=False):
        inputs = [talker_hidden_states.to(dtype=predictor_dtype).unsqueeze(1)]
        for index in range(15):
            if index == 0:
                embedding = talker.get_input_embeddings()(codec_ids[:, :1])
            else:
                embedding = predictor.get_input_embeddings()[index - 1](codec_ids[:, index : index + 1])
            inputs.append(embedding.to(dtype=predictor_dtype))
        outputs = predictor.forward_finetune(inputs_embeds=torch.cat(inputs, dim=1), labels=None)
        logits = outputs.logits
        loss = aligned_subtalker_cross_entropy(logits, codec_ids[:, 1:])
    return logits, loss


def align_subtalker_timesteps(
    hidden_states: torch.Tensor, codec_mask: torch.Tensor, codec_ids: torch.Tensor
) -> tuple[torch.Tensor, torch.Tensor]:
    talker_hidden_states = hidden_states[:, :-1, :][codec_mask[:, 1:]]
    talker_codec_ids = codec_ids[codec_mask]
    if talker_hidden_states.shape[0] != talker_codec_ids.shape[0]:
        raise RuntimeError("Main-talker hidden states are not aligned with codec timesteps")
    return talker_hidden_states, talker_codec_ids


def build_initial_talker_embeddings(
    talker: nn.Module,
    input_text_ids: torch.Tensor,
    input_codec_ids: torch.Tensor,
    text_embedding_mask: torch.Tensor,
    codec_embedding_mask: torch.Tensor,
    speaker_embedding: torch.Tensor,
) -> torch.Tensor:
    input_text_embedding = talker.text_projection(talker.model.text_embedding(input_text_ids)) * text_embedding_mask
    input_codec_embedding = talker.model.codec_embedding(input_codec_ids) * codec_embedding_mask
    input_codec_embedding[:, 7, :] = speaker_embedding
    return input_text_embedding + input_codec_embedding


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
        input_embeddings = build_initial_talker_embeddings(
            core.talker,
            input_text_ids,
            input_codec_ids,
            text_embedding_mask,
            codec_embedding_mask,
            speaker_embedding,
        )
        for index in range(1, 16):
            codec_embedding = core.talker.code_predictor.get_input_embeddings()[index - 1](codec_ids[:, :, index])
            input_embeddings = input_embeddings + codec_embedding.to(input_embeddings.dtype) * codec_mask.unsqueeze(-1)

        outputs = core.talker(
            inputs_embeds=input_embeddings,
            attention_mask=attention_mask,
            labels=codec_0_labels,
            output_hidden_states=True,
        )
        ensure_finite_tensor("main talker loss", outputs.loss)
        hidden_states = outputs.hidden_states[0][-1]
        talker_hidden_states, talker_codec_ids = align_subtalker_timesteps(hidden_states, codec_mask, codec_ids)
        _, subtalker_loss = forward_aligned_subtalker(core.talker, talker_codec_ids, talker_hidden_states)
        total_loss = outputs.loss + self.subtalker_weight * subtalker_loss
        ensure_finite_tensor("total loss", total_loss)
        return total_loss, outputs.loss.detach(), subtalker_loss.detach()


def validate_resume_state(state: dict[str, Any], checkpoint: Path) -> None:
    version = state.get("training_semantics_version")
    if version != TRAINING_SEMANTICS_VERSION:
        found = "missing" if version is None else repr(version)
        raise RuntimeError(
            f"Cannot resume {checkpoint}: training semantics version is {found}; "
            f"expected {TRAINING_SEMANTICS_VERSION!r}. Start a fresh run and preserve the old checkpoint for inspection."
        )
    metrics = state.get("last_metrics") or {}
    for name in ("loss", "first_code_loss", "subtalker_loss"):
        if name in metrics and not math.isfinite(float(metrics[name])):
            raise RuntimeError(f"Cannot resume {checkpoint}: saved {name} is non-finite")


def ensure_finite_state_tensors(value: Any, context: str) -> None:
    if torch.is_tensor(value):
        if (value.is_floating_point() or value.is_complex()) and not bool(torch.isfinite(value).all()):
            raise RuntimeError(f"Cannot resume: {context} contains non-finite tensors")
        return
    if isinstance(value, dict):
        for nested in value.values():
            ensure_finite_state_tensors(nested, context)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            ensure_finite_state_tensors(nested, context)


def validate_train_eval_disjoint(train_rows: list[dict[str, Any]], eval_rows: list[dict[str, Any]]) -> None:
    def normalized_text(row: dict[str, Any]) -> str:
        text = unicodedata.normalize("NFKC", str(row.get("text") or "")).casefold()
        return re.sub(r"\s+", " ", text).strip()

    for label, value_of in (
        ("source_key", lambda row: str(row.get("source_key") or "").strip()),
        ("normalized text", normalized_text),
    ):
        train_values: dict[str, str] = {}
        for row in train_rows:
            value = value_of(row)
            if value:
                train_values.setdefault(value, str(row.get("id") or "<unknown>"))
        for row in eval_rows:
            value = value_of(row)
            if value and value in train_values:
                preview = value if len(value) <= 80 else value[:77] + "..."
                raise RuntimeError(
                    f"Train/eval overlap by {label}: train={train_values[value]}, "
                    f"eval={row.get('id') or '<unknown>'}, value={preview!r}"
                )


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
    last_metrics: dict[str, Any],
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
        {
            "training_semantics_version": TRAINING_SEMANTICS_VERSION,
            "epoch": 0,
            "micro_step": micro_step,
            "optimizer_step": optimizer_step,
            "last_metrics": last_metrics,
            "saved_at": utc_now(),
        },
    )


def append_metric(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


@torch.no_grad()
def evaluate(model: nn.Module, dataloader: DataLoader, accelerator: Accelerator) -> dict[str, float]:
    if len(dataloader) == 0:
        raise RuntimeError("Evaluation dataloader is empty; refusing to start or finish training without evaluation")
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
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not (relative.parent == Path(".") and ROOT_MODEL_WEIGHT_PATTERN.fullmatch(path.name)):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def validate_final_checkpoint_structure(base_snapshot: Path, checkpoint: Path) -> None:
    root_weights = [
        path for path in checkpoint.iterdir() if path.is_file() and ROOT_MODEL_WEIGHT_PATTERN.fullmatch(path.name)
    ]
    if not root_weights:
        raise RuntimeError("Final checkpoint has no root model weights")
    source_speech_tokenizer = base_snapshot / "speech_tokenizer"
    if not source_speech_tokenizer.is_dir():
        raise RuntimeError("Pinned Base snapshot is missing the speech_tokenizer subtree")
    required_files = [path.relative_to(base_snapshot) for path in source_speech_tokenizer.rglob("*") if path.is_file()]
    if not required_files:
        raise RuntimeError("Pinned Base snapshot has an empty speech_tokenizer subtree")
    missing = [relative.as_posix() for relative in required_files if not (checkpoint / relative).is_file()]
    if missing:
        raise RuntimeError(f"Final checkpoint is missing required speech_tokenizer assets: {missing[:5]}")
    if not any(relative.name.endswith(ROOT_WEIGHT_SUFFIXES) for relative in required_files):
        raise RuntimeError("Pinned Base snapshot speech_tokenizer has no model weights")


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
    unwrapped.adapter_model.get_base_model().talker.code_predictor.to(dtype=torch.float16)
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
    validate_final_checkpoint_structure(base_snapshot, temporary)
    temporary.replace(final_path)
    return final_path


def validate_cuda_training_state(accelerator: Accelerator, model: nn.Module, batch: dict[str, torch.Tensor]) -> None:
    if accelerator.device.type != "cuda":
        raise RuntimeError(f"CUDA training is required, but Accelerator selected {accelerator.device}")
    trainable = next(((name, parameter) for name, parameter in model.named_parameters() if parameter.requires_grad), None)
    if trainable is None or "lora_" not in trainable[0]:
        raise RuntimeError("No intended trainable LoRA parameter is available for the CUDA guard")
    trainable_name, trainable_parameter = trainable
    if trainable_parameter.device.type != "cuda":
        raise RuntimeError(f"Trainable LoRA parameter is not on CUDA: {trainable_name} -> {trainable_parameter.device}")
    non_cuda_batch = {name: str(value.device) for name, value in batch.items() if torch.is_tensor(value) and value.device.type != "cuda"}
    if non_cuda_batch:
        raise RuntimeError(f"Prepared training batch tensors are not on CUDA: {non_cuda_batch}")
    device_index = accelerator.device.index if accelerator.device.index is not None else torch.cuda.current_device()
    accelerator.print(
        json.dumps(
            {
                "accelerator_device": str(accelerator.device),
                "cuda_device_name": torch.cuda.get_device_name(device_index),
                "lora_parameter": trainable_name,
                "lora_parameter_device": str(trainable_parameter.device),
                "batch_tensor_device": str(next(value.device for value in batch.values() if torch.is_tensor(value))),
                "cuda_memory_allocated": torch.cuda.memory_allocated(device_index),
                "cuda_memory_reserved": torch.cuda.memory_reserved(device_index),
            },
            ensure_ascii=False,
        )
    )


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

    base_snapshot = Path(
        snapshot_download(
            repo_id=model_spec["repo_id"],
            revision=model_spec["revision"],
            cache_dir=project_path("model_cache"),
        )
    )
    wrapper = Qwen3TTSModel.from_pretrained(
        str(base_snapshot),
        revision=model_spec["revision"],
        cache_dir=project_path("model_cache"),
        torch_dtype=torch.float16,
        attn_implementation="sdpa",
        low_cpu_mem_usage=True,
    )
    core = wrapper.model
    if core.config.tts_model_type != "base":
        raise RuntimeError("Selective adaptation requires an untouched Base checkpoint")
    freeze_base(core)
    configure_subtalker_precision(core.talker)

    lora = LoraConfig(
        r=int(model_spec["lora_rank"]),
        lora_alpha=int(model_spec["lora_alpha"]),
        lora_dropout=float(model_spec["lora_dropout"]),
        bias="none",
        target_modules=training["target_module_pattern"],
    )
    if resume_checkpoint is None:
        adapter_model = get_peft_model(core, lora)
        resume_state = {
            "training_semantics_version": TRAINING_SEMANTICS_VERSION,
            "micro_step": -1,
            "optimizer_step": 0,
        }
    else:
        with (resume_checkpoint / "trainer_state.json").open("r", encoding="utf-8") as handle:
            resume_state = json.load(handle)
        validate_resume_state(resume_state, resume_checkpoint)
        adapter_model = PeftModel.from_pretrained(core, resume_checkpoint / "adapter", is_trainable=True)
        ensure_finite_state_tensors(
            [parameter for parameter in adapter_model.parameters() if parameter.requires_grad],
            "LoRA checkpoint",
        )
    if training["gradient_checkpointing"]:
        enable_talker_gradient_checkpointing(core)
    trainable_summary = validate_trainable_parameters(adapter_model)
    model = SelectiveTrainingModel(adapter_model, float(training["subtalker_loss_weight"]))

    manifest_dir = project_path("training_data/golos_balalaika/manifests")
    train_rows = list(iter_jsonl(manifest_dir / "train_with_codes.jsonl"))
    eval_rows = list(iter_jsonl(manifest_dir / "eval_with_codes.jsonl"))
    if not eval_rows:
        raise RuntimeError("Evaluation manifest is empty; refusing to start training")
    validate_train_eval_disjoint(train_rows, eval_rows)
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
        optimizer_state = torch.load(resume_checkpoint / "optimizer.pt", map_location="cpu", weights_only=True)
        ensure_finite_state_tensors(optimizer_state, "optimizer checkpoint")
        optimizer.load_state_dict(optimizer_state)
        for _ in range(int(resume_state["optimizer_step"])):
            scheduler.step()

    model, optimizer, train_loader, eval_loader = accelerator.prepare(model, optimizer, train_loader, eval_loader)
    if resume_checkpoint is not None and accelerator.scaler is not None and (resume_checkpoint / "scaler.pt").is_file():
        accelerator.scaler.load_state_dict(torch.load(resume_checkpoint / "scaler.pt", map_location="cpu", weights_only=True))

    model.train()
    optimizer_step = int(resume_state["optimizer_step"])
    start_micro_step = int(resume_state["micro_step"])
    cuda_guard_complete = False
    for micro_step, batch in enumerate(train_loader):
        if micro_step <= start_micro_step:
            continue
        if not cuda_guard_complete:
            validate_cuda_training_state(accelerator, model, batch)
            cuda_guard_complete = True
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
                    save_resume_checkpoint(
                        accelerator,
                        model,
                        optimizer,
                        resume_root,
                        micro_step,
                        optimizer_step,
                        metric,
                    )

    evaluation = evaluate(model, eval_loader, accelerator)
    if accelerator.is_main_process:
        append_metric(metrics_path, {"at": utc_now(), "epoch": 0, **evaluation})
    final_adapter = output_root / "adapter-epoch-1"
    accelerator.wait_for_everyone()
    if accelerator.is_main_process:
        accelerator.unwrap_model(model).adapter_model.save_pretrained(final_adapter, safe_serialization=True)
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
