from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import re
import tempfile
import time
from typing import Any
from urllib import error, request
from urllib.parse import urlsplit
import wave

import numpy as np


CATEGORY = "Qwen TTS API"
VERSION = "0.6.0"
INHERIT_SERVER_MODEL = "Inherit Server model"
SERVER_MODEL_OPTIONS = (
    "Backend Default (tts-1-ru)",
    "0.6B Fast (tts-1-ru-fast)",
    "1.7B Quality (tts-1-ru-quality)",
    "0.6B Russian Tuned (tts-1-ru-fast-tuned)",
    "1.7B Russian Tuned (tts-1-ru-quality-tuned)",
)
SYNTH_MODEL_OPTIONS = (INHERIT_SERVER_MODEL, *SERVER_MODEL_OPTIONS)
# Compatibility for callers that imported the former public constant.
MODEL_OPTIONS = SERVER_MODEL_OPTIONS
MODEL_ALIASES = {
    INHERIT_SERVER_MODEL: "",
    SERVER_MODEL_OPTIONS[0]: "tts-1-ru",
    SERVER_MODEL_OPTIONS[1]: "tts-1-ru-fast",
    SERVER_MODEL_OPTIONS[2]: "tts-1-ru-quality",
    SERVER_MODEL_OPTIONS[3]: "tts-1-ru-fast-tuned",
    SERVER_MODEL_OPTIONS[4]: "tts-1-ru-quality-tuned",
    "tts-1-ru": "tts-1-ru",
    "tts-1-ru-fast": "tts-1-ru-fast",
    "tts-1-ru-quality": "tts-1-ru-quality",
    "tts-1-ru-fast-tuned": "tts-1-ru-fast-tuned",
    "tts-1-ru-quality-tuned": "tts-1-ru-quality-tuned",
}
BACKEND_DEFAULT = "Use Backend Default"
GENERATION_PRESET_OPTIONS = (BACKEND_DEFAULT, "Default", "Stable Russian")
NORMALIZATION_OPTIONS = (BACKEND_DEFAULT, "Off", "Basic Russian", "Full Russian")
MULTILINGUAL_OPTIONS = (BACKEND_DEFAULT, "Auto Russian + English", "Off (Russian only)")
CHUNKING_OPTIONS = (BACKEND_DEFAULT, "Semantic / prosody-aware", "Off")
ALLOWED_STYLES = (
    "neutral", "soft", "whisper", "breathy", "happy", "sad", "angry", "tense", "pleasure", "intimate"
)
SOUND_TYPES = ("laugh", "giggle", "gasp", "sigh", "pant", "moan")
TAG_RE = re.compile(r"\[voice:([a-z][a-z0-9_-]*)\]", re.I)
SERVICE_TAG_RE = re.compile(r"\[\s*voice(?=\s|:|\])(?:\s*:\s*|\s+)?([^\]\r\n]*)\]", re.I)
UNTERMINATED_TAG_RE = re.compile(r"\[\s*voice(?=\s|:)(?:\s*:\s*|\s+)[a-z0-9_-]*", re.I)
SOUND_TAG_RE = re.compile(r"\[sound:([a-z][a-z0-9_-]*)\]", re.I)
SOUND_SERVICE_TAG_RE = re.compile(r"\[\s*sound(?=\s|:|\])(?:\s*:\s*|\s+)?([^\]\r\n]*)\]", re.I)
UNTERMINATED_SOUND_TAG_RE = re.compile(r"\[\s*sound(?=\s|:)(?:\s*:\s*|\s+)[a-z0-9_-]*", re.I)


def _is_escaped(text: str, position: int) -> bool:
    backslashes = 0
    position -= 1
    while position >= 0 and text[position] == "\\":
        backslashes += 1
        position -= 1
    return bool(backslashes % 2)


def _strip_service_tags(text: str) -> str:
    value = UNTERMINATED_TAG_RE.sub("", SERVICE_TAG_RE.sub("", text))
    value = UNTERMINATED_SOUND_TAG_RE.sub("", SOUND_SERVICE_TAG_RE.sub("", value))
    return value.strip()


