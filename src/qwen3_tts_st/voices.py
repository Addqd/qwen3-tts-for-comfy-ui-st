from __future__ import annotations

import json
import math
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import re

import numpy as np
import soundfile as sf


@dataclass
class VoiceProfile:
    voice_id: str
    character: str
    profile_id: str
    display_name: str
    style: str
    reference_audio: str
    ref_text: str
    language: str
    clone_mode: str
    directory: Path
    notes: str = ""

    def public(self) -> dict:
        result = asdict(self)
        result.pop("directory")
        result["reference_available"] = self.reference_path.exists()
        return result

    @property
    def reference_path(self) -> Path:
        return self.directory / self.reference_audio


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]+", "_", value.strip()).strip("_")
    if not cleaned:
        raise ValueError("имя профиля пусто после безопасной нормализации")
    return cleaned.lower()


def validate_audio(path: Path, ref_text: str = "") -> dict:
    try:
        data, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    except Exception as exc:
        return {"valid": False, "errors": [f"не удалось прочитать аудио: {exc}"], "warnings": []}
    mono = data.mean(axis=1)
    duration = len(mono) / sample_rate if sample_rate else 0.0
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    rms = float(math.sqrt(float(np.mean(np.square(mono))))) if len(mono) else 0.0
    edge = max(1, min(len(mono) // 10, sample_rate // 2))
    noise = np.concatenate((mono[:edge], mono[-edge:])) if len(mono) else np.array([0.0])
    noise_rms = float(math.sqrt(float(np.mean(np.square(noise)))))
    snr_db = 20 * math.log10(max(rms, 1e-9) / max(noise_rms, 1e-9))
    errors: list[str] = []
    warnings: list[str] = []
    if duration < 1.0:
        errors.append("референс короче 1 секунды")
    if duration > 30.0:
        warnings.append("референс длиннее 30 секунд; рекомендуется 3–15 секунд")
    if sample_rate < 16000:
        warnings.append("sample rate ниже 16 kHz")
    if data.shape[1] > 1:
        warnings.append("стерео будет сведено в моно моделью")
    if peak >= 0.999:
        warnings.append("обнаружен возможный clipping")
    if rms < 0.005:
        errors.append("аудио слишком тихое")
    if snr_db < 10:
        warnings.append("оценочный уровень фонового шума высок")
    if not ref_text.strip():
        errors.append("для ICL требуется точная транскрипция ref_text")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "duration_seconds": round(duration, 3),
        "sample_rate": sample_rate,
        "channels": data.shape[1],
        "peak": round(peak, 5),
        "rms": round(rms, 5),
        "estimated_snr_db": round(snr_db, 2),
    }


class VoiceLibrary:
    def __init__(self, root: Path):
        self.root = root
        self.profiles_root = root / "profiles"
        self.profiles_root.mkdir(parents=True, exist_ok=True)
        self.profiles: dict[str, VoiceProfile] = {}
        self.reload()

    def reload(self) -> int:
        found: dict[str, VoiceProfile] = {}
        for metadata_path in self.profiles_root.rglob("metadata.json"):
            try:
                data = json.loads(metadata_path.read_text(encoding="utf-8"))
                display = str(data["display_name"])
                profile = VoiceProfile(
                    voice_id=f"clone:{display}",
                    character=str(data["character"]),
                    profile_id=str(data["profile_id"]),
                    display_name=display,
                    style=str(data.get("style", "neutral")),
                    reference_audio=str(data.get("reference_audio", "reference.wav")),
                    ref_text=str(data.get("ref_text", "")),
                    language=str(data.get("language", "Russian")),
                    clone_mode=str(data.get("clone_mode", "icl")),
                    notes=str(data.get("notes", "")),
                    directory=metadata_path.parent,
                )
                found[profile.voice_id.lower()] = profile
                found[profile.profile_id.lower()] = profile
                found[profile.display_name.lower()] = profile
            except (KeyError, ValueError, json.JSONDecodeError):
                continue
        self.profiles = found
        return len({profile.profile_id for profile in found.values()})

    def list(self) -> list[dict]:
        unique = {profile.profile_id: profile for profile in self.profiles.values()}
        return [profile.public() for profile in sorted(unique.values(), key=lambda item: item.display_name.lower())]

    def resolve(self, voice: str, fallback: str | None = None) -> VoiceProfile:
        profile = self.profiles.get(voice.lower())
        if profile is None and fallback:
            profile = self.profiles.get(fallback.lower())
        if profile is None:
            raise KeyError(f"голосовой профиль не найден: {voice}")
        if not profile.reference_path.exists():
            raise FileNotFoundError(f"у профиля {profile.display_name} отсутствует reference.wav")
        return profile

    def find_style(self, character: str, style: str, fallback: VoiceProfile) -> VoiceProfile:
        unique = {profile.profile_id: profile for profile in self.profiles.values()}.values()
        for profile in unique:
            if profile.character.lower() == character.lower() and profile.style.lower() == style.lower() and profile.reference_path.exists():
                return profile
        return fallback

    def resolve_family_neutral(self, selected: VoiceProfile, configured_fallback: str | None = None) -> VoiceProfile:
        """Return the deterministic neutral base for a selected voice family."""

        neutral = self.find_style(selected.character, "neutral", selected)
        if neutral.style.lower() == "neutral" and neutral.reference_path.exists():
            return neutral

        if configured_fallback:
            safe = self.resolve(configured_fallback)
            safe_neutral = self.find_style(safe.character, "neutral", safe)
            if safe_neutral.style.lower() == "neutral" and safe_neutral.reference_path.exists():
                return safe_neutral

        raise KeyError(
            f"для voice family {selected.character} отсутствует neutral-профиль и безопасный fallback"
        )

    def create(self, source: Path, metadata: dict, overwrite: bool = False) -> tuple[VoiceProfile, dict]:
        validation = validate_audio(source, str(metadata.get("ref_text", "")))
        if not validation["valid"]:
            raise ValueError("; ".join(validation["errors"]))
        character_dir = _safe_name(str(metadata["character"]))
        style_dir = _safe_name(str(metadata.get("style", "neutral")))
        target = self.profiles_root / character_dir / style_dir
        if target.exists() and any(target.iterdir()):
            if not overwrite:
                raise FileExistsError(f"профиль уже существует: {target}")
            backup = target.parent / f"{target.name}.backup-{datetime.now():%Y%m%d-%H%M%S}"
            shutil.copytree(target, backup)
        target.mkdir(parents=True, exist_ok=True)
        reference = target / "reference.wav"
        shutil.copy2(source, reference)
        payload = {
            "character": metadata["character"],
            "profile_id": metadata.get("profile_id") or f"{character_dir}_{style_dir}",
            "display_name": metadata.get("display_name") or f"{metadata['character']}{style_dir.title()}",
            "style": metadata.get("style", "neutral"),
            "reference_audio": "reference.wav",
            "ref_text": metadata["ref_text"],
            "language": metadata.get("language", "Russian"),
            "clone_mode": metadata.get("clone_mode", "icl"),
            "notes": metadata.get("notes", ""),
        }
        (target / "metadata.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.reload()
        return self.resolve(f"clone:{payload['display_name']}"), validation
