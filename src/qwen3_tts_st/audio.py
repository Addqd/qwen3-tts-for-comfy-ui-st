from __future__ import annotations

import io
import subprocess

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly


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


def stitch(parts: list[tuple[np.ndarray, int]], crossfade_ms: int = 8) -> tuple[np.ndarray, int]:
    """Join internal synthesis parts without inserting artificial silence."""

    if not parts:
        raise ValueError("нет аудиосегментов для объединения")
    target_rate = parts[0][1]
    prepared = [resample(wav, rate, target_rate) for wav, rate in parts]
    output = prepared[0]
    fade_size = int(target_rate * crossfade_ms / 1000)
    for part in prepared[1:]:
        overlap = min(fade_size, len(output), len(part))
        if overlap > 0:
            fade_in = np.linspace(0.0, 1.0, overlap, dtype=np.float32)
            mixed = output[-overlap:] * (1.0 - fade_in) + part[:overlap] * fade_in
            output = np.concatenate((output[:-overlap], mixed, part[overlap:]))
        else:
            output = np.concatenate((output, part))
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
