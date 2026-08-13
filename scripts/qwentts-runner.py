from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

from qwen3_tts_st.config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Project-owned persistent qwentts.cpp runner")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    root = Path(__file__).resolve().parents[1]
    runtime = root / "runtime"
    logs = root / "logs"
    runtime.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    executable = config.path("qwentts.executable", "runtime/qwentts/bin/tts-server.exe")
    command = [
        str(executable),
        "--model", str(config.path("qwentts.talker_model", "runtime/qwentts/models/qwen-talker-1.7b-base-Q8_0.gguf")),
        "--codec", str(config.path("qwentts.codec_model", "runtime/qwentts/models/qwen-tokenizer-12hz-Q8_0.gguf")),
        "--alias", str(config.get("qwentts.model_id", "tts-1-ru")),
        "--host", "127.0.0.1",
        "--port", str(int(config.get("qwentts.port", 8030))),
        "--lang", str(config.get("qwentts.language", "Russian")),
        "--max-batch", str(int(config.get("qwentts.max_batch", 1))),
    ]
    environment = os.environ.copy()
    environment["GGML_BACKEND"] = str(config.get("qwentts.backend", "CUDA0"))
    with (logs / "qwentts.out.log").open("wb", buffering=0) as stdout, (logs / "qwentts.err.log").open("wb", buffering=0) as stderr:
        process = subprocess.Popen(command, cwd=executable.parent, env=environment, stdout=stdout, stderr=stderr)
        state = {"pid": process.pid, "executable": str(executable), "command": command}
        (runtime / "qwentts.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
