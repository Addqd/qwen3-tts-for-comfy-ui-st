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
from qwen3_tts_st.emotion import (
    ALLOWED_SOUNDS,
    ALLOWED_STYLES,
    parse_emotion_script,
    parse_emotion_script_detailed,
    strip_voice_tags,
)
from qwen3_tts_st.service import TTSService


def parsed(text: str) -> list[tuple[str, str, str]]:
    return [(item.kind, item.style, item.text) for item in parse_emotion_script(text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Она подошла к окну.", [("narration", "neutral", "Она подошла к окну.")]),
        ('"Ты уже закончил?"', [("dialogue", "neutral", "Ты уже закончил?")]),
        ('[voice:happy] "Ты пришёл!"', [("dialogue", "happy", "Ты пришёл!")]),
        ('[voice:happy]\n"Ты пришёл!"', [("dialogue", "happy", "Ты пришёл!")]),
        (
            '[voice:angry] "Отстань!"\nОна отвернулась.',
            [("dialogue", "angry", "Отстань!"), ("narration", "neutral", "Она отвернулась.")],
        ),
        (
            '[voice:angry] "Отстань!"\nОна отвернулась.\n"Я не хочу говорить."',
            [
                ("dialogue", "angry", "Отстань!"),
                ("narration", "neutral", "Она отвернулась."),
                ("dialogue", "neutral", "Я не хочу говорить."),
            ],
        ),
        (
            'Она остановилась.\n[voice:tense] "Ты слышал?"\nОна замерла.\n[voice:whisper] "Тише."',
            [
                ("narration", "neutral", "Она остановилась."),
                ("dialogue", "tense", "Ты слышал?"),
                ("narration", "neutral", "Она замерла."),
                ("dialogue", "whisper", "Тише."),
            ],
        ),
        (
            '[voice:angry] "Ты издеваешься?\nЯ дважды предупреждала.\nИ ты всё равно это сделал."',
            [("dialogue", "angry", "Ты издеваешься?\nЯ дважды предупреждала.\nИ ты всё равно это сделал.")],
        ),
        (
            '[voice:tense] "Он действительно сказал «уходи»?"',
            [("dialogue", "tense", "Он действительно сказал «уходи»?")],
        ),
        (
            'Ёжик спросил 😊: [voice:soft] "Всё-таки пойдём?.."',
            [
                ("narration", "neutral", "Ёжик спросил 😊:"),
                ("dialogue", "soft", "Всё-таки пойдём?.."),
            ],
        ),
        (
            '[voice:tense] "Он сказал: \\"не двигайся\\" — и замолчал."',
            [("dialogue", "tense", 'Он сказал: "не двигайся" — и замолчал.')],
        ),
    ],
)
def test_quote_aware_parser_contract(text, expected):
    assert parsed(text) == expected


def test_all_ten_delivery_styles_remain_allowlisted():
    assert ALLOWED_STYLES == {
        "neutral", "soft", "whisper", "breathy", "happy", "sad", "angry", "tense",
        "pleasure", "intimate",
    }


def test_performance_router_orders_speech_and_sounds_without_leaking_service_tags():
    text = (
        '[voice:pleasure] "Да... продолжай." [sound:moan] '
        '[sound:unknown] [sound: laugh] '
        '[voice:breathy] "Не останавливайся..." [sound:pant]'
    )
    segments, warnings = parse_emotion_script_detailed(text)

    assert ALLOWED_SOUNDS == {"laugh", "giggle", "gasp", "sigh", "pant", "moan"}
    assert [
        (item.kind, item.style, item.text, item.sound_type, item.preferred_style)
        for item in segments
    ] == [
        ("dialogue", "pleasure", "Да... продолжай.", None, None),
        ("sound", "pleasure", "", "moan", "pleasure"),
        ("dialogue", "breathy", "Не останавливайся...", None, None),
        ("sound", "breathy", "", "pant", "breathy"),
    ]
    assert warnings == ["unknown_sound_tag_removed:unknown", "malformed_sound_tag_removed"]
    assert all("[sound:" not in item.text.lower() for item in segments)


def test_adjacent_same_style_dialogue_merges_but_performance_boundaries_remain():
    merged = parse_emotion_script('[voice:happy] "Да." [voice:happy] "Конечно!"')
    assert [(item.kind, item.style, item.text) for item in merged] == [
        ("dialogue", "happy", "Да. Конечно!"),
    ]

    separated = parse_emotion_script(
        '[voice:happy] "Да." [sound:giggle] [voice:happy] "Конечно!" [voice:sad] "Но потом."'
    )
    assert [(item.kind, item.style, item.text, item.sound_type) for item in separated] == [
        ("dialogue", "happy", "Да.", None),
        ("sound", "happy", "", "giggle"),
        ("dialogue", "happy", "Конечно!", None),
        ("dialogue", "sad", "Но потом.", None),
    ]


