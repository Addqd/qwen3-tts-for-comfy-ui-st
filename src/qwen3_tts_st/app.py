from __future__ import annotations

import base64
from contextlib import asynccontextmanager
import io
from pathlib import Path
import tempfile
from typing import Literal
import wave

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import AppConfig, load_config
from .normalization import parse_pronunciation_overrides
from .service import TTSService


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


class SpeechRequest(BaseModel):
    model: str = "tts-1-ru"
    voice: str = "clone:test_ru_dima_neutral"
    input: str = Field(min_length=1, max_length=20000)
    response_format: Literal["wav", "mp3", "flac", "opus", "aac"] = "wav"
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    russian_normalization: Literal["off", "basic", "full"] | None = None
    pronunciation_overrides: dict[str, str] | str | None = None
    seed: int | None = None
    max_new_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0)
    top_k: int | None = Field(default=None, ge=0)
    top_p: float | None = Field(default=None, gt=0, le=1)
    repetition_penalty: float | None = Field(default=None, gt=0)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        if value != "tts-1-ru":
            raise ValueError("Only the production model tts-1-ru is available")
        return value

    @field_validator("pronunciation_overrides")
    @classmethod
    def validate_pronunciation(cls, value):
        parse_pronunciation_overrides(value)
        return value


class RuntimeSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Literal["Russian"] = "Russian"
    russian_normalization: Literal["off", "basic", "full"]
    auto_stress: Literal["off", "silero"] = "silero"
    stress_format: Literal["plus", "acute", "apostrophe"] = "plus"
    text_enhancement: Literal["off", "silero"] = "off"
    pronunciation_defaults: dict[str, str] | str = Field(default_factory=dict)
    seed: int = -1
    max_new_tokens: int = Field(gt=0)
    temperature: float = Field(ge=0)
    top_k: int = Field(ge=0)
    top_p: float = Field(gt=0, le=1)
    repetition_penalty: float = Field(gt=0)


class CloneRequest(BaseModel):
    reference_audio_base64: str
    ref_text: str = Field(min_length=1)
    profile_name: str = Field(min_length=1)
    character_name: str = Field(min_length=1)
    language: Literal["Russian"] = "Russian"
    overwrite: bool = False


def create_app(config_path: str | Path | None = None, config: AppConfig | None = None) -> FastAPI:
    active_config = config or load_config(config_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service = TTSService(active_config)
        await service.startup()
        app.state.tts = service
        yield
        await service.shutdown()

    app = FastAPI(title="Qwen3-TTS ST", version="1.0.0", lifespan=lifespan, default_response_class=UTF8JSONResponse)

    @app.get("/health")
    async def health():
        return await app.state.tts.health()

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [{"id": "tts-1-ru", "object": "model", "owned_by": "local-qwentts"}]}

    @app.get("/v1/voices")
    async def voices():
        return {"object": "list", "data": app.state.tts.library.list()}

    @app.post("/v1/audio/speech")
    async def speech(request: SpeechRequest):
        try:
            payload, media_type, metadata = await app.state.tts.synthesize(request)
        except (KeyError, FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=503, detail=f"qwentts request failed: {exc.response.text}") from exc
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return Response(content=payload, media_type=media_type, headers={
            "X-Audio-Duration": f"{metadata['duration_seconds']:.3f}",
            "X-TTS-Engine": "qwentts.cpp",
            "X-TTS-Model": "tts-1-ru",
            "X-TTS-Voice": metadata["voice"],
            "X-TTS-Russian-Normalization": metadata["russian_normalization"],
        })

    @app.post("/v1/audio/voice-clone")
    async def clone(request: CloneRequest):
        try:
            audio = base64.b64decode(request.reference_audio_base64.split(",", 1)[-1], validate=True)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="reference_audio_base64 is invalid") from exc
        if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise HTTPException(status_code=422, detail="reference_audio must be WAV RIFF/WAVE")
        try:
            with wave.open(io.BytesIO(audio), "rb") as reference:
                if reference.getnframes() <= 0 or reference.getframerate() <= 0:
                    raise ValueError("empty WAV")
        except (EOFError, ValueError, wave.Error) as exc:
            raise HTTPException(status_code=422, detail="reference_audio is not a valid non-empty PCM WAV") from exc
        inbox = active_config.path("voices.library_dir", "voice_library") / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=inbox, suffix=".wav", delete=False) as handle:
            handle.write(audio)
            source = Path(handle.name)
        try:
            profile = await app.state.tts.library.create(
                source, request.profile_name, request.character_name, request.ref_text,
                request.language, request.overwrite, app.state.tts.client,
            )
        except (ValueError, FileExistsError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (FileNotFoundError, RuntimeError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        finally:
            source.unlink(missing_ok=True)
        return {"voice_id": profile.voice_id, "validation": {"valid": True}, "metadata": profile.public()}

    @app.post("/admin/reload-voices")
    async def reload_voices():
        count = app.state.tts.library.reload()
        try:
            registered = await app.state.tts.library.register_all(app.state.tts.client)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=f"qwentts registration failed: {exc}") from exc
        return {"status": "ok", "profile_count": count, "registered": registered}

    @app.get("/admin/runtime-settings")
    async def runtime_settings():
        return {"status": "ok", "settings": app.state.tts.settings.current()}

    @app.put("/admin/runtime-settings")
    async def save_runtime_settings(request: RuntimeSettingsRequest):
        try:
            settings = app.state.tts.settings.update(request.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"status": "ok", "settings": settings}

    @app.get("/metrics")
    async def metrics():
        return app.state.tts.metrics()

    return app