def _tag_at(text: str, position: int):
    strict = TAG_RE.match(text, position)
    if strict:
        requested = strict.group(1).lower()
        if requested in ALLOWED_STYLES:
            return strict.end(), "voice_tag", requested, []
        return strict.end(), "voice_tag", "neutral", ["unknown_voice_tag_neutral_fallback"]
    sound = SOUND_TAG_RE.match(text, position)
    if sound:
        requested = sound.group(1).lower()
        if requested in SOUND_TYPES:
            return sound.end(), "sound", requested, []
        return sound.end(), "service", "", [f"unknown_sound_tag_removed:{requested}"]
    malformed = SERVICE_TAG_RE.match(text, position)
    if malformed:
        return malformed.end(), "voice_tag", "neutral", ["malformed_voice_tag_neutral_fallback"]
    unterminated = UNTERMINATED_TAG_RE.match(text, position)
    if unterminated:
        return unterminated.end(), "voice_tag", "neutral", ["unterminated_voice_tag_removed"]
    malformed_sound = SOUND_SERVICE_TAG_RE.match(text, position)
    if malformed_sound:
        return malformed_sound.end(), "service", "", ["malformed_sound_tag_removed"]
    unterminated_sound = UNTERMINATED_SOUND_TAG_RE.match(text, position)
    if unterminated_sound:
        return unterminated_sound.end(), "service", "", ["unterminated_sound_tag_removed"]
    return None


def _quote_aware_segments(text: str):
    """Lightweight mirror of the backend parser for ComfyUI preview only."""

    tokens = []
    cursor = position = 0
    while position < len(text):
        if text[position] == "[":
            tag = _tag_at(text, position)
            if tag:
                if cursor < position:
                    tokens.append(("text", text[cursor:position], None, []))
                position, kind, tag_value, warnings = tag
                tokens.append((kind, "", tag_value, warnings))
                cursor = position
                continue
        if text[position] == '"' and not _is_escaped(text, position):
            closing = None
            for candidate in range(position + 1, len(text)):
                if text[candidate] == '"' and not _is_escaped(text, candidate):
                    closing = candidate
                    break
            if cursor < position:
                tokens.append(("text", text[cursor:position], None, []))
            if closing is None:
                tokens.append(("text", text[position + 1 :], None, ["unclosed_dialogue_treated_as_neutral"]))
                cursor = len(text)
                break
            tokens.append(("dialogue", text[position + 1 : closing], None, []))
            position = closing + 1
            cursor = position
            continue
        position += 1
    if cursor < len(text):
        tokens.append(("text", text[cursor:], None, []))

    segments = []
    warnings = []
    pending_style = None
    for kind, value, token_value, token_warnings in tokens:
        warnings.extend(token_warnings)
        if kind == "voice_tag":
            if pending_style is not None:
                warnings.append("multiple_voice_tags_last_wins")
            pending_style = token_value
            continue
        if kind == "sound":
            segments.append({"kind": "sound", "style": "neutral", "text": "", "sound_type": token_value})
            continue
        if kind == "service":
            continue
        clean = _strip_service_tags(value).replace('\\"', '"').strip()
        if kind == "dialogue":
            if clean:
                style = pending_style or "neutral"
                if segments and segments[-1]["kind"] == "dialogue" and segments[-1]["style"] == style:
                    segments[-1]["text"] = f'{segments[-1]["text"]} {clean}'.strip()
                else:
                    segments.append({"kind": "dialogue", "style": style, "text": clean})
            else:
                warnings.append("empty_dialogue_ignored")
            pending_style = None
        elif clean:
            if pending_style is not None:
                warnings.append("voice_tag_ignored_no_following_quoted_dialogue")
                pending_style = None
            if segments and segments[-1]["kind"] == "narration" and segments[-1]["style"] == "neutral":
                segments[-1]["text"] = f'{segments[-1]["text"]} {clean}'.strip()
            else:
                segments.append({"kind": "narration", "style": "neutral", "text": clean})
    if pending_style is not None:
        warnings.append("voice_tag_ignored_no_following_quoted_dialogue")
    for index, segment in enumerate(segments):
        if segment["kind"] != "sound":
            continue
        previous = next((item for item in reversed(segments[:index]) if item["kind"] != "sound"), None)
        following = next((item for item in segments[index + 1 :] if item["kind"] != "sound"), None)
        context = previous or following
        if context is not None:
            segment["style"] = context["style"]
            segment["preferred_style"] = context["style"]
    return segments, list(dict.fromkeys(warnings))


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


def _model_alias(value: str | None, server: dict) -> str:
    raw = (value or "").strip()
    selected = MODEL_ALIASES.get(raw, raw)
    return (selected or str(server.get("model") or "tts-1-ru")).strip()


