from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import numpy as np
import soundfile as sf


SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare_voice_samples.py"
SPEC = importlib.util.spec_from_file_location("prepare_voice_samples", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_prepare_copies_without_conversion_and_writes_audit_metadata(tmp_path):
    source_root = tmp_path / "downloaded" / "example" / "wavs"
    source_root.mkdir(parents=True)
    wav = source_root / "sample.wav"
    rate = 44100
    sf.write(wav, 0.1 * np.sin(2 * np.pi * 220 * np.arange(rate * 4) / rate), rate, subtype="PCM_16")
    csv_path = tmp_path / "downloaded" / "example" / "test.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=["audio_path", "speaker_id", "text", "emotion", "accent_text"], delimiter="|")
        writer.writeheader()
        writer.writerow({"audio_path": "wavs/sample.wav", "speaker_id": "A", "text": "Точная транскрипция.", "emotion": "neutral", "accent_text": ""})
    manifest = {
        "dataset": {
            "name": "example",
            "source_url": "https://example.invalid",
            "revision": "abc",
            "license": "test",
            "license_url": "https://example.invalid/license",
            "consent_evidence": "test consent",
            "source_csv": "downloaded/example/test.csv",
            "source_audio_root": "downloaded/example/wavs",
            "csv_delimiter": "|",
        },
        "voice_families": [{
            "voice_family": "test_ru_a",
            "speaker_id": "A",
            "character_name": "TestRuA",
            "samples": [{
                "sample_id": "test_ru_a_neutral_01",
                "original_filename": "sample.wav",
                "source_emotion": "neutral",
                "mapped_emotion": "neutral",
                "selection_status": "primary",
                "confidence": "high",
            }],
        }],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    summary = MODULE.prepare(manifest_path, tmp_path)
    prepared = tmp_path / "prepared" / "test_ru_a_neutral_01.wav"
    metadata = json.loads((tmp_path / "metadata" / "test_ru_a_neutral_01.json").read_text(encoding="utf-8"))
    assert summary["prepared_count"] == 1
    assert prepared.read_bytes() == wav.read_bytes()
    assert metadata["sample_rate_hz"] == 44100
    assert metadata["channels"] == 1
    assert metadata["audio_subtype"] == "PCM_16"
    assert metadata["exact_transcript"] == "Точная транскрипция."
    assert metadata["transcript_human_verified_by_project"] is False
