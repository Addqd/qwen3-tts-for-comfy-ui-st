from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

import soundfile as sf

from .audio import change_speed, encode, stitch
from .emotion import parse_emotion_script_detailed
from .generation import generation_kwargs
from .model_manager import ModelActivation, ModelManager
from .models import ModelRegistry
from .normalization import (
    apply_pronunciation,
    merge_pronunciation_dictionaries,
    normalize_russian_text,
)
from .preprocess import preprocess, split_long_text
from .resources import snapshot
from .voices import VoiceLibrary
from .worker import MockWorker


class TTSService:
    def __init__(self, config: Any):
        self.config = config
        self.registry = ModelRegistry(config)
        self.manager = ModelManager(config, self.registry)
        self.library = VoiceLibrary(config.path("voices.library_dir", "voice_library"))
        backend = str(config.get("model.backend", "qwen")).lower()
        # Existing tests and local instrumentation access service.worker directly.
        # Production paths use the manager and remain lazy.
        self.worker = self.manager.worker_for_compatibility() if backend == "mock" else None
        self.configured_max_concurrent = max(1, int(config.get("queue.max_concurrent", 1)))
        # ModelManager owns one activation and one resident worker. Serializing
        # the full request lifecycle prevents another request from switching the
        # model between prepare() and the final synthesize_prepared() call.
        self.effective_max_concurrent = 1
        self.semaphore = asyncio.Semaphore(self.effective_max_concurrent)
        self.waiting = 0
        self.completed = 0
        self.failed = 0
        self.started_at = time.time()
        self.last_metrics: dict[str, Any] = {}

    def health(self) -> dict:
        current = snapshot(int(self.config.get("resources.gpu.device", 0))).to_dict()
        unique_voices = self.library.list()
        activation = self.manager.active_activation or self.manager.preview_default()
        metadata = activation.metadata()
        return {
            "status": "ok",
            "model": metadata["requested_model"],
            "default_model": "tts-1-ru",
            "available_models": self.registry.public_aliases(),
            "active_model": metadata["resolved_model"] if self.manager.active_activation else None,
            "resolved_hf_id": metadata["resolved_hf_id"],
            "backend": self.config.get("model.backend"),
            "mode": metadata["mode"],
            "mode_reason": metadata["mode_reason"],
            "device": metadata["device"],
            "dtype": metadata["dtype"],
            "attention": metadata["attention"],
            "model_loaded": bool(self.manager.active_worker and self.manager.active_worker.loaded),
            "model_load_seconds": metadata["model_load_seconds"],
            "voice_count": len(unique_voices),
            "queue_waiting": self.waiting,
            "queue_max_concurrent_configured": self.configured_max_concurrent,
            "queue_max_concurrent_effective": self.effective_max_concurrent,
            "resources": current,
            "uptime_seconds": round(time.time() - self.started_at, 1),
        }

    async def _acquire(self) -> None:
        max_waiting = int(self.config.get("queue.max_waiting", 4))
        if self.waiting >= max_waiting:
            raise RuntimeError("очередь TTS заполнена")
        self.waiting += 1
        try:
            await asyncio.wait_for(self.semaphore.acquire(), timeout=float(self.config.get("queue.wait_timeout_seconds", 30)))
        finally:
            self.waiting -= 1

    async def _on_demand(
        self,
        text: str,
        voice: str,
        language: str,
        model: str,
        generation_preset: str,
    ) -> tuple[Any, int, dict]:
        runtime = self.config.path("runtime.dir", "runtime")
        runtime.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="qwen-tts-job-", dir=runtime) as folder:
            root = Path(folder)
            job_path, output_path, result_path = root / "job.json", root / "output.wav", root / "result.json"
            job = {
                "config": str(self.config.source),
                "text": text,
                "voice": voice,
                "language": language,
                "model": model,
                "generation_preset": generation_preset,
                "output": str(output_path),
                "result": str(result_path),
            }
            job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "qwen3_tts_st.worker_process",
                "--job",
                str(job_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=float(self.config.get("queue.generation_timeout_seconds", 900)))
            except TimeoutError:
                process.kill()
                await process.wait()
                raise TimeoutError("CUDA on-demand worker превысил timeout")
            if process.returncode != 0:
                raise RuntimeError(f"on-demand worker завершился с кодом {process.returncode}: {stderr.decode(errors='replace')[-1000:]}")
            waveform, sample_rate = sf.read(output_path, dtype="float32")
            metrics = json.loads(result_path.read_text(encoding="utf-8"))["metrics"]
            return waveform, sample_rate, metrics

    async def synthesize(self, request: Any) -> tuple[bytes, str, dict]:
        await self._acquire()
        try:
            settings = dict(self.config.get("preprocessing", {}) or {})
            prepared = preprocess(request.input, settings, request.preprocessing_mode)
            segments, router_warnings = parse_emotion_script_detailed(prepared)
            if not segments:
                raise ValueError("после удаления служебных тегов не осталось произносимого текста")
            configured_fallback = str(self.config.get("voices.fallback_profile", "")) or None
            selected = self.library.resolve(request.voice, configured_fallback)
            base = self.library.resolve_family_neutral(selected, configured_fallback)
            requested_model = getattr(request, "model", None) or "tts-1-ru"
            resolved = self.registry.resolve(requested_model)
            generation_preset = (
                getattr(request, "generation_preset", None)
                or str(self.config.get("request_defaults.generation_preset", "default"))
            )
            normalize_mode = (
                getattr(request, "russian_normalization", None)
                or str(self.config.get("request_defaults.russian_normalization", "off"))
            )
            generate = generation_kwargs(self.config, generation_preset, resolved.spec)
            pronunciation = merge_pronunciation_dictionaries(
                self.config.get("pronunciation.dictionary", {}),
                getattr(request, "pronunciation_overrides", None),
            )
            yo_dictionary = self.config.get("normalization.yo_dictionary", {}) or {}
            parts = []
            all_metrics = []
            selected_voices = []
            pronunciation_replacements = 0
            activation: ModelActivation = self.manager.prepare(requested_model)
            max_chars = int(self.config.get("chunking.max_chars", 320))
            for segment in segments:
                profile = self.library.find_style(base.character, segment.style, base)
                normalized = normalize_russian_text(segment.text, normalize_mode, yo_dictionary)
                pronounced, replacements = apply_pronunciation(normalized, pronunciation)
                pronunciation_replacements += replacements
                for chunk in split_long_text(pronounced, max_chars):
                    if activation.mode == "cuda_on_demand" and not isinstance(activation.worker, MockWorker):
                        waveform, sample_rate, metrics = await self._on_demand(
                            chunk,
                            profile.voice_id,
                            "Russian",
                            requested_model,
                            generation_preset,
                        )
                    else:
                        waveform, sample_rate, metrics = await asyncio.wait_for(
                            asyncio.to_thread(
                                self.manager.synthesize_prepared,
                                activation,
                                chunk,
                                profile,
                                "Russian",
                                generate,
                            ),
                            timeout=float(self.config.get("queue.generation_timeout_seconds", 900)),
                        )
                    metrics.update(activation.metadata())
                    metrics["generation_preset"] = generation_preset
                    parts.append((waveform, sample_rate))
                    all_metrics.append(metrics)
                    selected_voices.append(profile.voice_id)
            waveform, sample_rate = stitch(
                parts,
                pause_ms=int(self.config.get("pauses.segment_ms", 120)),
                crossfade_ms=int(self.config.get("pauses.crossfade_ms", 8)),
            )
            waveform = change_speed(waveform, request.speed)
            payload, media_type = encode(waveform, sample_rate, request.response_format)
            duration = len(waveform) / sample_rate
            metadata = {
                "duration_seconds": duration,
                "sample_rate": sample_rate,
                "segments": len(parts),
                "styles": [segment.style for segment in segments],
                "segment_types": [segment.kind for segment in segments],
                "voices": selected_voices,
                "router_warnings": router_warnings,
                "requested_model": requested_model,
                "resolved_model": resolved.canonical,
                "resolved_hf_id": resolved.hf_id,
                "model_action": activation.action,
                "mode": activation.mode,
                "device": activation.device,
                "dtype": activation.dtype,
                "attention": activation.attention,
                "generation_preset": generation_preset,
                "russian_normalization": normalize_mode,
                "pronunciation_replacements": pronunciation_replacements,
                "generation": all_metrics,
            }
            self.last_metrics = metadata
            self.completed += 1
            return payload, media_type, metadata
        except Exception:
            self.failed += 1
            raise
        finally:
            self.semaphore.release()

    def metrics(self) -> dict:
        return {"completed": self.completed, "failed": self.failed, "waiting": self.waiting, "last": self.last_metrics}

    def shutdown(self) -> None:
        self.manager.shutdown()