def _generation_preset(value: str) -> str | None:
    return {BACKEND_DEFAULT: None, "Default": "default", "Stable Russian": "stable_russian"}.get(value, value)


def _normalization_mode(value: str) -> str | None:
    return {BACKEND_DEFAULT: None, "Off": "off", "Basic Russian": "basic", "Full Russian": "full"}.get(value, value)


def _multilingual_mode(value: str) -> str | None:
    return {BACKEND_DEFAULT: None, "Auto Russian + English": "auto", "Off (Russian only)": "off"}.get(value, value)


def _chunking_mode(value: str) -> str | None:
    return {BACKEND_DEFAULT: None, "Semantic / prosody-aware": "semantic", "Off": "off"}.get(value, value)


def _pronunciation_overrides(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for number, raw_line in enumerate((value or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Pronunciation overrides line {number} must use source = replacement")
        source, replacement = (part.strip() for part in line.split("=", 1))
        if not source or not replacement:
            raise ValueError(f"Pronunciation overrides line {number} has an empty source or replacement")
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
            parsed_detail = json.loads(detail)
            detail = str(parsed_detail.get("detail") or parsed_detail.get("error", {}).get("message") or detail)
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
    waveform = np.nan_to_num(waveform).clip(-1.0, 1.0)
    pcm = (waveform * 32767.0).astype("<i2").tobytes()
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
            "model": (list(SERVER_MODEL_OPTIONS), {"default": SERVER_MODEL_OPTIONS[0]}),
            "response_format": (["wav", "mp3", "flac", "opus", "aac"], {"default": "wav"}),
        }}

    RETURN_TYPES = ("QWEN_TTS_SERVER", "STRING")
    RETURN_NAMES = ("server", "status")
    FUNCTION = "connect"
    CATEGORY = CATEGORY

    def connect(self, endpoint, timeout, model, response_format):
        server = {"endpoint": _endpoint(endpoint), "timeout": timeout, "model": _model_alias(model, {}), "response_format": response_format}
        try:
            health, _ = _json_request(server, "/health")
            status = f"ok: {health.get('mode')} / {health.get('device')} / voices={health.get('voice_count')}"
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
                "active_model": (list(SERVER_MODEL_OPTIONS), {"default": SERVER_MODEL_OPTIONS[0]}),
                "generation_preset": (["Default", "Stable Russian"], {"default": "Stable Russian"}),
                "russian_normalization": (["Off", "Basic Russian", "Full Russian"], {"default": "Full Russian"}),
                "multilingual_mode": (["Auto Russian + English", "Off (Russian only)"], {"default": "Auto Russian + English"}),
                "chunking_mode": (["Semantic / prosody-aware", "Off"], {"default": "Semantic / prosody-aware"}),
                "leading_silence_ms": ("INT", {"default": 100, "min": 0, "max": 2000}),
                "trailing_silence_ms": ("INT", {"default": 150, "min": 0, "max": 2000}),
            },
            "optional": {
                "pronunciation_defaults": (
                    "STRING",
                    {"multiline": True, "default": "", "placeholder": "Qwen = куэн\nComfyUI = комфи ю ай"},
                ),
            },
        }

    RETURN_TYPES = ("QWEN_TTS_SERVER", "STRING")
    RETURN_NAMES = ("server", "settings_json")
    FUNCTION = "configure"
    CATEGORY = CATEGORY
    OUTPUT_NODE = True

    def configure(
        self,
        server,
        apply_and_save,
        active_model,
        generation_preset,
        russian_normalization,
        multilingual_mode,
        chunking_mode,
        leading_silence_ms,
        trailing_silence_ms,
        pronunciation_defaults="",
    ):
        if apply_and_save:
            payload = {
                "active_model": _model_alias(active_model, {}),
                "generation_preset": _generation_preset(generation_preset),
                "russian_normalization": _normalization_mode(russian_normalization),
                "multilingual_mode": _multilingual_mode(multilingual_mode),
                "chunking_mode": _chunking_mode(chunking_mode),
                "leading_silence_ms": int(leading_silence_ms),
                "trailing_silence_ms": int(trailing_silence_ms),
                "pronunciation_defaults": _pronunciation_overrides(pronunciation_defaults),
            }
            result, _ = _json_request(server, "/admin/runtime-settings", payload, method="PUT")
        else:
            result, _ = _json_request(server, "/admin/runtime-settings")
        settings = result.get("settings", result)
        rendered = json.dumps(settings, ensure_ascii=False)
        return _ui_result("qwen_tts_runtime_settings", (server, rendered), settings)


class QwenTTSSynthesizeNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "server": ("QWEN_TTS_SERVER",),
                "text": ("STRING", {"multiline": True, "default": "Здравствуйте! Это проверка Qwen3-TTS."}),
                "voice": ("STRING", {"default": "clone:QwenDemoRussianNeutral"}),
                "speed": ("FLOAT", {"default": 1.0, "min": 0.25, "max": 4.0, "step": 0.05}),
                "model": (list(SYNTH_MODEL_OPTIONS), {"default": INHERIT_SERVER_MODEL}),
                "response_format": (["wav", "mp3", "flac", "opus", "aac"], {"default": "wav"}),
                "preprocessing_mode": (["all", "direct_speech"], {"default": "all"}),
            },
            "optional": {
                # Keep emotion_script first for migration of version 0.3 workflows.
                "emotion_script": ("STRING", {"multiline": True, "default": ""}),
                "generation_preset": (list(GENERATION_PRESET_OPTIONS), {"default": BACKEND_DEFAULT}),
                "russian_normalization": (list(NORMALIZATION_OPTIONS), {"default": BACKEND_DEFAULT}),
                "pronunciation_overrides": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "placeholder": "Qwen = куэн\nComfyUI = комфи ю ай",
                    },
                ),
                "multilingual_mode": (list(MULTILINGUAL_OPTIONS), {"default": BACKEND_DEFAULT}),
                "chunking_mode": (list(CHUNKING_OPTIONS), {"default": BACKEND_DEFAULT}),
                "leading_silence_ms": ("INT", {"default": -1, "min": -1, "max": 2000}),
                "trailing_silence_ms": ("INT", {"default": -1, "min": -1, "max": 2000}),
            },
        }

    RETURN_TYPES = ("AUDIO", "STRING", "STRING", "FLOAT")
    RETURN_NAMES = ("audio", "temporary_path", "metadata", "duration")
    FUNCTION = "synthesize"
    CATEGORY = CATEGORY
    OUTPUT_NODE = True

    def synthesize(
        self,
        server,
        text,
        voice,
        speed,
        model,
        response_format,
        preprocessing_mode,
        emotion_script="",
        generation_preset=BACKEND_DEFAULT,
        russian_normalization=BACKEND_DEFAULT,
        pronunciation_overrides="",
        multilingual_mode=BACKEND_DEFAULT,
        chunking_mode=BACKEND_DEFAULT,
        leading_silence_ms=-1,
        trailing_silence_ms=-1,
    ):
        spoken = emotion_script.strip() or text
        payload = {
            "model": _model_alias(model, server),
            "voice": voice,
            "input": spoken,
            "speed": speed,
            "response_format": response_format,
            "preprocessing_mode": preprocessing_mode,
            "pronunciation_overrides": _pronunciation_overrides(pronunciation_overrides),
        }
        optional_values = {
            "generation_preset": _generation_preset(generation_preset),
            "russian_normalization": _normalization_mode(russian_normalization),
            "multilingual_mode": _multilingual_mode(multilingual_mode),
            "chunking_mode": _chunking_mode(chunking_mode),
            "leading_silence_ms": None if int(leading_silence_ms) < 0 else int(leading_silence_ms),
            "trailing_silence_ms": None if int(trailing_silence_ms) < 0 else int(trailing_silence_ms),
        }
        payload.update({key: value for key, value in optional_values.items() if value is not None})
        body, headers = _json_request(server, "/v1/audio/speech", payload)
        stamp = int(time.time() * 1000)
        response_path = _temp_dir() / f"qwen-tts-{stamp}.{response_format}"
        response_path.write_bytes(body)
        audio = _load_comfy_audio(response_path)
        path = response_path
        if response_format != "wav":
            path = _temp_dir() / f"qwen-tts-{stamp}.wav"
            path.write_bytes(_audio_to_wav_bytes(audio))
        duration = float(headers.get("X-Audio-Duration", audio["waveform"].shape[-1] / audio["sample_rate"]))
        metadata = json.dumps(
            {
                "requested_model": payload["model"],
                "resolved_model": headers.get("X-TTS-Resolved-Model"),
                "generation_preset": headers.get("X-TTS-Generation-Preset", payload.get("generation_preset", "backend_default")),
                "russian_normalization": headers.get("X-TTS-Russian-Normalization", payload.get("russian_normalization", "backend_default")),
                "multilingual_mode": headers.get("X-TTS-Multilingual-Mode", payload.get("multilingual_mode", "backend_default")),
                "chunking_mode": headers.get("X-TTS-Chunking-Mode", payload.get("chunking_mode", "backend_default")),
                "pronunciation_override_count": len(payload["pronunciation_overrides"]),
                "voice": voice,
                "format": response_format,
                "sample_rate": audio["sample_rate"],
                "duration": duration,
                "node_version": VERSION,
            },
            ensure_ascii=False,
        )
        result = (audio, str(path), metadata, duration)
        return _ui_result("qwen_tts_synthesis", result, metadata)


class QwenTTSCloneVoiceNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "server": ("QWEN_TTS_SERVER",),
            "reference_audio": ("AUDIO",),
            "ref_text": ("STRING", {"multiline": True}),
            "profile_name": ("STRING", {"default": "CharacterNeutral"}),
            "character_name": ("STRING", {"default": "Character"}),
            "style": (list(ALLOWED_STYLES), {"default": "neutral"}),
            "language": (["Russian"], {"default": "Russian"}),
            "clone_mode": (["icl", "x_vector"], {"default": "icl"}),
            "consent_confirmed": ("BOOLEAN", {"default": False}),
            "overwrite": ("BOOLEAN", {"default": False}),
            "emotion_enabled": ("BOOLEAN", {"default": True}),
            "sound_enabled": ("BOOLEAN", {"default": False}),
            "sound_laugh": ("BOOLEAN", {"default": False}),
            "sound_giggle": ("BOOLEAN", {"default": False}),
            "sound_gasp": ("BOOLEAN", {"default": False}),
            "sound_sigh": ("BOOLEAN", {"default": False}),
            "sound_pant": ("BOOLEAN", {"default": False}),
            "sound_moan": ("BOOLEAN", {"default": False}),
        }}

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("voice_id", "validation", "metadata")
    FUNCTION = "clone"
    CATEGORY = CATEGORY

    def clone(
        self,
        server,
        reference_audio,
        ref_text,
        profile_name,
        character_name,
        style,
        language,
        clone_mode,
        consent_confirmed,
        overwrite,
        emotion_enabled=True,
        sound_enabled=False,
        sound_laugh=False,
        sound_giggle=False,
        sound_gasp=False,
        sound_sigh=False,
        sound_pant=False,
        sound_moan=False,
    ):
        if not consent_confirmed:
            raise ValueError("Confirm permission to use this voice before cloning")
        selected_sounds = [
            sound
            for sound, enabled in zip(
                SOUND_TYPES,
                (sound_laugh, sound_giggle, sound_gasp, sound_sigh, sound_pant, sound_moan),
                strict=True,
            )
            if enabled
        ]
        if sound_enabled and not selected_sounds:
            raise ValueError("Enable at least one sound capability")
        if not emotion_enabled and not sound_enabled:
            raise ValueError("Enable at least one emotion or sound profile capability")
        encoded = base64.b64encode(_audio_to_wav_bytes(reference_audio)).decode("ascii")
        result, _ = _json_request(server, "/v1/audio/voice-clone", {
            "reference_audio_base64": encoded, "ref_text": ref_text, "profile_name": profile_name,
            "character_name": character_name, "style": style,
            "emotion_enabled": bool(emotion_enabled),
            "sound_enabled": bool(sound_enabled),
            "sounds": selected_sounds if sound_enabled else [],
            "language": language,
            "clone_mode": clone_mode, "consent_confirmed": True, "overwrite": overwrite,
        })
        return result["voice_id"], json.dumps(result["validation"], ensure_ascii=False), json.dumps(result["metadata"], ensure_ascii=False)


class QwenTTSVoiceSelectorNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"server": ("QWEN_TTS_SERVER",), "voice": ("STRING", {"default": "clone:QwenDemoRussianNeutral"})}}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("voice_id", "available_voices")
    FUNCTION = "select"
    CATEGORY = CATEGORY
    OUTPUT_NODE = True

    def select(self, server, voice):
        try:
            result, _ = _json_request(server, "/v1/voices")
            voices = [item["voice_id"] for item in result.get("data", [])]
        except RuntimeError as exc:
            result = (voice, f"backend unavailable: {exc}")
            return _ui_result("qwen_tts_voices", result, result[1])
        if voice not in voices and voice.lower() not in [item.lower() for item in voices]:
            result = (voice, "requested voice is not currently available; " + ", ".join(voices))
            return _ui_result("qwen_tts_voices", result, result[1])
        result = (voice, ", ".join(voices))
        return _ui_result("qwen_tts_voices", result, result[1])


class QwenTTSEmotionScriptNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"text": ("STRING", {"multiline": True}), "character_profile_mapping": ("STRING", {"multiline": True, "default": "{}"})}}

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("normalized_script", "segments", "clean_text", "recognized_styles")
    FUNCTION = "parse"
    CATEGORY = CATEGORY
    OUTPUT_NODE = True

    def parse(self, text, character_profile_mapping):
        mapping = json.loads(character_profile_mapping or "{}")
        if not isinstance(mapping, dict):
            raise ValueError("character_profile_mapping must be a JSON object")
        segments, warnings = _quote_aware_segments(text)
        for item in segments:
            item["voice"] = None if item["kind"] == "sound" else mapping.get(item["style"]) or mapping.get("neutral")
        normalized_parts = []
        for item in segments:
            if item["kind"] == "sound":
                normalized_parts.append(f"[sound:{item['sound_type']}]")
            elif item["kind"] == "dialogue":
                escaped = item["text"].replace('"', '\\"')
                prefix = "" if item["style"] == "neutral" else f"[voice:{item['style']}] "
                normalized_parts.append(f'{prefix}"{escaped}"')
            else:
                normalized_parts.append(item["text"])
        normalized = "\n".join(normalized_parts)
        clean = " ".join(item["text"] for item in segments if item["text"])
        styles = ", ".join(dict.fromkeys(item["style"] for item in segments if item["kind"] != "sound"))
        result = (normalized, json.dumps(segments, ensure_ascii=False), clean, styles)
        return _ui_result("qwen_tts_emotion", result, {"segments": segments, "clean_text": clean, "styles": styles, "warnings": warnings})


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
            models = result.get("data", [])
            names = ", ".join(str(item.get("id", "")) for item in models if item.get("id"))
            result_tuple = (names, json.dumps(models, ensure_ascii=False))
            return _ui_result("qwen_tts_models", result_tuple, models)
        except RuntimeError as exc:
            result_tuple = ("", f"backend unavailable: {exc}")
            return _ui_result("qwen_tts_models", result_tuple, result_tuple[1])


class QwenTTSHealthNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"server": ("QWEN_TTS_SERVER",)}}

    RETURN_TYPES = ("BOOLEAN", "STRING", "STRING", "STRING", "INT", "STRING")
    RETURN_NAMES = ("available", "device", "model", "voices", "queue_length", "resources")
    FUNCTION = "check"
    CATEGORY = CATEGORY
    OUTPUT_NODE = True

    def check(self, server):
        try:
            health, _ = _json_request(server, "/health")
            voices, _ = _json_request(server, "/v1/voices")
            names = ", ".join(item["voice_id"] for item in voices.get("data", []))
            result = (True, str(health.get("device")), str(health.get("model")), names, int(health.get("queue_waiting", 0)), json.dumps(health.get("resources", {}), ensure_ascii=False))
            return _ui_result("qwen_tts_health", result, health)
        except RuntimeError as exc:
            result = (False, "unknown", "unknown", "", 0, str(exc))
            return _ui_result("qwen_tts_health", result, {"status": "unavailable", "error": str(exc)})


NODE_CLASS_MAPPINGS = {
    "QwenTTSServer": QwenTTSServerNode,
    "QwenTTSRuntimeSettings": QwenTTSRuntimeSettingsNode,
    "QwenTTSSynthesize": QwenTTSSynthesizeNode,
    "QwenTTSCloneVoice": QwenTTSCloneVoiceNode,
    "QwenTTSVoiceSelector": QwenTTSVoiceSelectorNode,
    "QwenTTSEmotionScript": QwenTTSEmotionScriptNode,
    "QwenTTSModels": QwenTTSModelsNode,
    "QwenTTSHealth": QwenTTSHealthNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "QwenTTSServer": "Qwen TTS Server",
    "QwenTTSRuntimeSettings": "Qwen TTS Runtime Settings",
    "QwenTTSSynthesize": "Qwen TTS Synthesize",
    "QwenTTSCloneVoice": "Qwen TTS Clone Voice",
    "QwenTTSVoiceSelector": "Qwen TTS Voice Selector",
    "QwenTTSEmotionScript": "Qwen TTS Emotion Script",
    "QwenTTSModels": "Qwen TTS Models",
    "QwenTTSHealth": "Qwen TTS Health",
}