@pytest.mark.parametrize(
    "text,style",
    [
        ('[voice:pleasure] "Тест."', "pleasure"),
        ('[voice:pleasure]\n"Тест."', "pleasure"),
        ('[voice:intimate]    "Тест."', "intimate"),
    ],
)
def test_new_styles_apply_only_to_the_next_complete_quote(text, style):
    assert parsed(text) == [("dialogue", style, "Тест.")]


def test_pleasure_and_intimate_reset_to_neutral_narration():
    text = (
        'Она улыбнулась. [voice:pleasure] "М-м... хорошо." '
        'Она немного приблизилась. '
        '[voice:intimate] "Я скажу тебе это только один раз."'
    )
    assert parsed(text) == [
        ("narration", "neutral", "Она улыбнулась."),
        ("dialogue", "pleasure", "М-м... хорошо."),
        ("narration", "neutral", "Она немного приблизилась."),
        ("dialogue", "intimate", "Я скажу тебе это только один раз."),
    ]


def test_invalid_narration_tag_is_removed_and_neutral():
    segments, warnings = parse_emotion_script_detailed("[voice:happy] Она улыбнулась.")
    assert [(item.kind, item.style, item.text) for item in segments] == [
        ("narration", "neutral", "Она улыбнулась.")
    ]
    assert warnings == ["voice_tag_ignored_no_following_quoted_dialogue"]
    assert "voice:" not in segments[0].text


def test_unknown_and_malformed_tags_are_neutral_and_never_spoken():
    text = '[voice:confused] "Что?" [voice: happy] "Правда?" [voice:] narration'
    segments, warnings = parse_emotion_script_detailed(text)
    assert [(item.kind, item.style, item.text) for item in segments] == [
        ("dialogue", "neutral", "Что? Правда?"),
        ("narration", "neutral", "narration"),
    ]
    assert "unknown_voice_tag_neutral_fallback" in warnings
    assert "malformed_voice_tag_neutral_fallback" in warnings
    assert "voice_tag_ignored_no_following_quoted_dialogue" in warnings
    assert "voice:" not in " ".join(item.text for item in segments).lower()
    assert "voice:" not in strip_voice_tags(text).lower()


def test_multiple_tags_are_deterministic_and_last_tag_wins():
    segments, warnings = parse_emotion_script_detailed(
        '[voice:happy] [voice:angry] "Последний тег определяет подачу."'
    )
    assert segments[0].style == "angry"
    assert warnings == ["multiple_voice_tags_last_wins"]


def test_empty_dialogue_and_unclosed_quote_do_not_crash():
    empty, empty_warnings = parse_emotion_script_detailed('[voice:happy] "" После.')
    assert [(item.kind, item.style, item.text) for item in empty] == [
        ("narration", "neutral", "После.")
    ]
    assert "empty_dialogue_ignored" in empty_warnings

    unclosed, unclosed_warnings = parse_emotion_script_detailed(
        '[voice:angry] "Незакрытая реплика'
    )
    assert [(item.kind, item.style, item.text) for item in unclosed] == [
        ("narration", "neutral", "Незакрытая реплика")
    ]
    assert "unclosed_dialogue_treated_as_neutral" in unclosed_warnings
    assert "voice_tag_ignored_no_following_quoted_dialogue" in unclosed_warnings


def test_empty_text_has_no_segments():
    assert parse_emotion_script("  \n ") == []


def _make_config(tmp_path: Path) -> AppConfig:
    data = deepcopy(load_config().data)
    data["model"]["backend"] = "mock"
    data["resources"]["mode"] = "cpu"
    data["voices"]["library_dir"] = str(tmp_path / "voices")
    data["voices"]["fallback_profile"] = "clone:test_ru_router_neutral"
    return AppConfig(data, tmp_path / "test.yaml")


def _add_profile(
    service: TTSService,
    tmp_path: Path,
    style: str,
    character: str = "TestRuRouter",
    prefix: str = "test_ru_router",
) -> str:
    source = tmp_path / f"{prefix}-{style}.wav"
    rate = 24000
    sf.write(source, 0.1 * np.sin(2 * np.pi * 220 * np.arange(rate * 2) / rate), rate)
    display = f"{prefix}_{style}"
    profile, _ = service.library.create(
        source,
        {
            "character": character,
            "profile_id": display,
            "display_name": display,
            "style": style,
            "ref_text": "Точный тестовый текст.",
        },
    )
    return profile.voice_id


