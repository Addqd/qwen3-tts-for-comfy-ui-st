from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import tempfile
from typing import Any
from urllib import error, request
from urllib.parse import urlsplit
import uuid
import wave

import numpy as np


CATEGORY = "Qwen TTS API"
VERSION = "1.0.0"
PRODUCTION_MODEL = "Qwen3-TTS 1.7B Base Q8_0 (tts-1-ru)"
NORMALIZATION_OPTIONS = ("Use Backend Default", "Off", "Basic Russian", "Full Russian")


def _endpoint(value: str) -> str:
    endpoint = value.strip().rstrip("/")
    parsed = urlsplit(endpoint)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Qwen TTS API endpoint has an invalid port") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Qwen TTS API endpoint must use http://127.0.0.1:<port>")
    return endpoint


def _normalization(value: str) -> str | None:
    return {"Use Backend Default": None, "Off": "off", "Basic Russian": "basic", "Full Russian": "full"}.get(value, value)


def _pronunciation(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for number, raw in enumerate((value or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Pronunciation line {number} must use source = replacement")
        source, replacement = (part.strip() for part in line.split("=", 1))
        if not source or not replacement:
            raise ValueError(f"Pronunciation line {number} has an empty value")
        result[source] = replacement
    return result


def _json_request(server: dict, path: str, payload: dict | None = None, method: str | None = None) -> tuple[Any, Any]:
    url = _endpoint(server["endpoint"]) + path
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = request.Request(url, data=data, headers=headers, method=method or ("POST" if data is not None else "GET"))
    try:
        response = request.urlopen(req, timeout=float(server["timeout"]))
        content_type = response.headers.get("Content-Type", "")
        body = response.read()
        if "json" in content_type:
            return json.loads(body.decode("utf-8")), response.headers
        return body, response.headers
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
            detail = str(parsed.get("detail") or parsed.get("error", {}).get("message") or detail)
        except (json.JSONDecodeError, AttributeError):
            pass
        raise RuntimeError(f"Qwen TTS API HTTP {exc.code}: {detail}") from exc
    except (error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Qwen TTS API unavailable at {url}: {exc}") from exc


def _temp_dir() -> Path:
    try:
        import folder_paths
        root = Path(folder_paths.get_temp_directory()) / "qwen_tts_api"
    except ImportError:
        root = Path(tempfile.gettempdir()) / "qwen_tts_api"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _load_comfy_audio(path: Path) -> dict:
    try:
        from comfy_extras.nodes_audio import load as comfy_load_audio
    except ImportError as exc:
        raise RuntimeError("ComfyUI audio loader is unavailable; update ComfyUI") from exc
    waveform, sample_rate = comfy_load_audio(str(path))
    return {"waveform": waveform.unsqueeze(0), "sample_rate": sample_rate}


def _audio_to_wav_bytes(audio: dict) -> bytes:
    if not isinstance(audio, dict) or "waveform" not in audio or "sample_rate" not in audio:
        raise ValueError("reference_audio is not a ComfyUI AUDIO object")
    waveform = audio["waveform"].detach().cpu().float().numpy()
    while waveform.ndim > 2:
        waveform = waveform[0]
    if waveform.ndim == 2:
        waveform = waveform.mean(axis=0)
    pcm = (np.nan_to_num(waveform).clip(-1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(audio["sample_rate"]))
        handle.writeframes(pcm)
    return output.getvalue()


def _ui_result(name: str, result: tuple, value: Any) -> dict:
    return {"ui": {name: [value]}, "result": result}


class QwenTTSServerNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "endpoint": ("STRING", {"default": "http://127.0.0.1:8020"}),
            "timeout": ("INT", {"default": 900, "min": 1, "max": 3600}),
            "response_format": (["wav", "mp3", "flac", "opus", "aac"], {"default": "wav"}),
        }}

    RETURN_TYPES = ("QWEN_TTS_SERVER", "STRING")
    RETURN_NAMES = ("server", "status")
    FUNCTION = "connect"
    CATEGORY = CATEGORY

    def connect(self, endpoint, timeout, response_format):
        server = {"endpoint": _endpoint(endpoint), "timeout": timeout, "model": "tts-1-ru", "response_format": response_format}
        try:
            health, _ = _json_request(server, "/health")
            status = f"ok: qwentts.cpp / {health.get('device')} / voices={health.get('voice_count')}"
        except RuntimeError as exc:
            status = f"unavailable: {exc}"
        return server, status


class QwenTTSRuntimeSettingsNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "server": ("QWEN_TTS_SERVER",),
                "apply_and_save": ("BOOLEAN", {"default": False}),
                "language": (["Russian"], {"default": "Russian"}),
                "russian_normalization": (["Off", "Basic Russian", "Full Russian"], {"default": "Full Russian"}),
                "seed": ("INT", {"default": -1, "min": -1, "max": 2147483647}),
                "max_new_tokens": ("INT", {"default": 4096, "min": 1, "max": 8192}),
                "temperature": ("FLOAT", {"default": 0.75, "min": 0.0, "max": 2.0, "step": 0.05}),
                "top_k": ("INT", {"default": 40, "min": 0, "max": 200}),
                "top_p": ("FLOAT", {"default": 0.9, "min": 0.01, "max": 1.0, "step": 0.01}),
                "repetition_penalty": ("FLOAT", {"default": 1.05, "min": 0.1, "max": 2.0, "step": 0.01}),
            },
            "optional": {
                "pronunciation_defaults": ("STRING", {"multiline": True, "default": "", "placeholder": "Qwen = куэн"}),
            },
        }

    RETURN_TYPES = ("QWEN_TTS_SERVER", "STRING")
    RETURN_NAMES = ("server", "settings_json")
    FUNCTION = "configure"
    CATEGORY = CATEGORY
    OUTPUT_NODE = True

    def configure(self, server, apply_and_save, language, russian_normalization, seed, max_new_tokens,
                  temperature, top_k, top_p, repetition_penalty, pronunciation_defaults=""):
        if apply_and_save:
            payload = {
                "language": language,
                "russian_normalization": _normalization(russian_normalization),
                "pronunciation_defaults": _pronunciation(pronunciation_defaults),
                "seed": int(seed),
                "max_new_tokens": int(max_new_tokens),
                "temperature": float(temperature),
                "top_k": int(top_k),
                "top_p": float(top_p),
                "repetition_penalty": float(repetition_penalty),
            }
            result, _ = _json_request(server, "/admin/runtime-settings", payload, method="PUT")
        else:
            result, _ = _json_request(server, "/admin/runtime-settings")
        settings = result.get("settings", result)
        rendered = json.dumps(settings, ensure_ascii=False)
        return _ui_result("qwen_tts_runtime_settings", (server, rendered), settings)


class QwenTTSCloneVoiceNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "server": ("QWEN_TTS_SERVER",),
            "reference_audio": ("AUDIO",),
            "ref_text": ("STRING", {"multiline": True}),
            "profile_name": ("STRING", {"default": "my_character"}),
            "character_name": ("STRING", {"default": "MyCharacter"}),
            "language": (["Russian"], {"default": "Russian"}),
            "overwrite": ("BOOLEAN", {"default": False}),
        }}

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("voice_id", "validation", "metadata")
    FUNCTION = "clone"
    CATEGORY = CATEGORY

    def clone(self, server, reference_audio, ref_text, profile_name, character_name, language, overwrite):
        encoded = base64.b64encode(_audio_to_wav_bytes(reference_audio)).decode("ascii")
        result, _ = _json_request(server, "/v1/audio/voice-clone", {
            "reference_audio_base64": encoded,
            "ref_text": ref_text,
            "profile_name": profile_name,
            "character_name": character_name,
            "language": language,
            "overwrite": overwrite,
        })
        if not isinstance(result, dict):
            raise RuntimeError("Qwen TTS clone response must be a JSON object")
        missing = [field for field in ("voice_id", "validation", "metadata") if field not in result]
        if missing:
            raise RuntimeError(f"Qwen TTS clone response is missing required field(s): {', '.join(missing)}")
        return result["voice_id"], json.dumps(result["validation"], ensure_ascii=False), json.dumps(result["metadata"], ensure_ascii=False)


class QwenTTSSynthesizeNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "server": ("QWEN_TTS_SERVER",),
                "text": ("STRING", {"multiline": True, "default": "Здравствуйте! Это проверка Qwen3-TTS."}),
                "voice": ("STRING", {"default": "clone:test_ru_dima_neutral"}),
                "speed": ("FLOAT", {"default": 1.0, "min": 0.25, "max": 4.0, "step": 0.05}),
                "response_format": (["wav", "mp3", "flac", "opus", "aac"], {"default": "wav"}),
                "russian_normalization": (list(NORMALIZATION_OPTIONS), {"default": NORMALIZATION_OPTIONS[0]}),
            },
            "optional": {
                "pronunciation_overrides": ("STRING", {"multiline": True, "default": "", "placeholder": "Qwen = куэн"}),
                "seed": ("INT", {"default": -1, "min": -1, "max": 2147483647}),
                "max_new_tokens": ("INT", {"default": -1, "min": -1, "max": 8192}),
                "temperature": ("FLOAT", {"default": -1.0, "min": -1.0, "max": 2.0, "step": 0.05}),
                "top_k": ("INT", {"default": -1, "min": -1, "max": 200}),
                "top_p": ("FLOAT", {"default": -1.0, "min": -1.0, "max": 1.0, "step": 0.01}),
                "repetition_penalty": ("FLOAT", {"default": -1.0, "min": -1.0, "max": 2.0, "step": 0.01}),
            },
        }

    RETURN_TYPES = ("AUDIO", "STRING", "STRING", "FLOAT")
    RETURN_NAMES = ("audio", "temporary_path", "metadata", "duration")
    FUNCTION = "synthesize"
    CATEGORY = CATEGORY
    OUTPUT_NODE = True

    def synthesize(self, server, text, voice, speed, response_format, russian_normalization,
                   pronunciation_overrides="", seed=-1, max_new_tokens=-1, temperature=-1.0,
                   top_k=-1, top_p=-1.0, repetition_penalty=-1.0):
        payload = {
            "model": "tts-1-ru",
            "voice": voice,
            "input": text,
            "speed": float(speed),
            "response_format": response_format,
            "pronunciation_overrides": _pronunciation(pronunciation_overrides),
        }
        optional = {
            "russian_normalization": _normalization(russian_normalization),
            "seed": None if int(seed) < 0 else int(seed),
            "max_new_tokens": None if int(max_new_tokens) < 0 else int(max_new_tokens),
            "temperature": None if float(temperature) < 0 else float(temperature),
            "top_k": None if int(top_k) < 0 else int(top_k),
            "top_p": None if float(top_p) < 0 else float(top_p),
            "repetition_penalty": None if float(repetition_penalty) < 0 else float(repetition_penalty),
        }
        payload.update({key: value for key, value in optional.items() if value is not None})
        body, headers = _json_request(server, "/v1/audio/speech", payload)
        unique_id = uuid.uuid4().hex
        response_path = _temp_dir() / f"qwen-tts-{unique_id}.{response_format}"
        response_path.write_bytes(body)
        audio = _load_comfy_audio(response_path)
        path = response_path
        if response_format != "wav":
            path = _temp_dir() / f"qwen-tts-{unique_id}.wav"
            path.write_bytes(_audio_to_wav_bytes(audio))
            response_path.unlink()
        duration = float(headers.get("X-Audio-Duration", audio["waveform"].shape[-1] / audio["sample_rate"]))
        metadata = json.dumps({
            "model": "tts-1-ru",
            "engine": headers.get("X-TTS-Engine", "qwentts.cpp"),
            "voice": voice,
            "format": path.suffix.removeprefix("."),
            "duration": duration,
            "node_version": VERSION,
        }, ensure_ascii=False)
        return _ui_result("qwen_tts_synthesis", (audio, str(path), metadata, duration), metadata)


class QwenTTSVoiceSelectorNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"server": ("QWEN_TTS_SERVER",), "voice": ("STRING", {"default": "clone:test_ru_dima_neutral"})}}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("voice_id", "available_voices")
    FUNCTION = "select"
    CATEGORY = CATEGORY
    OUTPUT_NODE = True

    def select(self, server, voice):
        try:
            result, _ = _json_request(server, "/v1/voices")
        except RuntimeError as exc:
            status = f"unavailable: {exc}"
            return _ui_result("qwen_tts_voices", (voice, status), status)
        voices = [item["voice_id"] for item in result.get("data", [])]
        status = ", ".join(voices)
        if voice.lower() not in {item.lower() for item in voices}:
            status = "requested voice is not available; " + status
        return _ui_result("qwen_tts_voices", (voice, status), status)


class QwenTTSModelsNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"server": ("QWEN_TTS_SERVER",)}}
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("model_ids", "models_json")
    FUNCTION = "list_models"
    CATEGORY = CATEGORY
    OUTPUT_NODE = True

    def list_models(self, server):
        try:
            result, _ = _json_request(server, "/v1/models")
        except RuntimeError as exc:
            unavailable = {"status": "unavailable", "detail": str(exc)}
            rendered = json.dumps(unavailable, ensure_ascii=False)
            return _ui_result("qwen_tts_models", ("", rendered), unavailable)
        models = result.get("data", [])
        names = ", ".join(str(item.get("id", "")) for item in models if item.get("id"))
        return _ui_result("qwen_tts_models", (names, json.dumps(models, ensure_ascii=False)), models)


class QwenTTSHealthNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"server": ("QWEN_TTS_SERVER",)}}
    RETURN_TYPES = ("BOOLEAN", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("available", "device", "model", "voices", "status_json")
    FUNCTION = "check"
    CATEGORY = CATEGORY
    OUTPUT_NODE = True

    def check(self, server):
        try:
            health, _ = _json_request(server, "/health")
            voices, _ = _json_request(server, "/v1/voices")
        except RuntimeError as exc:
            unavailable = {"status": "unavailable", "detail": str(exc)}
            result = (False, "unknown", "tts-1-ru", "", json.dumps(unavailable, ensure_ascii=False))
            return _ui_result("qwen_tts_health", result, unavailable)
        names = ", ".join(item["voice_id"] for item in voices.get("data", []))
        result = (health.get("status") == "ok", str(health.get("device")), "tts-1-ru", names, json.dumps(health, ensure_ascii=False))
        return _ui_result("qwen_tts_health", result, health)


NODE_CLASS_MAPPINGS = {
    "QwenTTSServer": QwenTTSServerNode,
    "QwenTTSRuntimeSettings": QwenTTSRuntimeSettingsNode,
    "QwenTTSSynthesize": QwenTTSSynthesizeNode,
    "QwenTTSCloneVoice": QwenTTSCloneVoiceNode,
    "QwenTTSVoiceSelector": QwenTTSVoiceSelectorNode,
    "QwenTTSModels": QwenTTSModelsNode,
    "QwenTTSHealth": QwenTTSHealthNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "QwenTTSServer": "Qwen TTS Server",
    "QwenTTSRuntimeSettings": "Qwen TTS Runtime Settings",
    "QwenTTSSynthesize": "Qwen TTS Synthesize",
    "QwenTTSCloneVoice": "Qwen TTS Clone Voice",
    "QwenTTSVoiceSelector": "Qwen TTS Voice Selector",
    "QwenTTSModels": "Qwen TTS Models",
    "QwenTTSHealth": "Qwen TTS Health",
}
