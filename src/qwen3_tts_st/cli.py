from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import uvicorn

from .app import create_app
from .config import load_config


def main() -> int:
    if os.environ.get("QWEN3_TTS_SESSION_INTERNAL") != "1":
        raise RuntimeError("Direct facade startup is unmanaged; use start.ps1 or start-tts-and-comfyui.bat")
    parser = argparse.ArgumentParser(description="Local qwentts.cpp compatibility service")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    config = load_config(args.config)
    logs = Path(__file__).resolve().parents[2] / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    sys.stdout = (logs / "facade.out.log").open("a", encoding="utf-8", buffering=1)
    sys.stderr = (logs / "facade.err.log").open("a", encoding="utf-8", buffering=1)
    uvicorn.run(
        create_app(config=config),
        host="127.0.0.1",
        port=int(config.get("server.port", 8020)),
        workers=1,
        log_level=str(config.get("logging.level", "info")).lower(),
        access_log=bool(config.get("logging.access_log", False)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
