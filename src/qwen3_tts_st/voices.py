from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class VoiceProfile:
    voice_id: str
    profile_id: str
    display_name: str
    character: str
    reference_path: Path
    ref_text: str
    language: str
    directory: Path

    @property
    def spk_path(self) -> Path:
        return self.directory / "reference.spk"

    @property
    def rvq_path(self) -> Path:
        return self.directory / "reference.rvq"

    @property
    def ready(self) -> bool:
        return self.reference_path.exists() and self.spk_path.exists() and self.rvq_path.exists() and bool(self.ref_text)

    def public(self) -> dict[str, Any]:
        return {
            "voice_id": self.voice_id,
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "character": self.character,
            "language": self.language,
            "ref_text": self.ref_text,
            "reference_available": self.reference_path.exists(),
            "spk_available": self.spk_path.exists(),
            "rvq_available": self.rvq_path.exists(),
            "ready": self.ready,
        }


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]+", "_", value.strip()).strip("_").lower()
    if not cleaned:
        raise ValueError("Profile name is empty after safe normalization")
    return cleaned


class VoiceLibrary:
    def __init__(self, root: Path, config: Any):
        self.root = root
        self.profiles_root = root / "profiles"
        self.backups_root = root / "backups"
        self.config = config
        self.profiles_root.mkdir(parents=True, exist_ok=True)
        self.profiles: dict[str, VoiceProfile] = {}
        self.reload()

    def reload(self) -> int:
        found: dict[str, VoiceProfile] = {}
        for path in self.profiles_root.rglob("metadata.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                profile_id = str(data["profile_id"])
                display_name = str(data.get("display_name") or profile_id)
                profile = VoiceProfile(
                    voice_id=f"clone:{display_name}",
                    profile_id=profile_id,
                    display_name=display_name,
                    character=str(data.get("character") or display_name),
                    reference_path=path.parent / str(data.get("reference_audio", "reference.wav")),
                    ref_text=str(data.get("ref_text", "")).strip(),
                    language=str(data.get("language", "Russian")),
                    directory=path.parent,
                )
                for key in (profile.voice_id, profile.profile_id, profile.display_name):
                    found[key.lower()] = profile
            except (OSError, KeyError, ValueError, json.JSONDecodeError):
                continue
        self.profiles = found
        return len({profile.profile_id for profile in found.values()})

    def list(self, ready_only: bool = True) -> list[dict[str, Any]]:
        unique = {profile.profile_id: profile for profile in self.profiles.values()}
        values = [profile for profile in unique.values() if profile.ready or not ready_only]
        return [profile.public() for profile in sorted(values, key=lambda item: item.display_name.lower())]

    def resolve(self, voice: str) -> VoiceProfile:
        profile = self.profiles.get(voice.lower())
        if profile is None:
            raise KeyError(f"Voice profile not found: {voice}")
        if not profile.ready:
            raise FileNotFoundError(f"Voice profile is not prepared for qwentts: {voice}")
        return profile

    def _encode(self, reference: Path) -> tuple[Path, Path]:
        executable = self.config.path("qwentts.codec_executable", "runtime/qwentts/bin/qwen-codec.exe")
        talker = self.config.path("qwentts.talker_model", "runtime/qwentts/models/qwen-talker-1.7b-base-Q8_0.gguf")
        codec = self.config.path("qwentts.codec_model", "runtime/qwentts/models/qwen-tokenizer-12hz-Q8_0.gguf")
        for required in (executable, talker, codec):
            if not required.exists():
                raise FileNotFoundError(f"Required qwentts file is missing: {required}")
        result = subprocess.run(
            [str(executable), "--model", str(codec), "--talker", str(talker), "-i", str(reference)],
            cwd=reference.parent,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"qwen-codec failed ({result.returncode}): {result.stderr[-1000:]}")
        spk, rvq = reference.with_suffix(".spk"), reference.with_suffix(".rvq")
        if not spk.exists() or not rvq.exists():
            raise RuntimeError("qwen-codec completed without reference.spk/reference.rvq")
        return spk, rvq

    @staticmethod
    async def register(profile: VoiceProfile, client: httpx.AsyncClient) -> None:
        payload = {
            "name": profile.voice_id,
            "ref_text": profile.ref_text,
            "spk_b64": base64.b64encode(profile.spk_path.read_bytes()).decode("ascii"),
            "rvq_b64": base64.b64encode(profile.rvq_path.read_bytes()).decode("ascii"),
        }
        response = await client.post("/v1/audio/voices", json=payload)
        response.raise_for_status()

    async def register_all(self, client: httpx.AsyncClient) -> int:
        unique = {profile.profile_id: profile for profile in self.profiles.values()}
        ready = [profile for profile in unique.values() if profile.ready]
        for profile in ready:
            await self.register(profile, client)
        return len(ready)

    async def create(
        self,
        source: Path,
        profile_name: str,
        character_name: str,
        ref_text: str,
        language: str,
        overwrite: bool,
        client: httpx.AsyncClient,
    ) -> VoiceProfile:
        if language != "Russian":
            raise ValueError("The active production server is configured for Russian")
        if not ref_text.strip():
            raise ValueError("Exact ref_text is required for ICL cloning")
        safe = _safe_name(profile_name)
        target = self.profiles_root / safe
        if target.exists() and any(target.iterdir()):
            if not overwrite:
                raise FileExistsError(f"Profile already exists: {profile_name}")
            backup = self.backups_root / f"{safe}-{datetime.now():%Y%m%d-%H%M%S-%f}"
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(target, backup)
        target.mkdir(parents=True, exist_ok=True)
        reference = target / "reference.wav"
        shutil.copy2(source, reference)
        self._encode(reference)
        metadata = {
            "schema": 2,
            "profile_id": profile_name,
            "display_name": profile_name,
            "character": character_name,
            "reference_audio": "reference.wav",
            "ref_text": ref_text.strip(),
            "language": language,
            "engine": "qwentts.cpp",
            "voice_assets": {"spk": "reference.spk", "rvq": "reference.rvq"},
        }
        (target / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.reload()
        profile = self.resolve(f"clone:{profile_name}")
        await self.register(profile, client)
        return profile
