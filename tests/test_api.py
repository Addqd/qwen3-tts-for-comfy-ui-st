from copy import deepcopy
import json
from pathlib import Path

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
        assert client.get("/health").status_code == 200
        assert client.get("/v1/models").status_code == 200
        assert client.get("/v1/voices").json()["data"][0]["display_name"] == "TestNeutral"
        response = client.post("/v1/audio/speech", json={
            "model": "tts-1-ru", "voice": "clone:TestNeutral", "input": "[voice:happy] Ах, русский Юникод работает!", "response_format": "wav", "speed": 1.0
        })
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("audio/wav")
        assert response.content[:4] == b"RIFF"
        assert b"voice:happy" not in response.content


def test_sillytavern_mp3_shape(tmp_path):
    with TestClient(create_app(config=make_test_config(tmp_path))) as client:
        response = client.post("/v1/audio/speech", json={
            "model": "tts-1-ru", "voice": "TestNeutral", "input": "Проверка SillyTavern.", "response_format": "mp3", "speed": 1
        })
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("audio/mpeg")
        assert response.content[:3] in {b"ID3", b"\xff\xfb", b"\xff\xf3"}


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
            "model": "tts-1-ru", "voice": "missing", "input": "Первое. [voice:angry] Второе!",
            "response_format": "wav", "speed": 1.0,
        })
        assert response.status_code == 200
        assert int(response.headers["x-tts-segments"]) == 2
        assert client.get("/metrics").json()["completed"] == 1
        assert client.post("/admin/reload-voices").status_code == 200
