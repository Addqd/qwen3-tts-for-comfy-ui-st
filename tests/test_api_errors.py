from __future__ import annotations

import base64
import io
from pathlib import Path
import wave

import httpx
from fastapi.testclient import TestClient

import qwen3_tts_st.app as app_module
from qwen3_tts_st.config import load_config


ROOT = Path(__file__).resolve().parents[1]


class FakeLibrary:
    clone_error: Exception | None = None

    def list(self):
        return []

    async def create(self, *_args, **_kwargs):
        raise self.clone_error or RuntimeError("unexpected")

    def reload(self):
        return 0

    async def register_all(self, _client):
        return 0


class FakeService:
    speech_error: Exception | None = None

    def __init__(self, _config):
        self.library = FakeLibrary()
        self.client = object()

    async def startup(self):
        return None

    async def shutdown(self):
        return None

    async def synthesize(self, _request):
        raise self.speech_error or RuntimeError("unexpected")


def client(monkeypatch) -> tuple[TestClient, FakeService]:
    service = FakeService(None)
    monkeypatch.setattr(app_module, "TTSService", lambda _config: service)
    config = load_config(ROOT / "config" / "config.example.yaml")
    return TestClient(app_module.create_app(config=config)), service


def test_invalid_speech_input_is_422_and_connectivity_is_503(monkeypatch):
    test_client, service = client(monkeypatch)
    with test_client:
        service.speech_error = ValueError("invalid input")
        response = test_client.post("/v1/audio/speech", json={"input": "тест"})
        assert response.status_code == 422
        assert response.json()["detail"] == "invalid input"

        service.speech_error = httpx.ConnectError("engine down")
        response = test_client.post("/v1/audio/speech", json={"input": "тест"})
        assert response.status_code == 503
        assert "engine down" in response.json()["detail"]


def test_clone_connectivity_failure_is_503(monkeypatch):
    test_client, service = client(monkeypatch)
    service.library.clone_error = httpx.ConnectError("registration unavailable")
    payload = io.BytesIO()
    with wave.open(payload, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x00\x00" * 32)
    wav = base64.b64encode(payload.getvalue()).decode("ascii")
    with test_client:
        response = test_client.post("/v1/audio/voice-clone", json={
            "reference_audio_base64": wav,
            "ref_text": "точная расшифровка",
            "profile_name": "test",
            "character_name": "Test",
            "language": "Russian",
            "overwrite": False,
        })
    assert response.status_code == 503
    assert "registration unavailable" in response.json()["detail"]
