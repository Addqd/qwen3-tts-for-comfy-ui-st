from copy import deepcopy
import base64
import json
from pathlib import Path
import shutil

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from qwen3_tts_st.app import create_app
from qwen3_tts_st.config import AppConfig, load_config


def make_test_config(tmp_path: Path) -> AppConfig:
    base = load_config().data
    data = deepcopy(base)
    data["model"]["backend"] = "mock"
    data["resources"]["mode"] = "cpu"
    data["voices"]["library_dir"] = str(tmp_path / "voices")
    data["voices"]["fallback_profile"] = "clone:TestNeutral"
    data["runtime"]["settings_file"] = str(tmp_path / "runtime-settings.json")
    folder = tmp_path / "voices" / "profiles" / "test" / "neutral"
    folder.mkdir(parents=True)
    sr = 24000
    sf.write(folder / "reference.wav", np.sin(2 * np.pi * 200 * np.arange(sr * 2) / sr) * 0.1, sr)
    (folder / "metadata.json").write_text(json.dumps({
        "character": "Test", "profile_id": "test_neutral", "display_name": "TestNeutral", "style": "neutral",
        "reference_audio": "reference.wav", "ref_text": "Раз, два, три.", "language": "Russian", "clone_mode": "icl"
    }, ensure_ascii=False), encoding="utf-8")
    return AppConfig(data, tmp_path / "test.yaml")


def test_endpoints_and_unicode_wav(tmp_path):
    with TestClient(create_app(config=make_test_config(tmp_path))) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["default_model"] == "tts-1-ru"
        assert health.json()["available_models"] == [
            "tts-1-ru",
            "tts-1-ru-fast",
            "tts-1-ru-quality",
            "tts-1-ru-fast-tuned",
            "tts-1-ru-quality-tuned",
        ]
        assert client.get("/v1/models").status_code == 200
        assert client.get("/v1/voices").json()["data"][0]["display_name"] == "TestNeutral"
        response = client.post("/v1/audio/speech", json={
            "model": "tts-1-ru", "voice": "clone:TestNeutral", "input": "[voice:happy] Ах, русский Юникод работает!", "response_format": "wav", "speed": 1.0
        })
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("audio/wav")
        assert response.content[:4] == b"RIFF"
        assert b"voice:happy" not in response.content


def test_model_catalog_and_request_level_quality_controls(tmp_path):
    with TestClient(create_app(config=make_test_config(tmp_path))) as client:
        catalog = client.get("/v1/models").json()["data"]
        assert [item["id"] for item in catalog] == [
            "tts-1-ru",
            "tts-1-ru-fast",
            "tts-1-ru-quality",
            "tts-1-ru-fast-tuned",
            "tts-1-ru-quality-tuned",
        ]
        response = client.post(
            "/v1/audio/speech",
            json={
                "model": "tts-1-ru-quality",
                "voice": "clone:TestNeutral",
                "input": "Qwen готов на 25% к 12:30.",
                "response_format": "wav",
                "generation_preset": "stable_russian",
                "russian_normalization": "full",
                "pronunciation_overrides": {"Qwen": "куэн"},
            },
        )
        assert response.status_code == 200, response.text
        assert response.headers["x-tts-resolved-model"] == "qwen3-tts-1.7b"
        assert response.headers["x-tts-generation-preset"] == "stable_russian"
        assert response.headers["x-tts-russian-normalization"] == "full"
        metrics = client.get("/metrics").json()["last"]
        assert metrics["resolved_hf_id"].endswith("1.7B-Base")
        assert metrics["model_action"] == "switched"
        assert metrics["pronunciation_replacements"] == 1
        assert metrics["generation"][0]["generation_kwargs"]["temperature"] == 0.75


def test_runtime_settings_accept_tuned_model_ids(tmp_path):
    with TestClient(create_app(config=make_test_config(tmp_path))) as client:
        settings = client.get("/admin/runtime-settings").json()["settings"]
        for model_id in ("tts-1-ru-fast-tuned", "tts-1-ru-quality-tuned"):
            response = client.put(
                "/admin/runtime-settings",
                json={**settings, "active_model": model_id},
            )
            assert response.status_code == 200, response.text
            settings = response.json()["settings"]


def test_unknown_model_is_clear_422_without_fallback(tmp_path):
    with TestClient(create_app(config=make_test_config(tmp_path))) as client:
        response = client.post(
            "/v1/audio/speech",
            json={"model": "not-a-real-model", "voice": "clone:TestNeutral", "input": "Тест."},
        )
    assert response.status_code == 422
    assert "not-a-real-model" in response.text


def test_sillytavern_mp3_shape(tmp_path):
    with TestClient(create_app(config=make_test_config(tmp_path))) as client:
        response = client.post("/v1/audio/speech", json={
            "model": "tts-1-ru", "voice": "TestNeutral", "input": "Проверка SillyTavern.", "response_format": "mp3", "speed": 1
        })
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("audio/mpeg")
        assert response.content[:3] in {b"ID3", b"\xff\xfb", b"\xff\xf3"}
        metrics = client.get("/metrics").json()["last"]
        assert metrics["generation_preset"] == "stable_russian"
        assert metrics["russian_normalization"] == "full"


