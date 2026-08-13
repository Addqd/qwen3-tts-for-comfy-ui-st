from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import time
import wave

import httpx


ROOT = Path(__file__).resolve().parents[1]


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:8030")
    args = parser.parse_args()

    input_path = ROOT / "scripts" / "qwentts-quality-inputs.json"
    raw = input_path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise RuntimeError("Quality input JSON must be UTF-8 without BOM")
    text = raw.decode("utf-8")
    if text.encode("utf-8") != raw:
        raise RuntimeError("Quality input file failed exact UTF-8 byte round-trip")
    samples = json.loads(text)
    if any(value.encode("utf-8").decode("utf-8") != value for value in samples.values()):
        raise RuntimeError("A quality input failed Unicode round-trip")

    voice_dir = ROOT / "voice_library" / "profiles" / "testrudima" / "neutral"
    metadata = json.loads((voice_dir / "metadata.json").read_text(encoding="utf-8"))
    voice_id = "clone:test_ru_dima_neutral"
    output_dir = ROOT / "runtime" / "qwentts" / "benchmarks"
    output_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client(base_url=args.endpoint, timeout=300) as client:
        registration = {
            "name": voice_id,
            "ref_text": metadata["ref_text"],
            "spk_b64": base64.b64encode((voice_dir / "reference.spk").read_bytes()).decode("ascii"),
            "rvq_b64": base64.b64encode((voice_dir / "reference.rvq").read_bytes()).decode("ascii"),
        }
        client.post("/v1/audio/voices", json=registration).raise_for_status()
        results = []
        for number, (name, source_text) in enumerate(samples.items(), 1):
            serialized = json.dumps(
                {
                    "model": "tts-1-ru",
                    "voice": voice_id,
                    "input": source_text,
                    "response_format": "wav",
                    "seed": 42,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            decoded = json.loads(serialized.decode("utf-8"))["input"]
            if decoded != source_text:
                raise RuntimeError(f"HTTP JSON UTF-8 round-trip failed for {name}")
            started = time.perf_counter()
            response = client.post(
                "/v1/audio/speech",
                content=serialized,
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            elapsed = time.perf_counter() - started
            response.raise_for_status()
            path = output_dir / f"qwentts-dima-{name}.wav"
            path.write_bytes(response.content)
            duration = wav_duration(path)
            results.append(
                {
                    "sample": name,
                    "input_utf8_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
                    "wall_seconds": round(elapsed, 3),
                    "audio_seconds": round(duration, 3),
                    "rtf": round(elapsed / duration, 3),
                    "wav_sha256": hashlib.sha256(response.content).hexdigest(),
                }
            )
    print(json.dumps({"utf8_round_trip": True, "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
