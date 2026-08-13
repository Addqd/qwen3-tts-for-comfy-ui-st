from __future__ import annotations

import argparse
from pathlib import Path
import sys

import uvicorn

from .app import create_app
from .config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Qwen3-TTS OpenAI-compatible backend")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    config = load_config(args.config)
    log_dir = Path(__file__).resolve().parents[2] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    sys.stdout = (log_dir / "server.out.log").open("a", encoding="utf-8", buffering=1)
    sys.stderr = (log_dir / "server.err.log").open("a", encoding="utf-8", buffering=1)
    uvicorn.run(
        create_app(config=config),
        host=str(config.get("server.host", "127.0.0.1")),
        port=int(config.get("server.port", 8020)),
        workers=1,
        log_level=str(config.get("logging.level", "info")).lower(),
        access_log=bool(config.get("logging.access_log", False)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
