from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from pathlib import Path
import tempfile
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

from .config import AppConfig, load_config
from .emotion import VoiceStyle
from .normalization import parse_pronunciation_overrides
from .service import TTSService


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


class SpeechRequest(BaseModel):
    model: str = "tts-1-ru"
    voice: str
    input: str = Field(min_length=1, max_length=20000)
    response_format: Literal["wav", "mp3", "flac", "opus", "aac"] = "wav"
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    preprocessing_mode: Literal["all", "direct_speech"] | None = None
    generation_preset: Literal["default", "stable_russian"] | None = None
    russian_normalization: Literal["off", "basic", "full"] | None = None
    pronunciation_overrides: dict[str, str] | str | None = None

    @field_validator("pronunciation_overrides")
    @classmethod
    def validate_pronunciation_overrides(cls, value):
        parse_pronunciation_overrides(value)
        return value


class CloneRequest(BaseModel):
    reference_audio_base64: str
    ref_text: str = Field(min_length=1)
    profile_name: str
    character_name: str
    style: VoiceStyle = "neutral"
    language: str = "Russian"
    clone_mode: Literal["icl", "x_vector"] = "icl"
    overwrite: bool = False
    consent_confirmed: bool = False


def create_app(config_path: str | Path | None = None, config: AppConfig | None = None) -> FastAPI:
    active_config = config or load_config(config_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.tts = TTSService(active_config)
        yield
        app.state.tts.shutdown()

    app = FastAPI(
        title="Qwen3-TTS ST",
        version="0.2.0",
        lifespan=lifespan,
        default_response_class=UTF8JSONResponse,
    )

    @app.exception_handler(Exception)
    async def unhandled_exception(_, exc: Exception):
        return UTF8JSONResponse(status_code=500, content={"error": {"type": type(exc).__name__, "message": str(exc)}})

    @app.get("/health")
    async def health():
        return app.state.tts.health()

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": app.state.tts.registry.public_models()}

    @app.get("/v1/voices")
    async def voices():
        return {"object": "list", "data": app.state.tts.library.list()}

    @app.post("/v1/audio/speech")
    async def speech(request: SpeechRequest):
        try:
            payload, media_type, metadata = await app.state.tts.synthesize(request)
        except (KeyError, FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except RuntimeError as exc:
            status = 429 if "очеред" in str(exc).lower() else 503
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return Response(
            content=payload,
            media_type=media_type,
            headers={
                "X-Audio-Duration": f"{metadata['duration_seconds']:.3f}",
                "X-TTS-Segments": str(metadata["segments"]),
                "X-TTS-Requested-Model": str(metadata["requested_model"]),
                "X-TTS-Resolved-Model": str(metadata["resolved_model"]),
                "X-TTS-Generation-Preset": str(metadata["generation_preset"]),
                "X-TTS-Russian-Normalization": str(metadata["russian_normalization"]),
            },
        )

    @app.post("/v1/audio/voice-clone")
    async def clone(request: CloneRequest):
        if not request.consent_confirmed:
            raise HTTPException(status_code=400, detail="требуется consent_confirmed=true: клонируйте только разрешённый голос")
        try:
            encoded = request.reference_audio_base64.split(",", 1)[-1]
            audio = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="reference_audio_base64 повреждён") from exc
        if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise HTTPException(status_code=422, detail="reference_audio должен быть WAV RIFF/WAVE")
        inbox = active_config.path("voices.library_dir", "voice_library") / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=inbox, suffix=".wav", delete=False) as handle:
            handle.write(audio)
            temporary = Path(handle.name)
        try:
            profile, validation = app.state.tts.library.create(
                temporary,
                {
                    "character": request.character_name,
                    "profile_id": request.profile_name,
                    "display_name": request.profile_name,
                    "style": request.style,
                    "ref_text": request.ref_text,
                    "language": request.language,
                    "clone_mode": request.clone_mode,
                },
                request.overwrite,
            )
        except (ValueError, FileExistsError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            temporary.unlink(missing_ok=True)
        return {"voice_id": profile.voice_id, "validation": validation, "metadata": profile.public()}

    @app.post("/admin/reload-voices")
    async def reload_voices():
        count = app.state.tts.library.reload()
        return {"status": "ok", "profile_count": count}

    @app.get("/metrics")
    async def metrics():
        return app.state.tts.metrics()

    return app
