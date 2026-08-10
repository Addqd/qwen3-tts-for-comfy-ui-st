from __future__ import annotations

import argparse
import json
from pathlib import Path

import soundfile as sf

from .config import load_config
from .generation import generation_kwargs
from .models import ModelRegistry
from .voices import VoiceLibrary
from .worker import QwenWorker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    job_path = Path(args.job).resolve()
    job = json.loads(job_path.read_text(encoding="utf-8"))
    config = load_config(job["config"])
    registry = ModelRegistry(config)
    resolved = registry.resolve(job.get("model"))
    library = VoiceLibrary(config.path("voices.library_dir", "voice_library"))
    profile = library.resolve(job["voice"])
    worker = QwenWorker(
        config,
        "cuda_on_demand",
        model_id=resolved.hf_id,
        runtime=resolved.spec.runtime,
    )
    try:
        language = str(job.get("language", "Russian"))
        if language not in {"Russian", "English"}:
            raise ValueError(f"unsupported synthesis language: {language}")
        preset = str(job.get("generation_preset", "default"))
        generate = generation_kwargs(config, preset, resolved.spec)
        # QwenWorker.synthesize() owns lazy load; an extra load() here would
        # duplicate lifecycle logic without improving safety.
        waveform, sample_rate, metrics = worker.synthesize(
            job["text"],
            profile,
            language,
            generation_kwargs=generate,
        )
        metrics.update(
            {
                "requested_model": resolved.requested_alias,
                "resolved_model": resolved.canonical,
                "resolved_hf_id": resolved.hf_id,
                "generation_preset": preset,
            }
        )
        sf.write(job["output"], waveform, sample_rate, subtype="PCM_16")
        result_path = Path(job["result"])
        result_path.write_text(json.dumps({"sample_rate": sample_rate, "metrics": metrics}), encoding="utf-8")
        return 0
    finally:
        worker.unload()


if __name__ == "__main__":
    raise SystemExit(main())
