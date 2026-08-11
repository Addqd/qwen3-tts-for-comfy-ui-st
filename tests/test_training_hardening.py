from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from torch import nn
from torch.utils.data import DataLoader

pytest.importorskip("peft")

from training.russian_adaptation.train_lora import (
    TRAINING_SEMANTICS_VERSION,
    SelectiveTrainingModel,
    advance_optimizer_step_if_applied,
    aligned_subtalker_cross_entropy,
    configure_subtalker_precision,
    copy_non_weight_assets,
    ensure_finite_tensor,
    evaluate,
    forward_aligned_subtalker,
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
    destination = tmp_path / "destination"
    (source / "speech_tokenizer").mkdir(parents=True)
    (source / "model.safetensors").write_bytes(b"old-root")
    (source / "model.safetensors.index.json").write_text("{}", encoding="utf-8")
    (source / "required_asset.pt").write_bytes(b"asset")
    (source / "config.json").write_text("{}", encoding="utf-8")
    (source / "speech_tokenizer" / "model.safetensors").write_bytes(b"speech")
    (source / "speech_tokenizer" / "config.json").write_text("{}", encoding="utf-8")

    copy_non_weight_assets(source, destination)
    assert not (destination / "model.safetensors").exists()
    assert (destination / "required_asset.pt").read_bytes() == b"asset"
    assert (destination / "speech_tokenizer" / "model.safetensors").read_bytes() == b"speech"
    assert (destination / "speech_tokenizer" / "config.json").is_file()

    (destination / "model.safetensors").write_bytes(b"merged-root")
    validate_final_checkpoint_structure(source, destination)
    assert (destination / "model.safetensors").read_bytes() == b"merged-root"


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
