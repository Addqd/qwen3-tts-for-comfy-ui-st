from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from torch import nn
from torch.utils.data import DataLoader

pytest.importorskip("peft")

import training.russian_adaptation.train_lora as train_lora_module
from training.russian_adaptation.common import load_plan, project_path
from training.russian_adaptation.train_lora import (
    TRAINING_SEMANTICS_VERSION,
    SelectiveTrainingModel,
    advance_optimizer_step_if_applied,
    aligned_subtalker_cross_entropy,
    configure_subtalker_precision,
    copy_non_weight_assets,
    ensure_finite_tensor,
    evaluate,
    dispatch_training_command,
    forward_aligned_subtalker,
    prepare_finalization_directory,
    save_merged_model_files,
    validate_cuda_training_state,
    validate_final_checkpoint_structure,
    validate_resume_state,
    validate_train_eval_disjoint,
)


class _FakeCodePredictor(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.anchor = nn.Parameter(torch.ones(()), requires_grad=False)
        self.embeddings = nn.ModuleList([nn.Embedding(8, 4) for _ in range(15)])
        for embedding in self.embeddings:
            nn.init.zeros_(embedding.weight)
            embedding.weight.requires_grad = False
        self.last_inputs: torch.Tensor | None = None

    def get_input_embeddings(self) -> nn.ModuleList:
        return self.embeddings

    def forward_finetune(self, *, inputs_embeds: torch.Tensor, labels=None) -> SimpleNamespace:
        assert labels is None
        self.last_inputs = inputs_embeds.detach().clone()
        signal = inputs_embeds[:, 1:, :1] + inputs_embeds[:, :1, :1]
        classes = torch.arange(8, dtype=signal.dtype, device=signal.device).view(1, 1, -1)
        return SimpleNamespace(logits=signal * classes * self.anchor)


class _FakeTalker(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.model = nn.Module()
        self.model.text_embedding = nn.Embedding(8, 4)
        self.model.codec_embedding = nn.Embedding(8, 4)
        nn.init.zeros_(self.model.text_embedding.weight)
        nn.init.zeros_(self.model.codec_embedding.weight)
        self.text_projection = nn.Identity()
        self.code_predictor = _FakeCodePredictor()
        self.config = SimpleNamespace(hidden_size=4, num_code_groups=16)
        self.last_forward: dict[str, torch.Tensor] = {}

    def get_input_embeddings(self) -> nn.Embedding:
        return self.model.codec_embedding

    def forward(self, **kwargs) -> SimpleNamespace:
        self.last_forward = kwargs
        embeddings = kwargs["inputs_embeds"]
        positions = torch.arange(embeddings.shape[1], dtype=embeddings.dtype).view(1, -1, 1)
        hidden = embeddings + positions
        loss = embeddings.sum() * 0 + 1.0
        return SimpleNamespace(loss=loss, hidden_states=((hidden,),))


class _FakeCore(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.talker = _FakeTalker()
        self.speaker_encoder = nn.Identity()
        self.dtype = torch.float32


class _FakeAdapter(nn.Module):
    def __init__(self, core: nn.Module) -> None:
        super().__init__()
        self.core = core

    def get_base_model(self) -> nn.Module:
        return self.core


def _synthetic_batch() -> dict[str, torch.Tensor]:
    codec_ids = torch.zeros((1, 12, 16), dtype=torch.long)
    codec_ids[0, 9, 1:] = torch.arange(15) % 8
    codec_ids[0, 10, 1:] = (torch.arange(15) + 1) % 8
    codec_mask = torch.zeros((1, 12), dtype=torch.bool)
    codec_mask[:, 9:11] = True
    labels = torch.full((1, 12), -100, dtype=torch.long)
    labels[:, 9:11] = codec_ids[:, 9:11, 0]
    return {
        "input_ids": torch.zeros((1, 12, 2), dtype=torch.long),
        "codec_ids": codec_ids,
        "ref_mels": torch.zeros((1, 4)),
        "text_embedding_mask": torch.ones((1, 12, 1)),
        "codec_embedding_mask": torch.ones((1, 12, 1)),
        "attention_mask": torch.ones((1, 12), dtype=torch.long),
        "codec_0_labels": labels,
        "codec_mask": codec_mask,
    }


def test_main_talker_uses_full_unshifted_labels_and_one_causal_alignment() -> None:
    core = _FakeCore()
    model = SelectiveTrainingModel(_FakeAdapter(core), subtalker_weight=0.3)
    batch = _synthetic_batch()

    model(batch)

    assert core.talker.last_forward["inputs_embeds"].shape[1] == 12
    assert core.talker.last_forward["attention_mask"].shape[1] == 12
    assert torch.equal(core.talker.last_forward["labels"], batch["codec_0_labels"])
    assert core.talker.code_predictor.last_inputs is not None
    assert core.talker.code_predictor.last_inputs[:, 0, 0].tolist() == [8.0, 9.0]


def test_all_15_subtalker_targets_are_aligned_and_receive_gradient() -> None:
    logits = torch.zeros((2, 15, 20), requires_grad=True)
    targets = torch.arange(15).repeat(2, 1)
    loss = aligned_subtalker_cross_entropy(logits, targets)
    loss.backward()
    assert torch.all(logits.grad.abs().sum(dim=-1) > 0)

    talker = _FakeTalker()
    for parameter in talker.code_predictor.parameters():
        parameter.requires_grad = False
    configure_subtalker_precision(talker)
    hidden = torch.randn((2, 4), requires_grad=True)
    codec_ids = torch.arange(16).remainder(8).repeat(2, 1)
    _, subtalker_loss = forward_aligned_subtalker(talker, codec_ids, hidden)
    subtalker_loss.backward()
    assert hidden.grad is not None and bool(torch.isfinite(hidden.grad).all())
    assert hidden.grad.abs().sum() > 0
    assert not any(parameter.requires_grad for parameter in talker.code_predictor.parameters())
    assert all(parameter.dtype == torch.float32 for parameter in talker.code_predictor.parameters())


def test_nonfinite_losses_fail_before_backward() -> None:
    with pytest.raises(RuntimeError, match="Non-finite main talker loss"):
        ensure_finite_tensor("main talker loss", torch.tensor(float("nan")))
    with pytest.raises(RuntimeError, match="Non-finite subtalker logits"):
        aligned_subtalker_cross_entropy(
            torch.full((1, 15, 4), float("inf")),
            torch.zeros((1, 15), dtype=torch.long),
        )


def test_resume_semantics_reject_old_mismatched_and_nonfinite_states(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="version is missing"):
        validate_resume_state({}, tmp_path)
    with pytest.raises(RuntimeError, match="expected"):
        validate_resume_state({"training_semantics_version": "old"}, tmp_path)
    with pytest.raises(RuntimeError, match="saved loss is non-finite"):
        validate_resume_state(
            {"training_semantics_version": TRAINING_SEMANTICS_VERSION, "last_metrics": {"loss": float("nan")}},
            tmp_path,
        )
    validate_resume_state(
        {"training_semantics_version": TRAINING_SEMANTICS_VERSION, "last_metrics": {"loss": 1.0}},
        tmp_path,
    )


def test_checkpoint_assets_keep_nested_speech_weights_but_not_root_weights(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output_root = tmp_path / "output"
    output_root.mkdir()
    old_incomplete = output_root / ".checkpoint-epoch-1.incomplete"
    old_incomplete.mkdir()
    (old_incomplete / "diagnostic.txt").write_text("preserve", encoding="utf-8")
    destination, final_path = prepare_finalization_directory(output_root, recovery=True)
    (source / "speech_tokenizer").mkdir(parents=True)
    (source / "model.safetensors").write_bytes(b"old-root")
    (source / "model-00001-of-00002.safetensors").write_bytes(b"old-shard")
    (source / "model.safetensors.index.json").write_text("{}", encoding="utf-8")
    (source / "required_asset.pt").write_bytes(b"asset")
    (source / "config.json").write_text('{"tts_model_type":"base"}', encoding="utf-8")
    (source / "generation_config.json").write_text("{}", encoding="utf-8")
    (source / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (source / "preprocessor_config.json").write_text("{}", encoding="utf-8")
    (source / "speech_tokenizer" / "model.safetensors").write_bytes(b"speech")
    (source / "speech_tokenizer" / "config.json").write_text("{}", encoding="utf-8")

    copy_non_weight_assets(source, destination)
    assert not (destination / "model.safetensors").exists()
    assert not (destination / "model-00001-of-00002.safetensors").exists()
    assert (destination / "required_asset.pt").read_bytes() == b"asset"
    assert (destination / "speech_tokenizer" / "model.safetensors").read_bytes() == b"speech"
    assert (destination / "speech_tokenizer" / "config.json").is_file()

    class Config:
        def to_dict(self):
            return {"model_type": "qwen3_tts", "tts_model_type": "base", "tts_model_size": "0b6"}

    model = nn.Linear(2, 2)
    model.config = Config()
    save_merged_model_files(model, source / "config.json", destination, max_shard_size="1KB")
    validate_final_checkpoint_structure(source, destination)
    destination.replace(final_path)
    assert (final_path / "model.safetensors").is_file()
    assert (final_path / "generation_config.json").is_file()
    assert (final_path / "tokenizer_config.json").is_file()
    assert (final_path / "preprocessor_config.json").is_file()
    archived = list(output_root.glob(".checkpoint-epoch-1.failed-*"))
    assert len(archived) == 1
    assert (archived[0] / "diagnostic.txt").read_text(encoding="utf-8") == "preserve"


@pytest.mark.parametrize("model_key", ("0.6b", "1.7b"))
def test_full_qwen_config_serialization_round_trips_composite_dtype(tmp_path: Path, model_key: str) -> None:
    from qwen_tts.core.models.configuration_qwen3_tts import Qwen3TTSConfig

    plan = load_plan()
    spec = plan["models"][model_key]
    cache_name = f"models--Qwen--Qwen3-TTS-12Hz-{model_key.upper()}-Base"
    source = project_path("model_cache") / cache_name / "snapshots" / spec["revision"] / "config.json"
    config = Qwen3TTSConfig.from_json_file(source)
    config.dtype = torch.float16

    model = nn.Linear(2, 2, dtype=torch.float16)
    model.config = config
    destination = tmp_path / model_key
    destination.mkdir()
    save_merged_model_files(model, source, destination, max_shard_size="1KB")

    restored = Qwen3TTSConfig.from_json_file(destination / "config.json")
    assert restored.tts_model_type == "base"
    assert restored.tts_model_size == spec["expected_tts_model_size"]
    assert restored.talker_config.hidden_size == config.talker_config.hidden_size
    assert restored.talker_config.code_predictor_config.hidden_size == config.talker_config.code_predictor_config.hidden_size
    assert restored.speaker_encoder_config.enc_dim == config.speaker_encoder_config.enc_dim
    assert restored.dtype == torch.float16


def test_finalize_only_dispatch_uses_completed_adapter_and_writes_success_last(tmp_path: Path, monkeypatch) -> None:
    output_root = tmp_path / "trained"
    adapter = output_root / "adapter-epoch-1"
    adapter.mkdir(parents=True)
    (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
    adapter_weights = adapter / "adapter_model.safetensors"
    adapter_weights.write_bytes(b"completed-adapter")
    (output_root / "training_metrics.jsonl").write_text(
        '{"eval_loss":1.0,"eval_first_code_loss":2.0,"eval_subtalker_loss":3.0}\n', encoding="utf-8"
    )
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    (manifests / "train_with_codes.jsonl").write_text("{}\n{}\n", encoding="utf-8")
    (manifests / "eval_with_codes.jsonl").write_text("{}\n", encoding="utf-8")
    (output_root / "FAILED.json").write_text(
        '{"error":"dtype","traceback":"save_final_checkpoint"}', encoding="utf-8"
    )

    spec = {
        "repo_id": "Qwen/test",
        "expected_tts_model_size": "0b6",
        "output_dir": "trained-output",
    }
    monkeypatch.setattr(train_lora_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(train_lora_module, "load_plan", lambda: {"models": {"0.6b": spec}})
    monkeypatch.setattr(
        train_lora_module,
        "project_path",
        lambda relative: output_root if str(relative) == "trained-output" else manifests,
    )
    completed_adapter = object()
    monkeypatch.setattr(
        train_lora_module,
        "load_completed_adapter",
        lambda model_spec, final_adapter: (tmp_path / "base", completed_adapter),
    )

    def fake_finalize(adapter_model, base_snapshot, destination, model_spec, recovery=False):
        assert adapter_model is completed_adapter
        assert recovery is True
        checkpoint = destination / "checkpoint-epoch-1"
        checkpoint.mkdir()
        return checkpoint

    monkeypatch.setattr(train_lora_module, "finalize_adapter_checkpoint", fake_finalize)
    monkeypatch.setattr(train_lora_module, "train", lambda model_key: pytest.fail("training loop entered"))
    result = dispatch_training_command("0.6b", finalize_only_mode=True)
    assert result["recovered_from_completed_adapter"] is True
    assert (output_root / "SUCCESS.json").is_file()
    assert not (output_root / "FAILED.json").exists()
    assert list(output_root.glob("FAILED.finalization-*.json"))
    assert adapter_weights.read_bytes() == b"completed-adapter"

    (output_root / "SUCCESS.json").unlink()
    (output_root / "checkpoint-epoch-1").rmdir()
    monkeypatch.setattr(
        train_lora_module,
        "finalize_adapter_checkpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("validation failed")),
    )
    with pytest.raises(RuntimeError, match="validation failed"):
        dispatch_training_command("0.6b", finalize_only_mode=True)
    assert not (output_root / "SUCCESS.json").exists()
    assert adapter_weights.read_bytes() == b"completed-adapter"


def test_success_state_skips_normal_training_model_load(tmp_path: Path, monkeypatch) -> None:
    output_root = tmp_path / "trained"
    output_root.mkdir()
    expected = {"status": "success", "model": "0.6b"}
    (output_root / "SUCCESS.json").write_text(json.dumps(expected), encoding="utf-8")
    monkeypatch.setattr(
        train_lora_module,
        "load_plan",
        lambda: {"training": {}, "models": {"0.6b": {"output_dir": "trained-output"}}},
    )
    monkeypatch.setattr(train_lora_module, "project_path", lambda relative: output_root)
    monkeypatch.setattr(
        train_lora_module,
        "load_pinned_base",
        lambda model_spec: pytest.fail("normal training attempted to load the Base model"),
    )
    assert train_lora_module.train("0.6b") == expected


def test_empty_eval_overlap_and_cpu_training_guards_fail_clearly() -> None:
    with pytest.raises(RuntimeError, match="Evaluation dataloader is empty"):
        evaluate(nn.Identity(), DataLoader([]), SimpleNamespace())
    with pytest.raises(RuntimeError, match="overlap by source_key"):
        validate_train_eval_disjoint(
            [{"id": "train", "source_key": "same", "text": "Один текст"}],
            [{"id": "eval", "source_key": "same", "text": "Другой текст"}],
        )
    with pytest.raises(RuntimeError, match="overlap by normalized text"):
        validate_train_eval_disjoint(
            [{"id": "train", "source_key": "a", "text": " ОДИН   текст "}],
            [{"id": "eval", "source_key": "b", "text": "один текст"}],
        )
    with pytest.raises(RuntimeError, match="CUDA training is required"):
        validate_cuda_training_state(SimpleNamespace(device=torch.device("cpu")), nn.Linear(1, 1), {})


def test_optimizer_bookkeeping_advances_only_when_fp16_update_is_applied() -> None:
    class Scheduler:
        steps = 0

        def step(self) -> None:
            self.steps += 1

    scheduler = Scheduler()
    optimizer_step, applied = advance_optimizer_step_if_applied(
        SimpleNamespace(optimizer_step_was_skipped=False), scheduler, 7
    )
    assert (optimizer_step, applied, scheduler.steps) == (8, True, 1)

    optimizer_step, applied = advance_optimizer_step_if_applied(
        SimpleNamespace(optimizer_step_was_skipped=True), scheduler, optimizer_step
    )
    assert (optimizer_step, applied, scheduler.steps) == (8, False, 1)
