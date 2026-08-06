from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from qwen3_tts_st.app import create_app
from qwen3_tts_st.config import AppConfig, load_config
from qwen3_tts_st.emotion import parse_emotion_script, strip_voice_tags
from qwen3_tts_st.service import TTSService


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Обычный русский текст.", [("neutral", "Обычный русский текст.")]),
        ("[voice:neutral] Спокойно.", [("neutral", "Спокойно.")]),
        ("[voice:happy] Радость! [voice:sad] Грусть.", [("happy", "Радость!"), ("sad", "Грусть.")]),
        ("[voice:angry] Раз! [voice:angry] Два!", [("angry", "Раз!"), ("angry", "Два!")]),
        ("[voice:happy] В одной строке. [voice:tense] И дальше?", [("happy", "В одной строке."), ("tense", "И дальше?")]),
        ("[voice:soft] Первая строка.\n[voice:whisper] Вторая строка.", [("soft", "Первая строка."), ("whisper", "Вторая строка.")]),
        ("До тега — текст, да?! [voice:breathy] После: «текст»...", [("neutral", "До тега — текст, да?!"), ("breathy", "После: «текст»...")]),
    ],
)
def test_parser_preserves_unicode_order_and_punctuation(text, expected):
    assert [(item.style, item.text) for item in parse_emotion_script(text)] == expected


def test_unknown_tag_becomes_neutral_and_is_removed():
    text = "[voice:happy] Да! [voice:EXCITED] Но неизвестный стиль."
    segments = parse_emotion_script(text)
    assert [(item.style, item.text) for item in segments] == [
        ("happy", "Да!"),
        ("neutral", "Но неизвестный стиль."),
    ]
    assert "voice:" not in strip_voice_tags(text)


def test_empty_text_has_no_segments():
    assert parse_emotion_script("  \n ") == []


def _make_config(tmp_path: Path) -> AppConfig:
    data = deepcopy(load_config().data)
    data["model"]["backend"] = "mock"
    data["resources"]["mode"] = "cpu"
    data["voices"]["library_dir"] = str(tmp_path / "voices")
    data["voices"]["fallback_profile"] = "clone:test_ru_router_neutral"
    return AppConfig(data, tmp_path / "test.yaml")


def _add_profile(service: TTSService, tmp_path: Path, style: str) -> str:
    source = tmp_path / f"{style}.wav"
    rate = 24000
    sf.write(source, 0.1 * np.sin(2 * np.pi * 220 * np.arange(rate * 2) / rate), rate)
    display = f"test_ru_router_{style}"
    profile, _ = service.library.create(
        source,
        {
            "character": "TestRuRouter",
            "profile_id": display,
            "display_name": display,
            "style": style,
            "ref_text": "Точный тестовый текст.",
        },
    )
    return profile.voice_id


def test_service_selects_profiles_falls_back_and_never_synthesizes_tags(tmp_path, monkeypatch):
    service = TTSService(_make_config(tmp_path))
    neutral = _add_profile(service, tmp_path, "neutral")
    happy = _add_profile(service, tmp_path, "happy")
    sad = _add_profile(service, tmp_path, "sad")
    angry = _add_profile(service, tmp_path, "angry")
    captured: list[tuple[str, str]] = []
    original = service.worker.synthesize

    def recording_synthesize(text, profile, language):
        captured.append((text, profile.voice_id))
        return original(text, profile, language)

    monkeypatch.setattr(service.worker, "synthesize", recording_synthesize)
    request = SimpleNamespace(
        input=(
            "[voice:neutral] Сейчас я говорю спокойно. "
            "[voice:happy] А теперь я очень рад! "
            "[voice:sad] Но потом мне стало грустно. "
            "[voice:angry] И наконец я разозлился! "
            "[voice:soft] Для этого стиля профиля нет."
        ),
        preprocessing_mode="all",
        voice="missing-requested-profile",
        speed=1.0,
        response_format="wav",
    )
    payload, media_type, metadata = asyncio.run(service.synthesize(request))

    assert media_type == "audio/wav"
    assert payload.startswith(b"RIFF")
    assert metadata["styles"] == ["neutral", "happy", "sad", "angry", "soft"]
    assert metadata["voices"] == [neutral, happy, sad, angry, neutral]
    assert [voice for _, voice in captured] == metadata["voices"]
    assert [text for text, _ in captured] == [
        "Сейчас я говорю спокойно.",
        "А теперь я очень рад!",
        "Но потом мне стало грустно.",
        "И наконец я разозлился!",
        "Для этого стиля профиля нет.",
    ]
    assert all("[voice:" not in text for text, _ in captured)
    assert metadata["duration_seconds"] > 0


def test_empty_api_input_is_rejected(tmp_path):
    with TestClient(create_app(config=_make_config(tmp_path))) as client:
        response = client.post(
            "/v1/audio/speech",
            json={"voice": "missing", "input": "", "response_format": "wav"},
        )
    assert response.status_code == 422
