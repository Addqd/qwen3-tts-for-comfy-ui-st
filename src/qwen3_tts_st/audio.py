from __future__ import annotations

from dataclasses import dataclass
import io
import subprocess
from typing import Any

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly


@dataclass(frozen=True)
class AudioPart:
    waveform: np.ndarray
    sample_rate: int
    kind: str = "speech"
    profile_id: str = ""


def normalize_waveform(waveform: np.ndarray) -> np.ndarray:
    value = np.asarray(waveform, dtype=np.float32)
    if value.ndim == 2:
        value = value.mean(axis=1 if value.shape[0] > value.shape[1] else 0)
    return np.nan_to_num(value, nan=0.0, posinf=1.0, neginf=-1.0).clip(-1.0, 1.0)


def resample(waveform: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return normalize_waveform(waveform)
    from math import gcd

    divisor = gcd(source_rate, target_rate)
    return resample_poly(normalize_waveform(waveform), target_rate // divisor, source_rate // divisor).astype(np.float32)


def _coerce_part(part: tuple[np.ndarray, int] | AudioPart) -> AudioPart:
    return part if isinstance(part, AudioPart) else AudioPart(part[0], part[1])


def _clean_internal_edges(waveform: np.ndarray, sample_rate: int, config: dict[str, Any]) -> np.ndarray:
    value = normalize_waveform(waveform).copy()
    dc_threshold = float(config.get("dc_offset_threshold", 0.01))
    offset = float(np.mean(value)) if len(value) else 0.0
    if abs(offset) >= dc_threshold:
        value = value - offset

    window = min(len(value), max(0, int(sample_rate * float(config.get("edge_window_ms", 40)) / 1000)))
    if not window:
        return normalize_waveform(value)
    threshold = float(config.get("edge_silence_threshold", 0.0025))
    minimum = max(1, int(sample_rate * float(config.get("edge_min_silence_ms", 12)) / 1000))
    safety = max(0, int(sample_rate * float(config.get("edge_safety_ms", 4)) / 1000))
    start = 0
    leading_activity = np.flatnonzero(np.abs(value[:window]) > threshold)
    if leading_activity.size and int(leading_activity[0]) >= minimum:
        start = max(0, int(leading_activity[0]) - safety)
    end = len(value)
    trailing_activity = np.flatnonzero(np.abs(value[-window:]) > threshold)
    if trailing_activity.size:
        trailing = window - 1 - int(trailing_activity[-1])
        if trailing >= minimum:
            end = min(len(value), len(value) - trailing + safety)
    return normalize_waveform(value[start:end])


def _transition_key(left: AudioPart, right: AudioPart) -> str:
    if left.kind == "speech" and right.kind == "speech":
        return "same_profile_speech" if left.profile_id and left.profile_id == right.profile_id else "different_emotion_speech"
    if left.kind == "speech" and right.kind == "sound":
        return "speech_to_sound"
    if left.kind == "sound" and right.kind == "speech":
        return "sound_to_speech"
    return "sound_to_sound"


def _match_boundary_level(left: np.ndarray, right: np.ndarray, sample_rate: int, config: dict[str, Any]) -> np.ndarray:
    window = max(1, int(sample_rate * float(config.get("level_window_ms", 20)) / 1000))
    left_rms = float(np.sqrt(np.mean(np.square(left[-window:])))) if len(left) else 0.0
    right_rms = float(np.sqrt(np.mean(np.square(right[:window])))) if len(right) else 0.0
    floor = float(config.get("level_rms_floor", 0.003))
    if left_rms < floor or right_rms < floor:
        return right
    requested_db = 20.0 * np.log10(left_rms / right_rms)
    limit_db = max(0.0, float(config.get("max_gain_correction_db", 2.5)))
    correction_db = float(np.clip(requested_db, -limit_db, limit_db))
    return (right * (10.0 ** (correction_db / 20.0))).astype(np.float32)


def stitch(
    parts: list[tuple[np.ndarray, int] | AudioPart],
    crossfade_ms: int = 8,
    boundary_config: dict[str, Any] | None = None,
) -> tuple[np.ndarray, int]:
    """Join generated pieces with deterministic conservative equal-power boundaries."""

    if not parts:
        raise ValueError("нет аудиосегментов для объединения")
    source_parts = [_coerce_part(part) for part in parts]
    target_rate = source_parts[0].sample_rate
    config = dict(boundary_config or {})
    prepared = []
    for part in source_parts:
        waveform = resample(part.waveform, part.sample_rate, target_rate)
        if boundary_config is not None:
            waveform = _clean_internal_edges(waveform, target_rate, config)
        prepared.append(AudioPart(waveform, target_rate, part.kind, part.profile_id))
    output = prepared[0].waveform
    previous = prepared[0]
    transitions = dict(config.get("crossfade_ms", {}) or {})
    for current in prepared[1:]:
        part = current.waveform
        if boundary_config is not None:
            part = _match_boundary_level(output, part, target_rate, config)
        transition_ms = float(transitions.get(_transition_key(previous, current), crossfade_ms))
        fade_size = int(target_rate * transition_ms / 1000)
        overlap = min(fade_size, len(output), len(part))
        if overlap > 0:
            phase = (
                np.array([np.pi / 4], dtype=np.float32)
                if overlap == 1
                else np.linspace(0.0, np.pi / 2, overlap, dtype=np.float32)
            )
            mixed = output[-overlap:] * np.cos(phase) + part[:overlap] * np.sin(phase)
            output = np.concatenate((output[:-overlap], mixed, part[overlap:]))
        else:
            output = np.concatenate((output, part))
        previous = current
    return output.clip(-1.0, 1.0), target_rate


def change_speed(waveform: np.ndarray, speed: float) -> np.ndarray:
    if abs(speed - 1.0) < 1e-6:
        return waveform
    import librosa

    return librosa.effects.time_stretch(normalize_waveform(waveform), rate=speed).astype(np.float32)


def fade_edges(waveform: np.ndarray, sample_rate: int, fade_ms: int = 5) -> np.ndarray:
    value = normalize_waveform(waveform).copy()
    size = min(len(value) // 2, max(0, int(sample_rate * fade_ms / 1000)))
    if size:
        fade = np.linspace(0.0, 1.0, size, dtype=np.float32)
        value[:size] *= fade
        value[-size:] *= fade[::-1]
    return value


def pad_edges(
    waveform: np.ndarray,
    sample_rate: int,
    leading_silence_ms: int = 100,
    trailing_silence_ms: int = 150,
) -> np.ndarray:
    """Pad a complete utterance once, strictly at its absolute edges."""

    leading = np.zeros(max(0, int(sample_rate * leading_silence_ms / 1000)), dtype=np.float32)
    trailing = np.zeros(max(0, int(sample_rate * trailing_silence_ms / 1000)), dtype=np.float32)
    return np.concatenate((leading, normalize_waveform(waveform), trailing))


def wav_bytes(waveform: np.ndarray, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, normalize_waveform(waveform), sample_rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


MIME_TYPES = {"wav": "audio/wav", "mp3": "audio/mpeg", "flac": "audio/flac", "opus": "audio/opus", "aac": "audio/aac"}


def encode(waveform: np.ndarray, sample_rate: int, response_format: str) -> tuple[bytes, str]:
    fmt = response_format.lower()
    source = wav_bytes(waveform, sample_rate)
    if fmt == "wav":
        return source, MIME_TYPES[fmt]
    if fmt not in MIME_TYPES:
        raise ValueError(f"неподдерживаемый response_format: {fmt}")
    codecs = {"mp3": "libmp3lame", "flac": "flac", "opus": "libopus", "aac": "aac"}
    container = "adts" if fmt == "aac" else fmt
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-c:a", codecs[fmt], "-f", container, "pipe:1"],
        input=source,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg не смог кодировать {fmt}: {result.stderr.decode('utf-8', errors='replace')[-500:]}")
    return result.stdout, MIME_TYPES[fmt]
