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

from .emotion import ALLOWED_SOUNDS, ALLOWED_STYLES, SOUND_TYPES


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
    emotion_enabled: bool = True
    emotion: str = "neutral"
    sound_enabled: bool = False
    sounds: tuple[str, ...] = ()

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


def _normalize_sounds(value) -> tuple[str, ...]:
    if value is None:
        requested = []
    elif isinstance(value, str):
        requested = [item.strip().lower() for item in value.split(",") if item.strip()]
    else:
        requested = [str(item).strip().lower() for item in value if str(item).strip()]
    unknown = sorted(set(requested) - ALLOWED_SOUNDS)
    if unknown:
        raise ValueError(f"unsupported sound capabilities: {', '.join(unknown)}")
    return tuple(sound for sound in SOUND_TYPES if sound in requested)


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
        self.backups_root = root / "backups"
        self.profiles_root.mkdir(parents=True, exist_ok=True)
        self.backups_root.mkdir(parents=True, exist_ok=True)
        self.profiles: dict[str, VoiceProfile] = {}
        self.reload()

    def _is_legacy_backup(self, metadata_path: Path) -> bool:
        relative = metadata_path.relative_to(self.profiles_root)
        return any(".backup-" in part.lower() for part in relative.parts[:-1])

    def reload(self) -> int:
        found: dict[str, VoiceProfile] = {}
        for metadata_path in self.profiles_root.rglob("metadata.json"):
            if self._is_legacy_backup(metadata_path):
                continue
            try:
                data = json.loads(metadata_path.read_text(encoding="utf-8"))
                display = str(data["display_name"])
                emotion = str(data.get("emotion", data.get("style", "neutral"))).lower()
                if emotion not in ALLOWED_STYLES:
                    raise ValueError(f"unsupported emotion capability: {emotion}")
                sounds = _normalize_sounds(data.get("sounds", []))
                profile = VoiceProfile(
                    voice_id=f"clone:{display}",
                    character=str(data["character"]),
                    profile_id=str(data["profile_id"]),
                    display_name=display,
                    style=emotion,
                    reference_audio=str(data.get("reference_audio", "reference.wav")),
                    ref_text=str(data.get("ref_text", "")),
                    language=str(data.get("language", "Russian")),
                    clone_mode=str(data.get("clone_mode", "icl")),
                    notes=str(data.get("notes", "")),
                    directory=metadata_path.parent,
                    emotion_enabled=bool(data.get("emotion_enabled", True)),
                    emotion=emotion,
                    sound_enabled=bool(data.get("sound_enabled", bool(sounds))),
                    sounds=sounds,
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
        unique = sorted(
            {profile.profile_id: profile for profile in self.profiles.values()}.values(),
            key=lambda item: (item.profile_id.lower(), item.display_name.lower()),
        )
        for profile in unique:
            if (
                profile.character.lower() == character.lower()
                and profile.emotion_enabled
                and profile.emotion.lower() == style.lower()
                and profile.reference_path.exists()
            ):
                return profile
        return fallback

    def find_sound(self, character: str, sound_type: str, preferred_emotion: str | None = None) -> VoiceProfile | None:
        requested = sound_type.strip().lower()
        if requested not in ALLOWED_SOUNDS:
            raise ValueError(f"unsupported sound capability: {sound_type}")
        candidates = sorted(
            (
                profile
                for profile in {item.profile_id: item for item in self.profiles.values()}.values()
                if profile.character.lower() == character.lower()
                and profile.sound_enabled
                and requested in profile.sounds
                and profile.reference_path.exists()
            ),
            key=lambda item: (item.profile_id.lower(), item.display_name.lower()),
        )
        if preferred_emotion:
            matching = [
                profile
                for profile in candidates
                if profile.emotion_enabled and profile.emotion.lower() == preferred_emotion.lower()
            ]
            if matching:
                return matching[0]
        return candidates[0] if candidates else None

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
        emotion = str(metadata.get("emotion", metadata.get("style", "neutral"))).lower()
        if emotion not in ALLOWED_STYLES:
            raise ValueError(f"unsupported emotion capability: {emotion}")
        emotion_enabled = bool(metadata.get("emotion_enabled", True))
        sounds = _normalize_sounds(metadata.get("sounds", []))
        sound_enabled = bool(metadata.get("sound_enabled", bool(sounds)))
        if sound_enabled and not sounds:
            raise ValueError("sound profile is enabled but no sound capabilities were selected")
        if not emotion_enabled and not sound_enabled:
            raise ValueError("enable at least one emotion or sound profile capability")
        if not sound_enabled:
            sounds = ()
        style_dir = _safe_name(emotion)
        profile_id = str(metadata.get("profile_id") or f"{character_dir}_{style_dir}")
        target_dir = style_dir if emotion_enabled else _safe_name(profile_id)
        target = self.profiles_root / character_dir / target_dir
        if target.exists() and any(target.iterdir()):
            if not overwrite:
                raise FileExistsError(f"профиль уже существует: {target}")
            backup_parent = self.backups_root / character_dir
            backup_parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup = backup_parent / f"{style_dir}-{timestamp}"
            shutil.copytree(target, backup)
        target.mkdir(parents=True, exist_ok=True)
        reference = target / "reference.wav"
        shutil.copy2(source, reference)
        payload = {
            "character": metadata["character"],
            "profile_id": profile_id,
            "display_name": metadata.get("display_name") or f"{metadata['character']}{style_dir.title()}",
            "style": emotion,
            "emotion_enabled": emotion_enabled,
            "emotion": emotion,
            "sound_enabled": sound_enabled,
            "sounds": list(sounds),
            "reference_audio": "reference.wav",
            "ref_text": metadata["ref_text"],
            "language": metadata.get("language", "Russian"),
            "clone_mode": metadata.get("clone_mode", "icl"),
            "notes": metadata.get("notes", ""),
        }
        (target / "metadata.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.reload()
        return self.resolve(f"clone:{payload['display_name']}"), validation
