from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import torch

import training.russian_adaptation.prepare_dataset as prepare_dataset_module
from training.russian_adaptation.common import PACKAGE_DIR, load_plan, ordinary_text
from training.russian_adaptation.dataset import RussianAdaptationDataset
from training.russian_adaptation.prepare_codes import encode_manifest
from training.russian_adaptation.prepare_dataset import Candidate, _features, row_is_eligible, select_candidates


def _metadata(index: int) -> dict:
    return {
        "speaker_id": index // 4,
        "total_duration": 2.0,
        "is_single_speaker": True,
        "asr_consistency": 100.0,
        "silence_percent": 2.0,
        "max_silence_duration": 0.1,
        "music_prob": 0.01,
        "DistillMOS": 4.5,
        "punct": f"Она выразительно произнесла тестовую фразу номер {index}.",
        "accent": f"Он+а выраз+ительно произнесл+а тестовую фр+азу н+омер {index}.",
        "rover_phonemes": "a n a v ɨ r a z i tʲ e lʲ n a",
    }


def _candidate(index: int, split: str = "train") -> Candidate:
    metadata = _metadata(index)
    shard = f"shard_{index // 4:03d}.tar"
    key = f"key-{index}"
    return Candidate(
        split=split,
        shard_path=Path(shard),
        shard_name=shard,
        key=key,
        audio_member=f"{key}.opus",
        metadata=metadata,
        text=ordinary_text(metadata),
        duration=2.0,
        selection_group=shard,
        item_id=f"{split}-{index:03d}",
    )


def test_plan_is_conservative_and_keeps_base_outputs_separate() -> None:
    plan = load_plan()
    assert plan["dataset"]["repo_id"] == "lab260/golos_balalaika"
    assert plan["dataset"]["train_split"] == "train"
    assert plan["dataset"]["eval_split"] == "test"
    assert plan["dataset"]["eval_target_seconds"] == 120
    assert plan["training"]["epochs"] == 1
    assert plan["training"]["precision"] == "fp16"
    assert plan["training"]["attention"] == "sdpa"
    assert set(plan["models"]) == {"0.6b", "1.7b"}
    assert plan["tokenizer"]["revision"] == "7dd38ad4e9bad454aae9cd937d0cd577604fe229"
    assert plan["models"]["0.6b"]["revision"] == "5d83992436eae1d760afd27aff78a71d676296fc"
    assert plan["models"]["1.7b"]["revision"] == "fd4b254389122332181a7c3db7f27e918eec64e3"
    for spec in plan["models"].values():
        assert len(spec["revision"]) == 40
        assert set(spec["revision"]) <= set("0123456789abcdef")
        assert spec["repo_id"].endswith("-Base")
        assert spec["expected_tts_model_type"] == "base"
        assert spec["output_dir"].startswith("trained_models/")
        assert "model_cache" not in spec["output_dir"]


def test_filter_uses_plain_punctuation_text_not_accent_hacks() -> None:
    plan = load_plan()
    metadata = _metadata(0)
    assert "+" in metadata["accent"]
    assert "+" not in ordinary_text(metadata)
    assert row_is_eligible(metadata, plan["dataset"]["filters"])


def test_stress_annotations_affect_selection_features_only() -> None:
    first = _candidate(0)
    second_metadata = {**first.metadata, "accent": "Он+а в+ыразительно произнесл+а тестовую фр+азу н+омер н+оль."}
    second = Candidate(**{**first.__dict__, "metadata": second_metadata, "item_id": "stress-variant"})
    first_stress = {feature for feature in _features(first) if feature.startswith("s:")}
    second_stress = {feature for feature in _features(second) if feature.startswith("s:")}
    assert first_stress
    assert first_stress != second_stress
    assert ordinary_text(first.metadata) == ordinary_text(second.metadata)
    assert "+" not in ordinary_text(second.metadata)


def test_pinned_revisions_are_forwarded_to_all_hugging_face_loads() -> None:
    training_root = PACKAGE_DIR
    prepare_codes = (training_root / "prepare_codes.py").read_text(encoding="utf-8")
    train_lora = (training_root / "train_lora.py").read_text(encoding="utf-8")
    validate = (training_root / "validate.py").read_text(encoding="utf-8")
    assert prepare_codes.count('revision=tokenizer_config["revision"]') == 2
    assert 'Qwen3TTSTokenizer.from_pretrained(\n        str(tokenizer_snapshot)' in prepare_codes
    assert train_lora.count('revision=model_spec["revision"]') == 2
    assert 'Qwen3TTSModel.from_pretrained(\n        str(base_snapshot)' in train_lora
    assert 'revision=spec["revision"]' in validate


