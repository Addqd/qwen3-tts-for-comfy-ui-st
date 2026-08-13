from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

import httpx

from .normalization import apply_pronunciation, merge_pronunciation, normalize_russian_text
from .preprocess import preprocess
from .runtime_settings import RuntimeSettingsStore
from .voices import VoiceLibrary


class TTSService:
    def __init__(self, config: Any):
        self.config = config
        self.model_variant, self.talker_model, self.codec_model = config.qwentts_model()
        self.settings = RuntimeSettingsStore(config)
        self.library = VoiceLibrary(config.path("voices.library_dir", "voice_library"), config)
        self.default_voice = str(config.get("voices.default_voice", "clone:test_ru_dima_neutral"))
        self.qwentts_url = f"http://127.0.0.1:{int(config.get('qwentts.port', 8030))}"
        self.qwentts_state_path = config.path("qwentts.state_file", "runtime/qwentts.json")
        self.client = httpx.AsyncClient(base_url=self.qwentts_url, timeout=float(config.get("qwentts.request_timeout_seconds", 900)))
        provenance_path = config.path("qwentts.provenance_file", "config/qwentts-runtime.json")
        try:
            self.engine_revision = str(json.loads(provenance_path.read_text(encoding="utf-8"))["upstream"]["revision"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            self.engine_revision = "unknown"
        self.lock = asyncio.Lock()
        self.lock_timeout_seconds = float(config.get("qwentts.queue_timeout_seconds", 30))
        self.started_at = time.time()
        self.completed = 0
        self.failed = 0
        self.last_metrics: dict[str, Any] = {}

    async def startup(self) -> None:
        health = await self.client.get("/health")
        health.raise_for_status()
        await self.library.register_all(self.client)
        self.library.resolve(self.default_voice)

    async def shutdown(self) -> None:
        await self.client.aclose()

    @asynccontextmanager
    async def _synthesis_slot(self):
        try:
            await asyncio.wait_for(self.lock.acquire(), timeout=self.lock_timeout_seconds)
        except TimeoutError as exc:
            raise RuntimeError("qwentts engine is busy; retry later") from exc
        try:
            yield
        finally:
            self.lock.release()

    async def health(self) -> dict[str, Any]:
        engine_health: dict[str, Any] = {}
        try:
            response = await self.client.get("/health")
            qwentts_ready = response.status_code == 200
            if qwentts_ready:
                parsed = response.json()
                if isinstance(parsed, dict):
                    engine_health = parsed
        except httpx.HTTPError:
            qwentts_ready = False
        except (TypeError, ValueError, json.JSONDecodeError):
            qwentts_ready = False
        device = None
        if qwentts_ready:
            device = engine_health.get("device") or engine_health.get("backend") or engine_health.get("talker_backend")
            if device is None:
                try:
                    state = json.loads(self.qwentts_state_path.read_text(encoding="utf-8-sig"))
                    device = state.get("verified_backend")
                except (OSError, AttributeError, TypeError, ValueError, json.JSONDecodeError):
                    device = None
        return {
            "status": "ok" if qwentts_ready else "degraded",
            "engine": "qwentts.cpp",
            "engine_revision": self.engine_revision,
            "model": "tts-1-ru",
            "model_variant": self.model_variant,
            "model_file": self.talker_model.name,
            "device": device,
            "qwentts_ready": qwentts_ready,
            "qwentts_url": self.qwentts_url,
            "default_voice": self.default_voice,
            "voice_count": len(self.library.list()),
            "runtime_settings": self.settings.current(),
            "uptime_seconds": round(time.time() - self.started_at, 1),
        }

    @staticmethod
    def _wav_duration(payload: bytes) -> float:
        try:
            with tempfile.SpooledTemporaryFile() as handle:
                handle.write(payload)
                handle.seek(0)
                with wave.open(handle, "rb") as wav:
                    return wav.getnframes() / wav.getframerate()
        except (EOFError, wave.Error) as exc:
            raise RuntimeError("qwentts returned invalid WAV audio") from exc

    @staticmethod
    def _convert(payload: bytes, response_format: str, speed: float) -> tuple[bytes, str]:
        suffixes = {"wav": "wav", "mp3": "mp3", "flac": "flac", "opus": "opus", "aac": "m4a"}
        media = {"wav": "audio/wav", "mp3": "audio/mpeg", "flac": "audio/flac", "opus": "audio/ogg", "aac": "audio/mp4"}
        if response_format not in suffixes:
            raise ValueError(f"Unsupported audio response format: {response_format}")
        if not 0.25 <= float(speed) <= 4.0:
            raise ValueError("speed must be between 0.25 and 4.0")
        if response_format == "wav" and speed == 1.0:
            return payload, "audio/wav"
        with tempfile.TemporaryDirectory(prefix="qwentts-format-") as folder:
            source = Path(folder) / "source.wav"
            output = Path(folder) / f"output.{suffixes[response_format]}"
            source.write_bytes(payload)
            command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)]
            if speed != 1.0:
                factors = []
                remaining = speed
                while remaining > 2:
                    factors.append(2.0)
                    remaining /= 2
                while remaining < 0.5:
                    factors.append(0.5)
                    remaining /= 0.5
                factors.append(remaining)
                command += ["-filter:a", ",".join(f"atempo={factor:.8g}" for factor in factors)]
            command += [str(output)]
            try:
                result = subprocess.run(command, capture_output=True, timeout=120, check=False)
            except FileNotFoundError as exc:
                raise RuntimeError("FFmpeg is required for format or speed conversion but was not found in PATH") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("FFmpeg conversion timed out") from exc
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg conversion failed: {result.stderr.decode(errors='replace')[-1000:]}")
            if not output.exists():
                raise RuntimeError("FFmpeg conversion completed without an output file")
            return output.read_bytes(), media[response_format]

    async def synthesize(self, request: Any) -> tuple[bytes, str, dict[str, Any]]:
        async with self._synthesis_slot():
            started = time.perf_counter()
            try:
                voice = request.voice or self.default_voice
                self.library.resolve(voice)
                current = self.settings.current()
                prepared = preprocess(request.input, dict(self.config.get("preprocessing", {}) or {}))
                pronunciation = merge_pronunciation(current["pronunciation_defaults"], request.pronunciation_overrides)
                prepared, replacements = apply_pronunciation(prepared, pronunciation)
                normalization = request.russian_normalization or current["russian_normalization"]
                prepared = normalize_russian_text(prepared, normalization)
                if not prepared:
                    raise ValueError("No pronounceable text remains after preprocessing")
                payload = {
                    "model": "tts-1-ru",
                    "voice": voice,
                    "input": prepared,
                    "response_format": "wav",
                    "seed": current["seed"] if request.seed is None else request.seed,
                    "max_new_tokens": current["max_new_tokens"] if request.max_new_tokens is None else request.max_new_tokens,
                    "temperature": current["temperature"] if request.temperature is None else request.temperature,
                    "top_k": current["top_k"] if request.top_k is None else request.top_k,
                    "top_p": current["top_p"] if request.top_p is None else request.top_p,
                    "repetition_penalty": current["repetition_penalty"] if request.repetition_penalty is None else request.repetition_penalty,
                }
                response = await self.client.post("/v1/audio/speech", json=payload)
                response.raise_for_status()
                wav = response.content
                source_duration = self._wav_duration(wav)
                output, media_type = await asyncio.to_thread(self._convert, wav, request.response_format, request.speed)
                duration = source_duration / request.speed
                metadata = {
                    "duration_seconds": duration,
                    "segments": 1,
                    "model": "tts-1-ru",
                    "engine": "qwentts.cpp",
                    "voice": voice,
                    "language": "Russian",
                    "russian_normalization": normalization,
                    "pronunciation_replacements": replacements,
                    "wall_seconds": round(time.perf_counter() - started, 3),
                }
                self.completed += 1
                self.last_metrics = metadata
                return output, media_type, metadata
            except Exception:
                self.failed += 1
                raise

    def metrics(self) -> dict[str, Any]:
        return {"completed": self.completed, "failed": self.failed, "last": self.last_metrics}
