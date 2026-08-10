from __future__ import annotations

import argparse
import io
import json
import math
import re
import tarfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from math import gcd
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from training.russian_adaptation.common import (
    PROJECT_ROOT,
    has_russian_text,
    load_plan,
    ordinary_text,
    project_path,
    source_id,
    stable_fraction,
    write_json_atomic,
    write_jsonl_atomic,
)


@dataclass(frozen=True)
class Candidate:
    split: str
    shard_path: Path
    shard_name: str
    key: str
    audio_member: str
    metadata: dict[str, Any]
    text: str
    duration: float
    selection_group: str
    item_id: str


def _number(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def row_is_eligible(metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
    duration = _number(metadata.get("total_duration"), -1.0)
    text = ordinary_text(metadata)
    mos = metadata.get("DistillMOS")
    return (
        metadata.get("is_single_speaker") is True
        and filters["min_duration_seconds"] <= duration <= filters["max_duration_seconds"]
        and _number(metadata.get("asr_consistency"), -1.0) >= filters["min_asr_consistency"]
        and _number(metadata.get("silence_percent"), 100.0) <= filters["max_silence_percent"]
        and _number(metadata.get("max_silence_duration"), 100.0) <= filters["max_single_silence_seconds"]
        and _number(metadata.get("music_prob"), 1.0) <= filters["max_music_probability"]
        and (mos is None or _number(mos, -1.0) >= filters["min_distill_mos_when_present"])
        and has_russian_text(text)
        and 2 <= len(text.split()) <= 60
    )


def scan_split(source_dir: Path, split: str, filters: dict[str, Any]) -> list[Candidate]:
    candidates: list[Candidate] = []
    shard_paths = sorted((source_dir / split).glob("*.tar"))
    if not shard_paths:
        raise FileNotFoundError(f"No WebDataset tar shards found in {source_dir / split}")

    for shard_path in shard_paths:
        with tarfile.open(shard_path, "r") as archive:
            member_names = {member.name for member in archive.getmembers() if member.isfile()}
            for member_name in sorted(name for name in member_names if name.endswith(".json")):
                extracted = archive.extractfile(member_name)
                if extracted is None:
                    continue
                metadata = json.loads(extracted.read().decode("utf-8"))
                if not row_is_eligible(metadata, filters):
                    continue
                key = member_name[: -len(".json")]
                audio_member = f"{key}.opus"
                if audio_member not in member_names:
                    continue
                shard_name = shard_path.name
                candidates.append(
                    Candidate(
                        split=split,
                        shard_path=shard_path,
                        shard_name=shard_name,
                        key=key,
                        audio_member=audio_member,
                        metadata=metadata,
                        text=ordinary_text(metadata),
                        duration=_number(metadata.get("total_duration"), 0.0),
                        selection_group=shard_name,
                        item_id=source_id(split, shard_name, key),
                    )
                )
    return candidates


def _features(candidate: Candidate) -> set[str]:
    normalized = re.sub(r"[^а-яё ]", "", candidate.text.lower())
    compact = normalized.replace(" ", "_")
    char_trigrams = {f"c:{compact[index:index + 3]}" for index in range(max(0, len(compact) - 2))}
    endings = {f"e:{word[-3:]}" for word in normalized.split() if len(word) >= 3}
    phonemes = str(candidate.metadata.get("rover_phonemes") or "").split()
    phone_bigrams = {f"p:{left}>{right}" for left, right in zip(phonemes, phonemes[1:])}
    punctuation = {f"u:{mark}" for mark in "?!—,:" if mark in candidate.text}
    length_bucket = {f"l:{min(len(candidate.text.split()) // 4, 8)}"}
    return char_trigrams | endings | phone_bigrams | punctuation | length_bucket


def select_candidates(
    candidates: list[Candidate],
    *,
    target_seconds: float,
    per_group_cap_seconds: float,
    seed: int,
) -> list[Candidate]:
    pool = list(candidates)
    if not pool:
        raise RuntimeError("No eligible targets remain after quality filtering")

    feature_sets = {row.item_id: _features(row) for row in pool}
    feature_counts = Counter(feature for features in feature_sets.values() for feature in features)

    def diversity_score(row: Candidate) -> tuple[float, float, str]:
        features = feature_sets[row.item_id]
        rarity = sum(1.0 / math.sqrt(feature_counts[feature]) for feature in features) / max(len(features), 1)
        quality = min(_number(row.metadata.get("asr_consistency"), 90.0) / 100.0, 1.0)
        mos = min(_number(row.metadata.get("DistillMOS"), 4.0) / 5.0, 1.0)
        jitter = stable_fraction(seed, row.item_id)
        return (rarity + quality * 0.08 + mos * 0.04 + jitter * 0.01, jitter, row.item_id)

    ranked = sorted(pool, key=diversity_score, reverse=True)
    selected: list[Candidate] = []
    group_seconds: dict[str, float] = defaultdict(float)
    total_seconds = 0.0
    for row in ranked:
        if total_seconds >= target_seconds:
            break
        if group_seconds[row.selection_group] + row.duration > per_group_cap_seconds:
            continue
        selected.append(row)
        group_seconds[row.selection_group] += row.duration
        total_seconds += row.duration

    if total_seconds < target_seconds * 0.9:
        raise RuntimeError(
            f"Eligible diverse subset is too small: {total_seconds:.1f}s selected, "
            f"target is {target_seconds:.1f}s"
        )
    return sorted(selected, key=lambda row: row.item_id)


def _decode_member(candidate: Candidate, sample_rate: int) -> np.ndarray:
    with tarfile.open(candidate.shard_path, "r") as archive:
        handle = archive.extractfile(candidate.audio_member)
        if handle is None:
            raise FileNotFoundError(f"Missing {candidate.audio_member} in {candidate.shard_path}")
        samples, source_rate = sf.read(io.BytesIO(handle.read()), dtype="float32", always_2d=True)
    mono = samples.mean(axis=1)
    if source_rate != sample_rate:
        divisor = gcd(source_rate, sample_rate)
        mono = resample_poly(mono, sample_rate // divisor, source_rate // divisor).astype(np.float32)
    if not np.isfinite(mono).all() or mono.size == 0:
        raise ValueError(f"Invalid decoded audio for {candidate.item_id}")
    return np.clip(mono, -1.0, 1.0)


def _write_audio(candidate: Candidate, destination: Path, sample_rate: int) -> None:
    if destination.is_file():
        info = sf.info(destination)
        if info.samplerate != sample_rate or info.channels != 1 or info.frames <= 0:
            raise ValueError(f"Existing prepared audio is invalid; preserved for inspection: {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp.wav")
    sf.write(temporary, _decode_member(candidate, sample_rate), sample_rate, subtype="PCM_16")
    temporary.replace(destination)


def _manifest_row(
    candidate: Candidate,
    audio_path: Path,
    revision: str,
) -> dict[str, Any]:
    return {
        "id": candidate.item_id,
        "dataset": "lab260/golos_balalaika",
        "dataset_revision": revision,
        "source_split": candidate.split,
        "source_shard": candidate.shard_name,
        "source_key": candidate.key,
        "selection_group": candidate.selection_group,
        "language": "Russian",
        "text": candidate.text,
        "accent_annotation": candidate.metadata.get("accent"),
        "phoneme_annotation": candidate.metadata.get("rover_phonemes"),
        "duration_seconds": candidate.duration,
        "audio": audio_path.resolve().relative_to(PROJECT_ROOT).as_posix(),
        "reference_id": candidate.item_id,
        "reference_audio": audio_path.resolve().relative_to(PROJECT_ROOT).as_posix(),
        "reference_strategy": "self_conditioned_frozen_speaker_encoder",
    }


def _prepare_split(
    source_dir: Path,
    prepared_root: Path,
    manifest_path: Path,
    split: str,
    config: dict[str, Any],
    sample_rate: int,
    revision: str,
    seed: int,
) -> tuple[list[dict[str, Any]], int]:
    candidates = scan_split(source_dir, split, config["filters"])
    selected = select_candidates(
        candidates,
        target_seconds=config[f"{split if split == 'train' else 'eval'}_target_seconds"],
        per_group_cap_seconds=config["max_seconds_per_selection_group"],
        seed=seed,
    )
    rows: list[dict[str, Any]] = []
    output_split = "train" if split == config["train_split"] else "eval"
    for candidate in selected:
        audio_path = prepared_root / output_split / "targets" / f"{candidate.item_id}.wav"
        _write_audio(candidate, audio_path, sample_rate)
        rows.append(_manifest_row(candidate, audio_path, revision))
    write_jsonl_atomic(manifest_path, rows)
    return rows, len(candidates)


def download_dataset(source_dir: Path, repo_id: str, revision: str) -> None:
    from huggingface_hub import snapshot_download

    source_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repo_id,
        repo_type="dataset",
        revision=revision,
        allow_patterns=["README.md", "train/*.tar", "test/*.tar"],
        local_dir=source_dir,
        cache_dir=project_path("model_cache"),
    )


def prepare() -> dict[str, Any]:
    plan = load_plan()
    dataset = plan["dataset"]
    tokenizer = plan["tokenizer"]
    data_root = project_path("training_data/golos_balalaika")
    source_dir = data_root / "source"
    manifest_dir = data_root / "manifests"
    prepared_root = data_root / "prepared_audio"
    summary_path = manifest_dir / "selection_summary.json"
    train_manifest = manifest_dir / "train_raw.jsonl"
    eval_manifest = manifest_dir / "eval_raw.jsonl"

    download_dataset(source_dir, dataset["repo_id"], dataset["revision"])
    train_rows, train_eligible = _prepare_split(
        source_dir,
        prepared_root,
        train_manifest,
        dataset["train_split"],
        dataset,
        tokenizer["sample_rate"],
        dataset["revision"],
        dataset["seed"],
    )
    eval_config = dict(dataset)
    eval_config["eval_target_seconds"] = dataset["eval_target_seconds"]
    eval_rows, eval_eligible = _prepare_split(
        source_dir,
        prepared_root,
        eval_manifest,
        dataset["eval_split"],
        eval_config,
        tokenizer["sample_rate"],
        dataset["revision"],
        dataset["seed"] + 1,
    )
    overlap = {row["id"] for row in train_rows} & {row["id"] for row in eval_rows}
    if overlap:
        raise RuntimeError(f"Train/eval overlap detected: {sorted(overlap)[:3]}")

    summary = {
        "dataset": dataset["repo_id"],
        "revision": dataset["revision"],
        "seed": dataset["seed"],
        "train": {
            "manifest": train_manifest.relative_to(PROJECT_ROOT).as_posix(),
            "eligible_count": train_eligible,
            "selected_count": len(train_rows),
            "selected_seconds": round(sum(row["duration_seconds"] for row in train_rows), 3),
            "selection_groups": len({row["selection_group"] for row in train_rows}),
        },
        "eval": {
            "manifest": eval_manifest.relative_to(PROJECT_ROOT).as_posix(),
            "eligible_count": eval_eligible,
            "selected_count": len(eval_rows),
            "selected_seconds": round(sum(row["duration_seconds"] for row in eval_rows), 3),
            "selection_groups": len({row["selection_group"] for row in eval_rows}),
        },
        "train_eval_overlap": 0,
        "training_text_source": "punct with rover fallback; accent/phoneme fields are metadata only",
        "reference_strategy": "self-conditioned target audio; dataset speaker_id is not treated as a global identity",
    }
    write_json_atomic(summary_path, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare deterministic golos_balalaika subsets")
    parser.parse_args()
    summary = prepare()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
