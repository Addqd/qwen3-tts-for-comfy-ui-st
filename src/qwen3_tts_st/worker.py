from __future__ import annotations

import gc
import math
import os
from pathlib import Path
import threading
import time
from typing import Any

import numpy as np

from .voices import VoiceProfile


_TORCH_THREAD_CONFIG_LOCK = threading.Lock()
_INTEROP_THREADS_CONFIGURED = False


def _configure_cpu_threads(torch_module: Any, threads: int) -> None:
    global _INTEROP_THREADS_CONFIGURED
    torch_module.set_num_threads(threads)
    # PyTorch permits set_num_interop_threads only once before any parallel
    # work. A request-level model switch must not call it again.
    with _TORCH_THREAD_CONFIG_LOCK:
        if not _INTEROP_THREADS_CONFIGURED:
            try:
                torch_module.set_num_interop_threads(max(1, min(2, threads)))
            except RuntimeError as exc:
                if "cannot set number of interop threads" not in str(exc).lower():
                    raise
            _INTEROP_THREADS_CONFIGURED = True


class QwenWorker:
    """One in-process model instance. Heavy imports are intentionally lazy."""

    def __init__(
        self,
        config: Any,
        mode: str,
        model_id: str | None = None,
        runtime: dict[str, Any] | None = None,
    ):
        self.config = config
        self.mode = mode
        legacy_model_id = (
            config.get("model.id", "Qwen/Qwen3-TTS-12Hz-0.6B-Base")
            if config is not None
            else "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
        )
        self.model_id = model_id or str(legacy_model_id)
        self.runtime = dict(runtime or {})
        self.model = None
        self.torch = None
        self.loaded_at: float | None = None
        self.load_seconds: float | None = None
        self.prompt_cache: dict[str, tuple[tuple[str, int, int, str, str], Any]] = {}
        self.lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self.model is not None

    def _device_dtype(self) -> tuple[str, str, str]:
        device = "cuda:0" if self.mode in {"cuda", "cuda_on_demand"} else "cpu"
        configured = str(self.runtime.get("dtype", self.config.get("model.dtype", "auto"))).lower()
        # FP16 stalled in the verified Turing configuration; auto stays FP32.
        dtype = configured if configured != "auto" else "float32"
        attention = str(self.runtime.get("attention", self.config.get("model.attention", "sdpa"))).lower()
        return device, dtype, attention

    def load(self) -> None:
        if self.loaded:
            return
        started = time.perf_counter()
        cache_dir = self.config.path("models.cache_dir", str(self.config.get("model.cache_dir", "model_cache")))
        cache_dir.mkdir(parents=True, exist_ok=True)
        # qwen-tts 0.1.1 forwards cache_dir to the model but not to its
        # AutoProcessor. Keep every Hugging Face component inside the project.
        os.environ["HF_HOME"] = str(cache_dir)
        os.environ["HUGGINGFACE_HUB_CACHE"] = str(cache_dir)
        import torch
        from qwen_tts import Qwen3TTSModel

        device, dtype_name, attention = self._device_dtype()
        if dtype_name == "bfloat16":
            raise ValueError("BF16 не разрешён автоматически для RTX Turing")
        dtype = {"float16": torch.float16, "float32": torch.float32}.get(dtype_name)
        if dtype is None:
            raise ValueError(f"неподдерживаемый dtype: {dtype_name}")
        if device == "cpu":
            reserve = int(self.config.get("resources.cpu.reserve_threads", 2))
            requested = self.config.get("resources.cpu.max_threads", "auto")
            threads = max(1, (os.cpu_count() or 1) - reserve) if requested == "auto" else int(requested)
            _configure_cpu_threads(torch, threads)
        elif not torch.cuda.is_available():
            raise RuntimeError("CUDA запрошена, но torch.cuda.is_available() == False")
        try:
            model = Qwen3TTSModel.from_pretrained(
                self.model_id,
                device_map=device,
                dtype=dtype,
                attn_implementation=attention,
                cache_dir=str(cache_dir),
            )
        except Exception:
            self.model = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            raise
        self.model = model
        self.torch = torch
        self.loaded_at = time.time()
        self.load_seconds = time.perf_counter() - started

    def _prompt(self, profile: VoiceProfile):
        key = str(profile.reference_path.resolve())
        stat = profile.reference_path.stat()
        identity = (
            self.model_id,
            stat.st_mtime_ns,
            stat.st_size,
            profile.ref_text,
            profile.clone_mode.lower(),
        )
        cached = self.prompt_cache.get(key)
        if cached and cached[0] == identity:
            return cached[1]
        prompt = self.model.create_voice_clone_prompt(
            ref_audio=str(profile.reference_path),
            ref_text=profile.ref_text,
            x_vector_only_mode=profile.clone_mode.lower() != "icl",
        )
        self.prompt_cache[key] = (identity, prompt)
        return prompt

    def synthesize(
        self,
        text: str,
        profile: VoiceProfile,
        language: str = "Russian",
        generation_kwargs: dict[str, Any] | None = None,
    ) -> tuple[np.ndarray, int, dict]:
        with self.lock:
            self.load()
            started = time.perf_counter()
            prompt = self._prompt(profile)
            generate = dict(generation_kwargs or {})
            generate.setdefault(
                "max_new_tokens",
                int(self.runtime.get("max_new_tokens", self.config.get("model.max_new_tokens", 2048))),
            )
            wavs, sample_rate = self.model.generate_voice_clone(
                text=text,
                language=language,
                voice_clone_prompt=prompt,
                **generate,
            )
            waveform = np.asarray(wavs[0], dtype=np.float32)
            if not np.isfinite(waveform).all():
                raise RuntimeError("модель вернула NaN или Inf")
            elapsed = time.perf_counter() - started
            duration = len(waveform) / sample_rate
            return waveform, sample_rate, {
                "synthesis_seconds": elapsed,
                "duration_seconds": duration,
                "rtf": elapsed / max(duration, 0.001),
                "model_id": self.model_id,
            }

    def unload(self) -> None:
        self.prompt_cache.clear()
        self.model = None
        gc.collect()
        if self.torch is not None and self.torch.cuda.is_available():
            self.torch.cuda.empty_cache()
            self.torch.cuda.synchronize()
        self.torch = None


class MockWorker(QwenWorker):
    """Deterministic WAV generator for API/integration tests; never used as production TTS."""

    @property
    def loaded(self) -> bool:
        return True

    def load(self) -> None:
        self.loaded_at = self.loaded_at or time.time()
        self.load_seconds = 0.0

    def synthesize(
        self,
        text: str,
        profile: VoiceProfile,
        language: str = "Russian",
        generation_kwargs: dict[str, Any] | None = None,
    ) -> tuple[np.ndarray, int, dict]:
        started = time.perf_counter()
        sample_rate = 24000
        duration = max(0.25, min(5.0, len(text) / 18.0))
        timeline = np.arange(int(sample_rate * duration), dtype=np.float32) / sample_rate
        frequency = 180 + (sum(map(ord, profile.style)) % 100)
        envelope = np.minimum(1.0, timeline * 20) * np.minimum(1.0, (duration - timeline) * 20)
        waveform = (0.08 * np.sin(2 * math.pi * frequency * timeline) * envelope).astype(np.float32)
        elapsed = time.perf_counter() - started
        return waveform, sample_rate, {
            "synthesis_seconds": elapsed,
            "duration_seconds": duration,
            "rtf": elapsed / duration,
            "mock": True,
            "model_id": self.model_id,
            "generation_kwargs": dict(generation_kwargs or {}),
        }
