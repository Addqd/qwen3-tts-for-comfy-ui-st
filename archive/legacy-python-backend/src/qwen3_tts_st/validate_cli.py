from __future__ import annotations

import argparse
import json
from pathlib import Path

from .voices import validate_audio


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wav")
    parser.add_argument("--ref-text", default="")
    args = parser.parse_args()
    result = validate_audio(Path(args.wav).resolve(), args.ref_text)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