def test_diverse_selection_is_deterministic_without_fake_speaker_pairing() -> None:
    candidates = [_candidate(index) for index in range(12)]
    first = select_candidates(
        candidates, target_seconds=10.0, per_group_cap_seconds=6.0, seed=2602026
    )
    second = select_candidates(
        list(reversed(candidates)), target_seconds=10.0, per_group_cap_seconds=6.0, seed=2602026
    )
    assert [row.item_id for row in first] == [row.item_id for row in second]
    assert len({row.selection_group for row in first}) >= 2


def test_collator_applies_explicit_russian_prefix() -> None:
    talker = SimpleNamespace(
        codec_language_id={"russian": 42},
        codec_think_id=10,
        codec_think_bos_id=11,
        codec_think_eos_id=12,
        codec_pad_id=13,
        codec_bos_id=14,
        codec_eos_token_id=15,
    )
    config = SimpleNamespace(tts_pad_token_id=20, tts_bos_token_id=21, tts_eos_token_id=22, talker_config=talker)
    dataset = RussianAdaptationDataset([], processor=None, config=config)
    batch = dataset.collate_fn(
        [
            {
                "text_ids": torch.arange(10).unsqueeze(0),
                "audio_codes": torch.ones((4, 16), dtype=torch.long),
                "ref_mel": torch.zeros((1, 5, 128)),
            }
        ]
    )
    assert batch["input_ids"][0, 3:9, 1].tolist() == [10, 11, 42, 12, 0, 13]
    assert not batch["codec_embedding_mask"][0, 7, 0]
    assert batch["codec_mask"].sum().item() == 4


def test_regression_corpus_and_runner_contract() -> None:
    rows = [json.loads(line) for line in (PACKAGE_DIR / "regression_ru.jsonl").read_text(encoding="utf-8").splitlines()]
    texts = {row["text"] for row in rows}
    assert {
        "Она пришла.",
        "Это она.",
        "Он и она пришли вместе.",
        "Она сказала, что она останется.",
        "Но она этого не знала.",
    }.issubset(texts)
    assert sum("игриво" in text.lower() for text in texts) >= 3

    runner = (PACKAGE_DIR.parents[1] / "scripts" / "train-russian-adaptation.ps1").read_text(encoding="utf-8")
    continue_index = runner.index('$ErrorActionPreference = "Continue"')
    native_call_index = runner.index("& $Python @Arguments 2>&1 | Tee-Object")
    finally_index = runner.index("finally", native_call_index)
    restore_index = runner.index("$ErrorActionPreference = $previousErrorActionPreference", finally_index)
    exit_check_index = runner.index("if ($LASTEXITCODE -ne 0)", restore_index)
    assert continue_index < native_call_index < finally_index < restore_index < exit_check_index
    assert runner.index('"--model-key", "0.6b"') < runner.index('"--model-key", "1.7b"')
    assert "Start-Process" not in runner
    assert "Start-Job" not in runner


def test_code_manifest_is_resumable_and_reused(tmp_path: Path) -> None:
    raw = tmp_path / "train_raw.jsonl"
    output = tmp_path / "train_with_codes.jsonl"
    source_rows = [
        {"id": "one", "audio": "runtime/nonexistent-one.wav"},
        {"id": "two", "audio": "runtime/nonexistent-two.wav"},
    ]
    raw.write_text("".join(json.dumps(row) + "\n" for row in source_rows), encoding="utf-8")

    class FakeTokenizer:
        calls = 0

        def encode(self, paths, return_dict=True):
            self.calls += 1
            return SimpleNamespace(audio_codes=[torch.ones((2, 16), dtype=torch.long) for _ in paths])

    tokenizer = FakeTokenizer()
    assert encode_manifest(raw, output, tokenizer, batch_size=1) == 2
    assert tokenizer.calls == 2
    assert not output.with_suffix(".partial.jsonl").exists()
    assert encode_manifest(raw, output, tokenizer, batch_size=1) == 2
    assert tokenizer.calls == 2


def test_selected_audio_opens_each_shard_once(tmp_path: Path, monkeypatch) -> None:
    selected = [_candidate(0), _candidate(1), _candidate(4)]
    opened: list[Path] = []
    writes: list[tuple[object, str]] = []

    class FakeArchive:
        def __init__(self, path: Path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    def fake_open(path, mode):
        opened.append(path)
        return FakeArchive(path)

    def fake_write(archive, candidate, destination, sample_rate):
        writes.append((archive, candidate.item_id))

    monkeypatch.setattr(prepare_dataset_module.tarfile, "open", fake_open)
    monkeypatch.setattr(prepare_dataset_module, "_write_audio", fake_write)
    paths = prepare_dataset_module._extract_selected_audio(selected, tmp_path, "train", 24000)

    assert opened == [selected[0].shard_path, selected[2].shard_path]
    assert writes[0][0] is writes[1][0]
    assert writes[0][0] is not writes[2][0]
    assert list(paths) == [candidate.item_id for candidate in selected]
