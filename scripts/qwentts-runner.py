from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone
from uuid import uuid4

from qwen3_tts_st.config import load_config


def persist_state_or_stop(
    process: subprocess.Popen[bytes], state_path: Path, temporary_state: Path, state: dict[str, object]
) -> None:
    try:
        temporary_state.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        temporary_state.replace(state_path)
    except Exception:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=15)
        temporary_state.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)
        raise


def main() -> int:
    if os.environ.get("QWEN3_TTS_SESSION_INTERNAL") != "1":
        raise RuntimeError("qwentts-runner.py is an internal component; use start.ps1 or start-tts-and-comfyui.bat")
    parser = argparse.ArgumentParser(description="Project-owned persistent qwentts.cpp runner")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    root = Path(__file__).resolve().parents[1]
    runtime = root / "runtime"
    logs = root / "logs"
    runtime.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    executable, _codec_executable = config.qwentts_executables()
    talker_model, codec_model = config.qwentts_models()
    for required in (executable, talker_model, codec_model):
        if not required.exists():
            raise FileNotFoundError(f"Required qwentts BF16 runtime file is missing: {required}")
    command = [
        str(executable),
        "--model", str(talker_model),
        "--codec", str(codec_model),
        "--alias", str(config.get("qwentts.model_id", "tts-1-ru")),
        "--host", "127.0.0.1",
        "--port", str(int(config.get("qwentts.port", 8030))),
        "--lang", str(config.get("qwentts.language", "Russian")),
        "--max-batch", str(int(config.get("qwentts.max_batch", 1))),
    ]
    environment = os.environ.copy()
    environment["GGML_BACKEND"] = config.qwentts_backend()
    state_path = runtime / "qwentts.json"
    temporary_state = runtime / "qwentts.json.tmp"
    session_id = uuid4().hex
    with (logs / "qwentts.out.log").open("wb", buffering=0) as stdout, (logs / "qwentts.err.log").open("wb", buffering=0) as stderr:
        stderr.write(f"[qwen3-tts-st] session={session_id}\n".encode("ascii"))
        process = subprocess.Popen(command, cwd=executable.parent, env=environment, stdout=stdout, stderr=stderr)
        state = {
            "pid": process.pid,
            "runner_pid": os.getpid(),
            "runner_parent_pid": os.getppid(),
            "executable": str(executable),
            "command": command,
            "model_variant": "bf16",
            "talker_model": talker_model.name,
            "codec_model": codec_model.name,
            "session_id": session_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        persist_state_or_stop(process, state_path, temporary_state, state)
        return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
