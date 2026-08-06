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


def stitch(parts: list[tuple[np.ndarray, int]], pause_ms: int = 120, crossfade_ms: int = 8) -> tuple[np.ndarray, int]:
    if not parts:
        raise ValueError("нет аудиосегментов для объединения")
    target_rate = parts[0][1]
    prepared = [resample(wav, rate, target_rate) for wav, rate in parts]
    output = prepared[0]
    silence = np.zeros(int(target_rate * pause_ms / 1000), dtype=np.float32)
    fade_size = int(target_rate * crossfade_ms / 1000)
    for part in prepared[1:]:
        if fade_size > 0 and len(output) >= fade_size and len(part) >= fade_size:
            fade_out = np.linspace(1.0, 0.0, fade_size, dtype=np.float32)
            fade_in = 1.0 - fade_out
            output[-fade_size:] = output[-fade_size:] * fade_out
            part[:fade_size] = part[:fade_size] * fade_in
        output = np.concatenate((output, silence, part))
    return output.clip(-1.0, 1.0), target_rate


def change_speed(waveform: np.ndarray, speed: float) -> np.ndarray:
    if abs(speed - 1.0) < 1e-6:
        return waveform
    import librosa

    return librosa.effects.time_stretch(normalize_waveform(waveform), rate=speed).astype(np.float32)


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

