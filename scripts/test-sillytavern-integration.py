from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx


def local_url(value: str) -> str:
    parsed = httpx.URL(value)
    if parsed.scheme != "http" or parsed.host != "127.0.0.1" or parsed.port is None:
        raise ValueError(f"Only http://127.0.0.1:<port> is allowed: {value}")
    return value.rstrip("/")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="http://127.0.0.1:8020")
    parser.add_argument("--sillytavern", default="http://127.0.0.1:8000")
    parser.add_argument("--voice", default="clone:test_ru_dima_neutral")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    backend, silly = local_url(args.backend), local_url(args.sillytavern)
    text = "Она проверила настройки и сказала: «Всё готово, можно продолжать работу»."
    if text.encode("utf-8").decode("utf-8") != text:
        raise RuntimeError("UTF-8 round-trip failed")
    root = Path(__file__).resolve().parents[1]
    output = root / "artifacts" / "audio-tests" / "sillytavern-qwentts-smoke.mp3"
    output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=args.timeout) as client:
        health_response = client.get(f"{backend}/health")
        health_response.raise_for_status()
        health = health_response.json()
        if health.get("engine") != "qwentts.cpp" or health.get("qwentts_ready") is not True:
            raise RuntimeError("The active backend is not a ready project qwentts.cpp facade")
        models_response = client.get(f"{backend}/v1/models")
        models_response.raise_for_status()
        models = models_response.json()
        voices_response = client.get(f"{backend}/v1/voices")
        voices_response.raise_for_status()
        voices = voices_response.json()
        if "tts-1-ru" not in [item["id"] for item in models["data"]]:
            raise RuntimeError("tts-1-ru is unavailable")
        if args.voice not in [item["voice_id"] for item in voices["data"]]:
            raise RuntimeError(f"Voice is unavailable: {args.voice}")
        csrf_response = client.get(f"{silly}/csrf-token")
        csrf_response.raise_for_status()
        token = csrf_response.json()["token"]
        response = client.post(
            f"{silly}/api/openai/custom/generate-voice",
            headers={"X-CSRF-Token": token},
            json={"provider_endpoint": f"{backend}/v1/audio/speech", "model": "tts-1-ru", "input": text,
                  "voice": args.voice, "response_format": "mp3", "speed": 1.0},
        )
        response.raise_for_status()
        output.write_bytes(response.content)
    if output.stat().st_size < 1024:
        raise RuntimeError("SillyTavern proxy returned an unexpectedly small audio file")
    print(json.dumps({"status": health["status"], "engine": health["engine"], "voice": args.voice,
                      "utf8_round_trip": True, "audio_path": str(output), "audio_bytes": output.stat().st_size},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
