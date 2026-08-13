from __future__ import annotations

import json
from pathlib import Path
import time

import httpx


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    samples = json.loads((ROOT / "scripts" / "qwentts-quality-inputs.json").read_text(encoding="utf-8"))
    text = samples["sample-1"]
    if json.loads(json.dumps({"input": text}, ensure_ascii=False))["input"] != text:
        raise RuntimeError("UTF-8 JSON round-trip failed")
    output = ROOT / "artifacts" / "audio-tests"
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with httpx.Client(timeout=300) as client:
        for response_format in ("wav", "mp3"):
            started = time.perf_counter()
            response = client.post("http://127.0.0.1:8020/v1/audio/speech", json={
                "model": "tts-1-ru",
                "voice": "clone:test_ru_dima_neutral",
                "input": text,
                "response_format": response_format,
                "speed": 1.0,
            })
            elapsed = time.perf_counter() - started
            response.raise_for_status()
            path = output / f"qwentts-facade-smoke.{response_format}"
            path.write_bytes(response.content)
            results.append({
                "format": response_format,
                "status": response.status_code,
                "wall_seconds": round(elapsed, 3),
                "bytes": len(response.content),
                "content_type": response.headers.get("content-type"),
                "engine": response.headers.get("x-tts-engine"),
                "input_round_trip": True,
            })
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
