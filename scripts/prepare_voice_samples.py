from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inside(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path escapes source root: {relative}")
    return candidate


def _edge_silence_seconds(mono: np.ndarray, sample_rate: int, threshold_db: float = -50.0) -> tuple[float, float]:
    if not len(mono) or not sample_rate:
        return 0.0, 0.0
    threshold = 10 ** (threshold_db / 20)
    audible = np.flatnonzero(np.abs(mono) >= threshold)
    if not len(audible):
        duration = len(mono) / sample_rate
        return duration, duration
    leading = int(audible[0]) / sample_rate
    trailing = (len(mono) - int(audible[-1]) - 1) / sample_rate
    return leading, trailing


def analyze_audio(path: Path) -> dict[str, Any]:
    info = sf.info(path)
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    rms = float(math.sqrt(float(np.mean(np.square(mono))))) if len(mono) else 0.0
    clipping_fraction = float(np.mean(np.abs(audio) >= 0.999)) if audio.size else 0.0
    leading, trailing = _edge_silence_seconds(mono, sample_rate)
    return {
        "sample_rate_hz": sample_rate,
        "channels": int(audio.shape[1]),
        "bit_depth": info.subtype_info,
        "audio_subtype": info.subtype,
        "duration_seconds": round(len(mono) / sample_rate, 6),
        "peak": round(peak, 6),
        "rms": round(rms, 6),
        "clipping_fraction": round(clipping_fraction, 8),
        "leading_silence_seconds_at_minus_50_db": round(leading, 6),
        "trailing_silence_seconds_at_minus_50_db": round(trailing, 6),
        "sha256": _sha256(path),
    }


def _load_rows(path: Path, delimiter: str) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = csv.DictReader(source, delimiter=delimiter)
        return {Path(row["audio_path"]).name: row for row in rows}


def prepare(manifest_path: Path, workspace: Path) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    dataset = manifest["dataset"]
    source_root = _inside(workspace, dataset["source_audio_root"])
    csv_path = _inside(workspace, dataset["source_csv"])
    rows = _load_rows(csv_path, dataset.get("csv_delimiter", "|"))

    prepared_root = workspace / "prepared"
    transcripts_root = workspace / "transcripts"
    metadata_root = workspace / "metadata"
    selected_root = workspace / "selected"
    reports_root = workspace / "reports"
    for path in (prepared_root, transcripts_root, metadata_root, selected_root, reports_root):
        path.mkdir(parents=True, exist_ok=True)

    selected: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []

    for family in manifest["voice_families"]:
        family_id = family["voice_family"]
        for sample in family["samples"]:
            filename = sample["original_filename"]
            if filename not in rows:
                raise KeyError(f"metadata row not found: {filename}")
            row = rows[filename]
            if row["speaker_id"] != family["speaker_id"]:
                raise ValueError(f"speaker mismatch for {filename}")
            if row["emotion"] != sample["source_emotion"]:
                raise ValueError(f"emotion mismatch for {filename}")

            source = _inside(source_root, filename)
            if not source.is_file():
                raise FileNotFoundError(source)
            sample_id = sample["sample_id"]
            prepared = prepared_root / f"{sample_id}.wav"
            shutil.copy2(source, prepared)
            analysis = analyze_audio(prepared)
            transcript = row["text"].strip()

            preparation_notes = [
                "Copied byte-for-byte from the pinned dataset revision; no resampling, normalization, or trimming.",
                "Dataset transcript retained verbatim; human listening verification is still required before production use.",
            ]
            quality_notes: list[str] = []
            if analysis["clipping_fraction"]:
                quality_notes.append("Samples at or above 0.999 full scale were detected.")
            else:
                quality_notes.append("No samples at or above 0.999 full scale were detected.")
            if analysis["leading_silence_seconds_at_minus_50_db"] > 1.0:
                quality_notes.append("Leading low-level region exceeds one second; review by ear before trimming.")
            if analysis["trailing_silence_seconds_at_minus_50_db"] > 1.0:
                quality_notes.append("Trailing low-level region exceeds one second; review by ear before trimming.")
            if not quality_notes:
                quality_notes.append("No objective container or level issue detected.")

            metadata = {
                "sample_id": sample_id,
                "speaker_id": family["speaker_id"],
                "voice_family": family_id,
                "character_name": family["character_name"],
                "original_dataset": dataset["name"],
                "source_url": dataset["source_url"],
                "source_revision": dataset["revision"],
                "license": dataset["license"],
                "license_url": dataset["license_url"],
                "consent_evidence": dataset["consent_evidence"],
                "original_filename": filename,
                "source_emotion": row["emotion"],
                "mapped_emotion": sample["mapped_emotion"],
                "mapping_notes": sample.get("mapping_notes", "Direct label mapping."),
                "exact_transcript": transcript,
                "accented_transcript": row.get("accent_text", ""),
                "transcript_source": "dataset metadata",
                "transcript_human_verified_by_project": False,
                "selection_status": sample["selection_status"],
                "confidence": sample["confidence"],
                "preparation_notes": preparation_notes,
                "quality_notes": quality_notes,
                **analysis,
            }
            (transcripts_root / f"{sample_id}.txt").write_text(transcript + "\n", encoding="utf-8")
            _write_json(metadata_root / f"{sample_id}.json", metadata)

            if sample["selection_status"] in {"primary", "backup", "diversity"}:
                target = selected_root / family_id / sample["mapped_emotion"]
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(prepared, target / f"{sample['selection_status']}_{sample_id}.wav")
                (target / f"{sample['selection_status']}_{sample_id}.txt").write_text(transcript + "\n", encoding="utf-8")
                _write_json(target / f"{sample['selection_status']}_{sample_id}.json", metadata)
                selected.append(metadata)
            else:
                rejected.append({**metadata, "rejection_reason": sample["rejection_reason"]})
            quality.append(metadata)

    for gap in manifest.get("unfilled_styles", []):
        rejected.append({
            "voice_family": gap["voice_family"],
            "mapped_emotion": gap["mapped_emotion"],
            "selection_status": "not_available",
            "rejection_reason": gap["reason"],
        })

    _write_json(reports_root / "selected_samples.json", selected)
    _write_json(reports_root / "rejected_samples.json", rejected)
    _write_json(reports_root / "audio_quality_report.json", quality)
    summary = {
        "dataset": dataset["name"],
        "prepared_count": len(quality),
        "selected_count": len(selected),
        "rejected_or_unavailable_count": len(rejected),
        "voice_families": sorted({item["voice_family"] for item in selected}),
        "profiles_must_not_mix_speakers": True,
        "human_listening_required": True,
    }
    _write_json(reports_root / "preparation_summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare ignored local voice samples with reproducible metadata.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=Path("local_voice_samples"))
    args = parser.parse_args()
    print(json.dumps(prepare(args.manifest.resolve(), args.workspace.resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
