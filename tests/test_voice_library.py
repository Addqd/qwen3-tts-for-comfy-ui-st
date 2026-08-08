import json
from pathlib import Path
import shutil

import numpy as np
import soundfile as sf

from qwen3_tts_st.voices import VoiceLibrary, validate_audio


def make_wav(path: Path, frequency: float = 220, sample_rate: int = 24000):
    sr = sample_rate
    t = np.arange(sr * 2) / sr
    sf.write(path, 0.1 * np.sin(2 * np.pi * frequency * t), sr)


def test_validate_and_create_profile(tmp_path):
    source = tmp_path / "source.wav"
    make_wav(source)
    check = validate_audio(source, "Точный текст.")
    assert check["valid"]
    library = VoiceLibrary(tmp_path / "library")
    profile, validation = library.create(
        source,
        {"character": "Тест", "profile_id": "test_neutral", "display_name": "TestNeutral", "style": "neutral", "ref_text": "Точный текст."},
    )
    assert validation["valid"]
    assert library.resolve(profile.voice_id).reference_path.exists()


def test_corrupt_audio_rejected(tmp_path):
    path = tmp_path / "broken.wav"
    path.write_bytes(b"not a wav")
    assert not validate_audio(path, "текст")["valid"]


def test_create_preserves_reference_sample_rate(tmp_path):
    source = tmp_path / "source-44100.wav"
    make_wav(source, sample_rate=44100)
    library = VoiceLibrary(tmp_path / "library")
    profile, _ = library.create(
        source,
        {
            "character": "RateTest",
            "profile_id": "rate_test_neutral",
            "display_name": "RateTestNeutral",
            "style": "neutral",
            "ref_text": "Точный текст.",
        },
    )
    assert sf.info(profile.reference_path).samplerate == 44100


def test_overwrite_backups_are_preserved_but_never_indexed(tmp_path):
    first_source = tmp_path / "first.wav"
    second_source = tmp_path / "second.wav"
    make_wav(first_source, 220)
    make_wav(second_source, 330)
    library = VoiceLibrary(tmp_path / "library")
    common = {
        "character": "Test",
        "profile_id": "test_neutral",
        "display_name": "TestNeutral",
        "style": "neutral",
    }
    original, _ = library.create(first_source, {**common, "ref_text": "Старый текст."})

    legacy = original.directory.parent / "neutral.backup-20260808-120000"
    shutil.copytree(original.directory, legacy)
    legacy_metadata = json.loads((legacy / "metadata.json").read_text(encoding="utf-8"))
    legacy_metadata.update({"profile_id": "legacy_backup", "display_name": "LegacyBackup"})
    (legacy / "metadata.json").write_text(
        json.dumps(legacy_metadata, ensure_ascii=False), encoding="utf-8"
    )
    assert library.reload() == 1
    assert [item["display_name"] for item in library.list()] == ["TestNeutral"]

    active, _ = library.create(
        second_source,
        {**common, "ref_text": "Новый текст."},
        overwrite=True,
    )
    backups = list(library.backups_root.rglob("metadata.json"))
    assert len(backups) == 1
    assert "profiles" not in backups[0].relative_to(library.root).parts
    assert json.loads(backups[0].read_text(encoding="utf-8"))["ref_text"] == "Старый текст."

    assert library.reload() == 1
    assert len(library.list()) == 1
    assert library.resolve("clone:TestNeutral").ref_text == "Новый текст."
    assert library.find_style("Test", "neutral", active).ref_text == "Новый текст."
    assert not any(item["display_name"] == "LegacyBackup" for item in library.list())
