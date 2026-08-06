from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any

import soundfile as sf

from .audio import change_speed, encode, stitch
from .emotion import parse_emotion_script
from .preprocess import preprocess, split_long_text
from .resources import choose_mode, snapshot
from .voices import VoiceLibrary
from .worker import MockWorker, QwenWorker


class TTSService:
    def __init__(self, config: Any):
        self.config = config
        self.mode, self.mode_reason, self.initial_resources = choose_mode(config)
        self.library = VoiceLibrary(config.path("voices.library_dir", "voice_library"))
        backend = str(config.get("model.backend", "qwen")).lower()
        self.worker = MockWorker(config, self.mode) if backend == "mock" else QwenWorker(config, self.mode)
        self.semaphore = asyncio.Semaphore(int(config.get("queue.max_concurrent", 1)))
        self.waiting = 0
        self.completed = 0
        self.failed = 0
        self.started_at = time.time()
        self.last_metrics: dict[str, Any] = {}

    def health(self) -> dict:
        current = snapshot(int(self.config.get("resources.gpu.device", 0))).to_dict()
        unique_voices = self.library.list()
        device = "cuda:0" if self.mode in {"cuda", "cuda_on_demand"} else "cpu"
        dtype = self.config.get("model.dtype", "auto")
        if dtype == "auto":
            dtype = "float16" if device.startswith("cuda") else "float32"
        return {
            "status": "ok",
            "model": self.config.get("model.id"),
            "backend": self.config.get("model.backend"),
            "mode": self.mode,
            "mode_reason": self.mode_reason,
            "device": device,
            "dtype": dtype,
            "attention": self.config.get("model.attention"),
            "model_loaded": self.worker.loaded,
            "model_load_seconds": self.worker.load_seconds,
            "voice_count": len(unique_voices),
            "queue_waiting": self.waiting,
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

    async def _on_demand(self, text: str, voice: str, language: str) -> tuple[Any, int, dict]:
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
            segments = parse_emotion_script(prepared, "neutral")
            base = self.library.resolve(request.voice, str(self.config.get("voices.fallback_profile", "")) or None)
            parts = []
            all_metrics = []
            selected_voices = []
            max_chars = int(self.config.get("chunking.max_chars", 320))
            for segment in segments:
                profile = self.library.find_style(base.character, segment.style, base)
                for chunk in split_long_text(segment.text, max_chars):
                    if self.mode == "cuda_on_demand" and not isinstance(self.worker, MockWorker):
                        waveform, sample_rate, metrics = await self._on_demand(chunk, profile.voice_id, "Russian")
                    else:
                        waveform, sample_rate, metrics = await asyncio.wait_for(
                            asyncio.to_thread(self.worker.synthesize, chunk, profile, "Russian"),
                            timeout=float(self.config.get("queue.generation_timeout_seconds", 900)),
                        )
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
                "voices": selected_voices,
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
        self.worker.unload()