def test_explicit_default_and_off_override_integration_compatible_defaults(tmp_path):
    with TestClient(create_app(config=make_test_config(tmp_path))) as client:
        response = client.post(
            "/v1/audio/speech",
            json={
                "model": "tts-1-ru",
                "voice": "TestNeutral",
                "input": "Тест 12:01.",
                "generation_preset": "default",
                "russian_normalization": "off",
            },
        )
        assert response.status_code == 200, response.text
        metrics = client.get("/metrics").json()["last"]
        assert metrics["generation_preset"] == "default"
        assert metrics["russian_normalization"] == "off"


def test_saved_runtime_defaults_drive_generic_requests_and_explicit_model_wins(tmp_path):
    config = make_test_config(tmp_path)
    with TestClient(create_app(config=config)) as client:
        saved = client.put(
            "/admin/runtime-settings",
            json={
                "active_model": "tts-1-ru-quality",
                "generation_preset": "stable_russian",
                "russian_normalization": "full",
                "multilingual_mode": "auto",
                "chunking_mode": "semantic",
                "leading_silence_ms": 100,
                "trailing_silence_ms": 150,
                "pronunciation_defaults": {"Qwen": "куэн"},
            },
        )
        assert saved.status_code == 200, saved.text

    assert (tmp_path / "runtime-settings.json").is_file()
    with TestClient(create_app(config=config)) as client:
        generic = client.post(
            "/v1/audio/speech",
            json={"model": "tts-1-ru", "voice": "TestNeutral", "input": "Она открыла Visual Studio Code."},
        )
        assert generic.status_code == 200, generic.text
        metrics = client.get("/metrics").json()["last"]
        assert metrics["effective_model"] == "tts-1-ru-quality"
        assert metrics["resolved_model"] == "qwen3-tts-1.7b"
        assert metrics["generation_preset"] == "stable_russian"
        assert metrics["russian_normalization"] == "full"
        assert metrics["chunking_mode"] == "semantic"
        assert metrics["languages"] == ["Russian", "English"]
        assert metrics["leading_silence_ms"] == 100
        assert metrics["trailing_silence_ms"] == 150

        explicit = client.post(
            "/v1/audio/speech",
            json={"model": "tts-1-ru-fast", "voice": "TestNeutral", "input": "Тест."},
        )
        assert explicit.status_code == 200, explicit.text
        assert client.get("/metrics").json()["last"]["resolved_model"] == "qwen3-tts-0.6b"


def test_language_specific_normalization_keeps_english_span_native(tmp_path):
    config = make_test_config(tmp_path)
    with TestClient(create_app(config=config)) as client:
        calls = []
        original = client.app.state.tts.worker.synthesize

        def recording(text, profile, language="Russian", generation_kwargs=None):
            calls.append((text, language))
            return original(text, profile, language, generation_kwargs)

        client.app.state.tts.worker.synthesize = recording
        response = client.post(
            "/v1/audio/speech",
            json={
                "voice": "TestNeutral",
                "input": "Version 2 и версия 2.",
                "russian_normalization": "full",
                "multilingual_mode": "auto",
            },
        )
        assert response.status_code == 200, response.text
    assert calls == [("Version 2", "English"), ("и версия два.", "Russian")]


def test_api_rejects_removed_legacy_chunking_mode(tmp_path):
    with TestClient(create_app(config=make_test_config(tmp_path))) as client:
        settings = client.get("/admin/runtime-settings").json()["settings"]
        settings["chunking_mode"] = "legacy"
        assert client.put("/admin/runtime-settings", json=settings).status_code == 422
        assert client.post(
            "/v1/audio/speech",
            json={"voice": "TestNeutral", "input": "Short text.", "chunking_mode": "legacy"},
        ).status_code == 422


def test_clone_rejects_non_wav_and_requires_consent(tmp_path):
    import base64

    payload = {
        "reference_audio_base64": base64.b64encode(b"not a wav").decode(),
        "ref_text": "Точный текст.", "profile_name": "Bad", "character_name": "Test",
    }
    with TestClient(create_app(config=make_test_config(tmp_path))) as client:
        assert client.post("/v1/audio/voice-clone", json=payload).status_code == 400
        payload["consent_confirmed"] = True
        response = client.post("/v1/audio/voice-clone", json=payload)
        assert response.status_code == 422
        assert "RIFF" in response.text


def test_metrics_reload_and_fallback(tmp_path):
    with TestClient(create_app(config=make_test_config(tmp_path))) as client:
        response = client.post("/v1/audio/speech", json={
            "model": "tts-1-ru", "voice": "missing", "input": 'Первое. [voice:angry] "Второе!"',
            "response_format": "wav", "speed": 1.0,
        })
        assert response.status_code == 200
        assert int(response.headers["x-tts-segments"]) == 2
        metrics = client.get("/metrics").json()
        assert metrics["completed"] == 1
        assert metrics["last"]["styles"] == ["neutral", "angry"]
        assert metrics["last"]["segment_types"] == ["narration", "dialogue"]
        assert metrics["last"]["voices"] == ["clone:TestNeutral", "clone:TestNeutral"]
        assert client.post("/admin/reload-voices").status_code == 200


