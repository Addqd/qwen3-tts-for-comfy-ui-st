import json
from pathlib import Path

import numpy as np
import soundfile as sf

from qwen3_tts_st.voices import VoiceLibrary, validate_audio


def make_wav(path: Path):
    sr = 24000
    t = np.arange(sr * 2) / sr
    sf.write(path, 0.1 * np.sin(2 * np.pi * 220 * t), sr)


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

