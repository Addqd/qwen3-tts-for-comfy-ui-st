from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import io
import json
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

import httpx

from .normalization import apply_pronunciation, merge_pronunciation, normalize_russian_text
from .preprocess import preprocess, split_long_text
from .runtime_settings import RuntimeSettingsStore
from .silero_preprocessing import SileroPreprocessingError, SileroPreprocessor
from .voices import VoiceLibrary


class TTSService:
    def __init__(self, config: Any):
        self.config = config
        self.talker_model, self.codec_model = config.qwentts_models()
        self.settings = RuntimeSettingsStore(config)
        self.silero = SileroPreprocessor(config.path("silero.provision_state", "runtime/silero-provisioned.json"))
        self.library = VoiceLibrary(config.path("voices.library_dir", "voice_library"), config)
        self.default_voice = str(config.get("voices.default_voice", "clone:test_ru_dima_neutral"))
        self.qwentts_url = f"http://127.0.0.1:{int(config.get('qwentts.port', 8030))}"
        self.qwentts_state_path = config.path("qwentts.state_file", "runtime/qwentts.json")
        self.client = httpx.AsyncClient(base_url=self.qwentts_url, timeout=float(config.get("qwentts.request_timeout_seconds", 900)))
        self.engine_revision = str(config.qwentts_manifest()["upstream"]["revision"])
        self.model_id = config.qwentts_model_id()
        self.language = config.qwentts_language()
        self.lock = asyncio.Lock()
        self.lock_timeout_seconds = float(config.get("qwentts.queue_timeout_seconds", 30))
        self.started_at = time.time()
        self.completed = 0
        self.failed = 0
        self.last_metrics: dict[str, Any] = {}

    async def startup(self) -> None:
        health = await self.client.get("/health")
        health.raise_for_status()
        await self.library.register_all(self.client, required_voice=self.default_voice)
        try:
            self.library.resolve(self.default_voice)
        except (KeyError, FileNotFoundError, RuntimeError) as exc:
            raise RuntimeError(f"Required default voice is unavailable: {self.default_voice}") from exc

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
        current = self.settings.current()
        return {
            "status": "ok" if qwentts_ready else "degraded",
            "engine": "qwentts.cpp",
            "engine_revision": self.engine_revision,
            "model": self.model_id,
            "model_variant": "bf16",
            "model_file": self.talker_model.name,
            "device": device,
            "qwentts_ready": qwentts_ready,
            "qwentts_url": self.qwentts_url,
            "default_voice": self.default_voice,
            "voice_count": len(self.library.list()),
            "runtime_settings": current,
            "auto_stress": current["auto_stress"],
            "stress_format": current["stress_format"],
            "text_enhancement": current["text_enhancement"],
            **self.silero.diagnostics(),
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
    def _stitch_wav(parts: list[bytes]) -> bytes:
        if not parts:
            raise RuntimeError("qwentts returned no WAV chunks")
        expected: tuple[int, int, int, str] | None = None
        frames: list[bytes] = []
        for payload in parts:
            try:
                with wave.open(io.BytesIO(payload), "rb") as source:
                    current = (
                        source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()
                    )
                    if expected is None:
                        expected = current
                    elif current != expected:
                        raise RuntimeError("qwentts returned incompatible WAV chunk formats")
                    frames.append(source.readframes(source.getnframes()))
            except (EOFError, wave.Error) as exc:
                raise RuntimeError("qwentts returned invalid WAV chunk audio") from exc
        assert expected is not None
        output = io.BytesIO()
        with wave.open(output, "wb") as target:
            target.setnchannels(expected[0])
            target.setsampwidth(expected[1])
            target.setframerate(expected[2])
            target.setcomptype(expected[3], "not compressed")
            for chunk_frames in frames:
                target.writeframes(chunk_frames)
        return output.getvalue()

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

    async def _prepare_text(self, request: Any, current: dict[str, Any]) -> tuple[str, int, str, float, float]:
        prepared = preprocess(request.input, dict(self.config.get("preprocessing", {}) or {}))
        pronunciation = merge_pronunciation(current["pronunciation_defaults"], request.pronunciation_overrides)
        _, expected_replacements = apply_pronunciation(prepared, pronunciation)
        automatic_enabled = current["auto_stress"] != "off" or current["text_enhancement"] != "off"
        stress_seconds = 0.0
        te_seconds = 0.0
        if automatic_enabled:
            prepared, timings = await asyncio.to_thread(
                self.silero.process,
                prepared,
                current["text_enhancement"],
                current["auto_stress"],
                current["stress_format"],
                list(pronunciation),
            )
            stress_seconds = timings["stress_seconds"]
            te_seconds = timings["text_enhancement_seconds"]
        prepared, replacements = apply_pronunciation(prepared, pronunciation)
        if replacements != expected_replacements:
            raise SileroPreprocessingError("Automatic preprocessing changed protected pronunciation terms")
        self.silero.record_timings(stress_seconds, te_seconds)
        normalization = request.russian_normalization or current["russian_normalization"]
        prepared = normalize_russian_text(prepared, normalization)
        if not prepared:
            raise ValueError("No pronounceable text remains after preprocessing")
        return prepared, replacements, normalization, stress_seconds, te_seconds

    async def synthesize(self, request: Any) -> tuple[bytes, str, dict[str, Any]]:
        async with self._synthesis_slot():
            started = time.perf_counter()
            try:
                voice = request.voice or self.default_voice
                self.library.resolve(voice)
                current = self.settings.current()
                prepared, replacements, normalization, stress_seconds, te_seconds = await self._prepare_text(request, current)
                payload = {
                    "model": self.model_id,
                    "voice": voice,
                    "response_format": "wav",
                    "seed": current["seed"] if request.seed is None else request.seed,
                    "max_new_tokens": current["max_new_tokens"] if request.max_new_tokens is None else request.max_new_tokens,
                    "temperature": current["temperature"] if request.temperature is None else request.temperature,
                    "top_k": current["top_k"] if request.top_k is None else request.top_k,
                    "top_p": current["top_p"] if request.top_p is None else request.top_p,
                    "repetition_penalty": current["repetition_penalty"] if request.repetition_penalty is None else request.repetition_penalty,
                }
                chunks = split_long_text(prepared, int(self.config.get("qwentts.max_chunk_chars", 320)))
                wav_parts: list[bytes] = []
                for index, chunk in enumerate(chunks, 1):
                    response = await self.client.post("/v1/audio/speech", json={**payload, "input": chunk})
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise RuntimeError(
                            f"qwentts chunk {index}/{len(chunks)} failed: {exc.response.text}"
                        ) from exc
                    wav_parts.append(response.content)
                wav = wav_parts[0] if len(wav_parts) == 1 else self._stitch_wav(wav_parts)
                source_duration = self._wav_duration(wav)
                output, media_type = await asyncio.to_thread(self._convert, wav, request.response_format, request.speed)
                duration = source_duration / request.speed
                metadata = {
                    "duration_seconds": duration,
                    "segments": len(chunks),
                    "model": self.model_id,
                    "engine": "qwentts.cpp",
                    "voice": voice,
                    "language": self.language,
                    "russian_normalization": normalization,
                    "auto_stress": current["auto_stress"],
                    "stress_format": current["stress_format"],
                    "text_enhancement": current["text_enhancement"],
                    "stress_preprocessing_wall_seconds": round(stress_seconds, 6),
                    "text_enhancement_wall_seconds": round(te_seconds, 6),
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
        return {
            "completed": self.completed,
            "failed": self.failed,
            "silero": self.silero.diagnostics(),
            "last": self.last_metrics,
        }
