from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

import soundfile as sf

from .audio import AudioPart, change_speed, encode, fade_edges, pad_edges, stitch
from .emotion import parse_emotion_script_detailed
from .generation import generation_kwargs
from .model_manager import ModelActivation, ModelManager
from .models import ModelRegistry
from .normalization import (
    apply_pronunciation,
    merge_pronunciation_dictionaries,
    normalize_russian_text,
)
from .preprocess import preprocess, split_language_spans, split_long_text
from .resources import snapshot
from .runtime_settings import RuntimeSettingsStore
from .voices import VoiceLibrary
from .worker import MockWorker


class TTSService:
    def __init__(self, config: Any):
        self.config = config
        self.registry = ModelRegistry(config)
        self.runtime_settings = RuntimeSettingsStore(
            config,
            self.registry.public_aliases(),
            list((config.get("generation.presets", {}) or {}).keys()),
        )
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
        runtime = self.runtime_settings.current()
        effective_default = self.runtime_settings.resolve_model("tts-1-ru")
        default_resolved = self.registry.resolve(effective_default)
        activation = self.manager.active_activation or self.manager.preview(effective_default)
        metadata = activation.metadata()
        return {
            "status": "ok",
            "model": "tts-1-ru",
            "default_model": runtime["active_model"],
            "effective_default_model": default_resolved.canonical,
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
            "runtime_settings": runtime,
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
            runtime = self.runtime_settings.current()
            requested_model = getattr(request, "model", None) or "tts-1-ru"
            effective_model = self.runtime_settings.resolve_model(requested_model)
            resolved = self.registry.resolve(effective_model)
            generation_preset = (
                getattr(request, "generation_preset", None)
                or str(runtime["generation_preset"])
            )
            normalize_mode = (
                getattr(request, "russian_normalization", None)
                or str(runtime["russian_normalization"])
            )
            multilingual_mode = getattr(request, "multilingual_mode", None) or str(runtime["multilingual_mode"])
            chunking_mode = getattr(request, "chunking_mode", None) or str(runtime["chunking_mode"])
            leading_silence_ms = getattr(request, "leading_silence_ms", None)
            if leading_silence_ms is None:
                leading_silence_ms = int(runtime["leading_silence_ms"])
            trailing_silence_ms = getattr(request, "trailing_silence_ms", None)
            if trailing_silence_ms is None:
                trailing_silence_ms = int(runtime["trailing_silence_ms"])
            generate = generation_kwargs(self.config, generation_preset, resolved.spec)
            persistent_pronunciation = merge_pronunciation_dictionaries(
                self.config.get("pronunciation.dictionary", {}),
                runtime["pronunciation_defaults"],
            )
            pronunciation = merge_pronunciation_dictionaries(
                persistent_pronunciation,
                getattr(request, "pronunciation_overrides", None),
            )
            yo_dictionary = self.config.get("normalization.yo_dictionary", {}) or {}
            parts: list[AudioPart] = []
            all_metrics = []
            selected_voices = []
            generation_voices = []
            languages = []
            pronunciation_replacements = 0
            activation: ModelActivation = self.manager.prepare(effective_model)
            max_chars = int(self.config.get("chunking.max_chars", 320))

            async def generate_piece(piece_text, profile, language, segment_kind):
                if activation.mode == "cuda_on_demand" and not isinstance(activation.worker, MockWorker):
                    waveform, sample_rate, metrics = await self._on_demand(
                        piece_text,
                        profile.voice_id,
                        language,
                        effective_model,
                        generation_preset,
                    )
                else:
                    waveform, sample_rate, metrics = await asyncio.wait_for(
                        asyncio.to_thread(
                            self.manager.synthesize_prepared,
                            activation,
                            piece_text,
                            profile,
                            language,
                            generate,
                        ),
                        timeout=float(self.config.get("queue.generation_timeout_seconds", 900)),
                    )
                metrics.update(activation.metadata())
                metrics["generation_preset"] = generation_preset
                metrics["language"] = language
                metrics["voice"] = profile.voice_id
                metrics["performance_kind"] = segment_kind
                parts.append(AudioPart(waveform, sample_rate, segment_kind, profile.profile_id))
                all_metrics.append(metrics)
                generation_voices.append(profile.voice_id)
                languages.append(language)

            for segment in segments:
                if segment.kind == "sound":
                    profile = self.library.find_sound(
                        base.character,
                        str(segment.sound_type),
                        segment.preferred_style,
                    )
                    if profile is None:
                        router_warnings.append(f"missing_sound_profile:{segment.sound_type}")
                        continue
                    carrier = str(
                        self.config.get(
                            f"performance.sound_carriers.{segment.sound_type}",
                            "",
                        )
                    ).strip()
                    if not carrier:
                        router_warnings.append(f"missing_sound_carrier:{segment.sound_type}")
                        continue
                    selected_voices.append(profile.voice_id)
                    await generate_piece(carrier, profile, "Russian", "sound")
                    continue

                profile = self.library.find_style(base.character, segment.style, base)
                selected_voices.append(profile.voice_id)
                pronounced, replacements = apply_pronunciation(segment.text, pronunciation)
                pronunciation_replacements += replacements
                for chunk in split_long_text(pronounced, max_chars, chunking_mode):
                    for language_span in split_language_spans(chunk, multilingual_mode):
                        span_text = language_span.text
                        if language_span.language == "Russian":
                            span_text = normalize_russian_text(span_text, normalize_mode, yo_dictionary)
                        await generate_piece(span_text, profile, language_span.language, "speech")
            router_warnings = list(dict.fromkeys(router_warnings))
            if not parts:
                raise ValueError("performance не создал ни одного аудиосегмента")
            waveform, sample_rate = stitch(
                parts,
                crossfade_ms=int(self.config.get("pauses.crossfade_ms", 8)),
                boundary_config=dict(self.config.get("performance.boundaries", {}) or {}),
            )
            if not waveform.size:
                raise ValueError("boundary pipeline вернул пустое итоговое аудио")
            waveform = change_speed(waveform, request.speed)
            waveform = fade_edges(waveform, sample_rate, int(self.config.get("pauses.edge_fade_ms", 5)))
            waveform = pad_edges(waveform, sample_rate, leading_silence_ms, trailing_silence_ms)
            payload, media_type = encode(waveform, sample_rate, request.response_format)
            duration = len(waveform) / sample_rate
            metadata = {
                "duration_seconds": duration,
                "sample_rate": sample_rate,
                "segments": len(parts),
                "styles": [segment.style for segment in segments],
                "segment_types": [segment.kind for segment in segments],
                "sound_types": [segment.sound_type for segment in segments if segment.kind == "sound"],
                "voices": selected_voices,
                "generation_voices": generation_voices,
                "router_warnings": router_warnings,
                "requested_model": requested_model,
                "effective_model": effective_model,
                "resolved_model": resolved.canonical,
                "resolved_hf_id": resolved.hf_id,
                "model_action": activation.action,
                "mode": activation.mode,
                "device": activation.device,
                "dtype": activation.dtype,
                "attention": activation.attention,
                "generation_preset": generation_preset,
                "russian_normalization": normalize_mode,
                "multilingual_mode": multilingual_mode,
                "chunking_mode": chunking_mode,
                "languages": languages,
                "leading_silence_ms": leading_silence_ms,
                "trailing_silence_ms": trailing_silence_ms,
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
