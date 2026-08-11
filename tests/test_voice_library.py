import json
import logging
from pathlib import Path
import shutil

import numpy as np
import pytest
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


def test_performance_capabilities_persist_and_resolve_deterministically(tmp_path):
    source = tmp_path / "source.wav"
    make_wav(source)
    root = tmp_path / "library"
    library = VoiceLibrary(root)

    legacy_dir = root / "profiles" / "legacy" / "neutral"
    legacy_dir.mkdir(parents=True)
    shutil.copy2(source, legacy_dir / "reference.wav")
    (legacy_dir / "metadata.json").write_text(
        json.dumps(
            {
                "character": "Legacy",
                "profile_id": "legacy_neutral",
                "display_name": "LegacyNeutral",
                "style": "neutral",
                "reference_audio": "reference.wav",
                "ref_text": "Старый профиль.",
                "language": "Russian",
                "clone_mode": "icl",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def create(profile_id, emotion, sounds, *, character="Performer", emotion_enabled=True):
        return library.create(
            source,
            {
                "character": character,
                "profile_id": profile_id,
                "display_name": profile_id,
                "style": emotion,
                "emotion_enabled": emotion_enabled,
                "sound_enabled": bool(sounds),
                "sounds": sounds,
                "ref_text": "Точный текст референса.",
            },
        )[0]

    pleasure = create("a_pleasure", "pleasure", ["moan", "pant"])
    intimate = create("b_intimate", "intimate", ["moan", "sigh"])
    create("z_laugh", "neutral", ["laugh"], emotion_enabled=False)
    alpha_laugh = create("a_laugh", "neutral", ["laugh"], emotion_enabled=False)
    create("other_giggle", "happy", ["giggle"], character="Other")

    reloaded = VoiceLibrary(root)
    legacy = reloaded.resolve("legacy_neutral")
    assert legacy.emotion_enabled is True
    assert legacy.emotion == "neutral"
    assert legacy.sound_enabled is False
    assert legacy.sounds == ()
    persisted = json.loads((pleasure.directory / "metadata.json").read_text(encoding="utf-8"))
    assert persisted["emotion_enabled"] is True
    assert persisted["emotion"] == "pleasure"
    assert persisted["sound_enabled"] is True
    assert persisted["sounds"] == ["pant", "moan"]
    assert reloaded.find_sound("Performer", "moan", "pleasure").profile_id == pleasure.profile_id
    assert reloaded.find_sound("Performer", "moan", "intimate").profile_id == intimate.profile_id
    assert reloaded.find_sound("Performer", "moan", "happy").profile_id == pleasure.profile_id
    assert reloaded.find_sound("Performer", "laugh").profile_id == alpha_laugh.profile_id
    assert reloaded.find_sound("Performer", "giggle") is None
    with pytest.raises(ValueError, match="unsupported sound capability"):
        reloaded.find_sound("Performer", "whistle")


def test_legacy_unsupported_style_remains_loadable_but_not_routable(tmp_path, caplog):
    source = tmp_path / "source.wav"
    make_wav(source)
    root = tmp_path / "library"
    library = VoiceLibrary(root)
    fallback, _ = library.create(
        source,
        {
            "character": "Fallback",
            "profile_id": "fallback_neutral",
            "display_name": "FallbackNeutral",
            "style": "neutral",
            "ref_text": "Точный текст.",
        },
    )
    legacy_dir = root / "profiles" / "legacy" / "mysterious"
    legacy_dir.mkdir(parents=True)
    shutil.copy2(source, legacy_dir / "reference.wav")
    (legacy_dir / "metadata.json").write_text(
        json.dumps(
            {
                "character": "Legacy",
                "profile_id": "legacy_mysterious",
                "display_name": "LegacyMysterious",
                "style": "Mysterious Legacy",
                "reference_audio": "reference.wav",
                "ref_text": "Старый текст.",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="qwen3_tts_st.voices"):
        library.reload()
    legacy = library.resolve("legacy_mysterious")
    assert legacy.style == "Mysterious Legacy"
    assert legacy.emotion == "Mysterious Legacy"
    assert legacy.emotion_enabled is False
    assert library.find_style("Legacy", "mysterious", fallback) is fallback
    assert "emotion routing disabled" in caplog.text


def test_sound_only_profiles_use_profile_names_and_cannot_be_neutral_speech(tmp_path):
    source = tmp_path / "source.wav"
    make_wav(source)
    library = VoiceLibrary(tmp_path / "library")

    def create(profile_id: str, *, overwrite: bool = False):
        return library.create(
            source,
            {
                "character": "SoundOnly",
                "profile_id": profile_id,
                "style": "neutral",
                "emotion_enabled": False,
                "sound_enabled": True,
                "sounds": ["laugh"],
                "ref_text": "Ха-ха-ха.",
            },
            overwrite=overwrite,
        )[0]

    first = create("sound_laugh_a")
    second = create("sound_laugh_b")
    assert first.display_name == "sound_laugh_a"
    assert second.display_name == "sound_laugh_b"
    with pytest.raises(KeyError, match="neutral-профиль"):
        library.resolve_family_neutral(first, first.voice_id)

    create("sound_laugh_a", overwrite=True)
    backup_names = [path.name for path in (library.backups_root / "soundonly").iterdir()]
    assert any(name.startswith("sound_laugh_a-") for name in backup_names)
    assert not any(name.startswith("neutral-") for name in backup_names)


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