def _add_sound_profile(
    service: TTSService,
    tmp_path: Path,
    sound_type: str,
    character: str,
    prefix: str,
) -> str:
    source = tmp_path / f"{prefix}-{sound_type}.wav"
    rate = 24000
    sf.write(source, 0.1 * np.sin(2 * np.pi * 330 * np.arange(rate * 2) / rate), rate)
    profile, _ = service.library.create(
        source,
        {
            "character": character,
            "profile_id": f"{prefix}_{sound_type}",
            "display_name": f"{prefix}_{sound_type}",
            "style": "neutral",
            "emotion_enabled": False,
            "sound_enabled": True,
            "sounds": [sound_type],
            "ref_text": "Точный тестовый звук.",
            "language": "English",
        },
    )
    return profile.voice_id


def _request(text: str, voice: str) -> SimpleNamespace:
    return SimpleNamespace(
        input=text,
        preprocessing_mode="all",
        voice=voice,
        speed=1.0,
        response_format="wav",
    )


def test_service_uses_neutral_narration_quote_scope_and_clean_worker_text(tmp_path, monkeypatch):
    service = TTSService(_make_config(tmp_path))
    neutral = _add_profile(service, tmp_path, "neutral")
    happy = _add_profile(service, tmp_path, "happy")
    tense = _add_profile(service, tmp_path, "tense")
    whisper = _add_profile(service, tmp_path, "whisper")
    captured: list[tuple[str, str]] = []
    original = service.worker.synthesize

    def recording_synthesize(text, profile, language):
        captured.append((text, profile.voice_id))
        return original(text, profile, language)

    monkeypatch.setattr(service.worker, "synthesize", recording_synthesize)
    request = _request(
        (
            'Она остановилась. [voice:tense] "Ты слышал?" '
            'Она замерла. [voice:whisper] "Тише." '
            '[voice:soft] "Для soft-профиля используется neutral."'
        ),
        happy,
    )
    payload, media_type, metadata = asyncio.run(service.synthesize(request))

    assert media_type == "audio/wav"
    assert payload.startswith(b"RIFF")
    assert metadata["styles"] == ["neutral", "tense", "neutral", "whisper", "soft"]
    assert metadata["segment_types"] == ["narration", "dialogue", "narration", "dialogue", "dialogue"]
    assert metadata["voices"] == [neutral, tense, neutral, whisper, neutral]
    assert [voice for _, voice in captured] == metadata["generation_voices"]
    assert [item["voice"] for item in metadata["generation"]] == metadata["generation_voices"]
    assert all("[voice:" not in text.lower() for text, _ in captured)
    assert all('"' not in text for text, _ in captured)
    assert metadata["router_warnings"] == []
    assert metadata["duration_seconds"] > 0


def test_missing_emotion_dynamically_falls_back_then_uses_new_profile(tmp_path, monkeypatch):
    service = TTSService(_make_config(tmp_path))
    neutral = _add_profile(service, tmp_path, "neutral")
    selected: list[str] = []
    original = service.worker.synthesize

    def recording_synthesize(text, profile, language):
        selected.append(profile.voice_id)
        return original(text, profile, language)

    monkeypatch.setattr(service.worker, "synthesize", recording_synthesize)
    asyncio.run(service.synthesize(_request('[voice:happy] "Привет!"', neutral)))
    assert selected[-1] == neutral

    happy = _add_profile(service, tmp_path, "happy")
    asyncio.run(service.synthesize(_request('[voice:happy] "Привет!"', neutral)))
    assert selected[-1] == happy


def test_missing_sound_is_skipped_while_surrounding_speech_is_returned(tmp_path, monkeypatch):
    service = TTSService(_make_config(tmp_path))
    _add_profile(service, tmp_path, "neutral")
    pleasure = _add_profile(service, tmp_path, "pleasure")
    captured: list[tuple[str, str]] = []
    original = service.worker.synthesize

    def recording_synthesize(text, profile, language):
        captured.append((text, profile.voice_id))
        return original(text, profile, language)

    monkeypatch.setattr(service.worker, "synthesize", recording_synthesize)
    payload, media_type, metadata = asyncio.run(
        service.synthesize(
            _request(
                '[voice:pleasure] "До звука." [sound:moan] [voice:pleasure] "После звука."',
                pleasure,
            )
        )
    )

    assert media_type == "audio/wav"
    assert payload.startswith(b"RIFF")
    assert [text for text, _ in captured] == ["До звука.", "После звука."]
    assert [voice for _, voice in captured] == [pleasure, pleasure]
    assert metadata["segments"] == 2
    assert metadata["segment_types"] == ["dialogue", "sound", "dialogue"]
    assert metadata["router_warnings"] == ["missing_sound_profile:moan"]
    assert all("sound:" not in text.lower() for text, _ in captured)


def test_only_unavailable_sound_returns_clear_api_error(tmp_path):
    service = TTSService(_make_config(tmp_path))
    neutral = _add_profile(service, tmp_path, "neutral")
    with TestClient(create_app(config=service.config)) as client:
        response = client.post(
            "/v1/audio/speech",
            json={"voice": neutral, "input": "[sound:moan]", "response_format": "wav"},
        )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert "ни одного аудиосегмента" in body["detail"]