def test_long_russian_text_is_chunked_and_json_is_utf8(tmp_path):
    config = make_test_config(tmp_path)
    config.data["chunking"]["max_chars"] = 24
    with TestClient(create_app(config=config)) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.headers["content-type"].startswith("application/json; charset=utf-8")
        response = client.post("/v1/audio/speech", json={
            "model": "tts-1-ru",
            "voice": "clone:TestNeutral",
            "input": "Первое длинное предложение. Второе длинное предложение!",
            "response_format": "wav",
            "speed": 1.0,
        })
        assert response.status_code == 200, response.text
        assert int(response.headers["x-tts-segments"]) >= 2


def test_clone_rejects_corrupt_riff_payload(tmp_path):
    import base64

    payload = {
        "reference_audio_base64": base64.b64encode(b"RIFF\x04\x00\x00\x00WAVEjunk").decode(),
        "ref_text": "Точный текст.",
        "profile_name": "Broken",
        "character_name": "Test",
        "consent_confirmed": True,
    }
    with TestClient(create_app(config=make_test_config(tmp_path))) as client:
        response = client.post("/v1/audio/voice-clone", json=payload)
        assert response.status_code == 422
        assert "аудио" in response.text


def test_full_message_quote_scope_unknown_tag_and_no_service_leakage(tmp_path):
    with TestClient(create_app(config=make_test_config(tmp_path))) as client:
        response = client.post(
            "/v1/audio/speech",
            json={
                "model": "tts-1-ru",
                "voice": "clone:TestNeutral",
                "input": (
                    'Она остановилась. [voice:happy] "Ты пришёл!" '
                    'Она улыбнулась. [voice:confused] "Что?"'
                ),
                "response_format": "wav",
                "speed": 1.0,
            },
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("audio/wav")
        assert response.content.startswith(b"RIFF")
        metrics = client.get("/metrics").json()["last"]
    assert metrics["styles"] == ["neutral", "happy", "neutral", "neutral"]
    assert metrics["segment_types"] == ["narration", "dialogue", "narration", "dialogue"]
    assert metrics["voices"] == ["clone:TestNeutral"] * 4
    assert metrics["router_warnings"] == ["unknown_voice_tag_neutral_fallback"]


def test_invalid_voice_without_safe_fallback_is_clear_422(tmp_path):
    config = make_test_config(tmp_path)
    config.data["voices"]["fallback_profile"] = ""
    with TestClient(create_app(config=config)) as client:
        response = client.post(
            "/v1/audio/speech",
            json={"voice": "missing", "input": "Текст.", "response_format": "wav"},
        )
    assert response.status_code == 422
    assert "голосовой профиль не найден" in response.text


def test_malformed_json_and_missing_fields_are_json_422(tmp_path):
    with TestClient(create_app(config=make_test_config(tmp_path))) as client:
        malformed = client.post(
            "/v1/audio/speech",
            content=b'{"voice":',
            headers={"Content-Type": "application/json"},
        )
        missing_voice = client.post("/v1/audio/speech", json={"input": "Текст."})
    assert malformed.status_code == 422
    assert malformed.headers["content-type"].startswith("application/json")
    assert missing_voice.status_code == 422


def test_voice_clone_accepts_pleasure_and_intimate_styles(tmp_path):
    config = make_test_config(tmp_path)
    reference = config.path("voices.library_dir", "voice_library") / "profiles" / "test" / "neutral" / "reference.wav"
    encoded = base64.b64encode(reference.read_bytes()).decode("ascii")
    with TestClient(create_app(config=config)) as client:
        for style in ("pleasure", "intimate"):
            response = client.post(
                "/v1/audio/voice-clone",
                json={
                    "reference_audio_base64": encoded,
                    "ref_text": "Раз, два, три.",
                    "profile_name": f"Test{style.title()}",
                    "character_name": "Test",
                    "style": style,
                    "clone_mode": "icl",
                    "consent_confirmed": True,
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["metadata"]["style"] == style


def test_v1_voices_ignores_legacy_profile_backup_directories(tmp_path):
    config = make_test_config(tmp_path)
    active = config.path("voices.library_dir", "voice_library") / "profiles" / "test" / "neutral"
    legacy = active.parent / "neutral.backup-20260808-120000"
    shutil.copytree(active, legacy)
    metadata_path = legacy / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.update({"profile_id": "backup_neutral", "display_name": "BackupNeutral"})
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

    with TestClient(create_app(config=config)) as client:
        voices = client.get("/v1/voices").json()["data"]
    assert [item["display_name"] for item in voices] == ["TestNeutral"]
