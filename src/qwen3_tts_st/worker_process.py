from __future__ import annotations

import argparse
import json
from pathlib import Path

import soundfile as sf

from .config import load_config
from .voices import VoiceLibrary
from .worker import QwenWorker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    job_path = Path(args.job).resolve()
    job = json.loads(job_path.read_text(encoding="utf-8"))
    config = load_config(job["config"])
    library = VoiceLibrary(config.path("voices.library_dir", "voice_library"))
    profile = library.resolve(job["voice"])
    worker = QwenWorker(config, "cuda_on_demand")
    waveform, sample_rate, metrics = worker.synthesize(job["text"], profile, job.get("language", "Russian"))
    sf.write(job["output"], waveform, sample_rate, subtype="PCM_16")
    result_path = Path(job["result"])
    result_path.write_text(json.dumps({"sample_rate": sample_rate, "metrics": metrics}), encoding="utf-8")
    worker.unload()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