def test_new_styles_fall_back_then_are_selected_after_reload(tmp_path, monkeypatch):
    service = TTSService(_make_config(tmp_path))
    neutral = _add_profile(service, tmp_path, "neutral")
    captured: list[tuple[str, str]] = []
    original = service.worker.synthesize

    def recording_synthesize(text, profile, language):
        captured.append((text, profile.voice_id))
        return original(text, profile, language)

    monkeypatch.setattr(service.worker, "synthesize", recording_synthesize)
    text = '[voice:pleasure] "Тест один." [voice:intimate] "Тест два."'
    _, _, first_metadata = asyncio.run(service.synthesize(_request(text, neutral)))
    assert first_metadata["styles"] == ["pleasure", "intimate"]
    assert [voice for _, voice in captured] == [neutral, neutral]
    assert all("[voice:" not in spoken.lower() for spoken, _ in captured)

    pleasure = _add_profile(service, tmp_path, "pleasure")
    intimate = _add_profile(service, tmp_path, "intimate")
    service.library.reload()
    captured.clear()
    _, _, second_metadata = asyncio.run(service.synthesize(_request(text, neutral)))
    assert second_metadata["styles"] == ["pleasure", "intimate"]
    assert [voice for _, voice in captured] == [pleasure, intimate]
    assert all("[voice:" not in spoken.lower() for spoken, _ in captured)


def test_emotional_request_voice_still_uses_family_neutral_for_narration(tmp_path, monkeypatch):
    service = TTSService(_make_config(tmp_path))
    neutral = _add_profile(service, tmp_path, "neutral")
    happy = _add_profile(service, tmp_path, "happy")
    selected: list[str] = []
    original = service.worker.synthesize

    def recording_synthesize(text, profile, language):
        selected.append(profile.voice_id)
        return original(text, profile, language)

    monkeypatch.setattr(service.worker, "synthesize", recording_synthesize)
    asyncio.run(service.synthesize(_request('Описание. "Обычная реплика."', happy)))
    assert selected == [neutral, neutral]


def test_missing_family_neutral_uses_configured_safe_neutral(tmp_path, monkeypatch):
    service = TTSService(_make_config(tmp_path))
    safe = _add_profile(service, tmp_path, "neutral")
    orphan_happy = _add_profile(
        service,
        tmp_path,
        "happy",
        character="OrphanFamily",
        prefix="orphan",
    )
    selected: list[str] = []
    original = service.worker.synthesize

    def recording_synthesize(text, profile, language):
        selected.append(profile.voice_id)
        return original(text, profile, language)

    monkeypatch.setattr(service.worker, "synthesize", recording_synthesize)
    asyncio.run(service.synthesize(_request('Нейтральное описание.', orphan_happy)))
    assert selected == [safe]


def test_fallback_family_uses_the_same_character_for_speech_and_sound(tmp_path, monkeypatch):
    service = TTSService(_make_config(tmp_path))
    safe = _add_profile(service, tmp_path, "neutral")
    safe_sound = _add_sound_profile(service, tmp_path, "laugh", "TestRuRouter", "safe")
    orphan_happy = _add_profile(
        service,
        tmp_path,
        "happy",
        character="OrphanFamily",
        prefix="orphan",
    )
    orphan_sound = _add_sound_profile(service, tmp_path, "laugh", "OrphanFamily", "orphan")
    selected: list[tuple[str, str]] = []
    original = service.worker.synthesize

    def recording_synthesize(text, profile, language):
        selected.append((profile.voice_id, language))
        return original(text, profile, language)

    monkeypatch.setattr(service.worker, "synthesize", recording_synthesize)
    asyncio.run(service.synthesize(_request("Описание. [sound:laugh]", orphan_happy)))
    assert selected == [(safe, "Russian"), (safe_sound, "Russian")]
    assert all(voice != orphan_sound for voice, _ in selected)


def test_only_service_tags_produce_clear_api_error(tmp_path):
    service = TTSService(_make_config(tmp_path))
    _add_profile(service, tmp_path, "neutral")
    with TestClient(create_app(config=service.config)) as client:
        response = client.post(
            "/v1/audio/speech",
            json={"voice": "clone:test_ru_router_neutral", "input": "[voice:happy]", "response_format": "wav"},
        )
    assert response.status_code == 422
    assert "произносимого текста" in response.text


def test_empty_api_input_is_rejected(tmp_path):
    with TestClient(create_app(config=_make_config(tmp_path))) as client:
        response = client.post(
            "/v1/audio/speech",
            json={"voice": "missing", "input": "", "response_format": "wav"},
        )
    assert response.status_code == 422
