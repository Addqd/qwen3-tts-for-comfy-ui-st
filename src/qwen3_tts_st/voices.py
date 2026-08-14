from __future__ import annotations

import asyncio
import base64
import json
import re
import shutil
import subprocess
import tempfile
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
        return self.directory / "variants" / "bf16" / "reference.spk"

    @property
    def rvq_path(self) -> Path:
        return self.directory / "variants" / "bf16" / "reference.rvq"

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
        self.talker_model, self.codec_model = config.qwentts_models()
        self.profiles_root.mkdir(parents=True, exist_ok=True)
        self.profiles: dict[str, VoiceProfile] = {}
        self._create_lock = asyncio.Lock()
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

    @staticmethod
    def _record_variant(directory: Path, variant: str) -> None:
        metadata_path = directory / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        voice_assets = metadata.setdefault("voice_assets", {})
        variants = voice_assets.setdefault("variants", {})
        variants[variant] = {
            "spk": f"variants/{variant}/reference.spk",
            "rvq": f"variants/{variant}/reference.rvq",
        }
        temporary = directory / "metadata.json.tmp"
        temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(metadata_path)

    def _encode(self, reference: Path, variant_dir: Path) -> tuple[Path, Path]:
        executable = self.config.path("qwentts.codec_executable", "runtime/qwentts/bin/qwen-codec.exe")
        for required in (executable, self.talker_model, self.codec_model):
            if not required.exists():
                raise FileNotFoundError(f"Required qwentts BF16 file is missing: {required}")
        variant_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="qwentts-bf16-", dir=reference.parent) as folder:
            working = Path(folder) / "reference.wav"
            shutil.copy2(reference, working)
            result = subprocess.run(
                [str(executable), "--model", str(self.codec_model), "--talker", str(self.talker_model), "-i", str(working)],
                cwd=working.parent,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"qwen-codec failed ({result.returncode}): {result.stderr[-1000:]}")
            spk, rvq = working.with_suffix(".spk"), working.with_suffix(".rvq")
            if not spk.exists() or not rvq.exists():
                raise RuntimeError("qwen-codec completed without reference.spk/reference.rvq")
            shutil.move(spk, variant_dir / "reference.spk")
            shutil.move(rvq, variant_dir / "reference.rvq")
        return variant_dir / "reference.spk", variant_dir / "reference.rvq"

    async def _prepare(self, profile: VoiceProfile) -> VoiceProfile:
        if not profile.ready and profile.reference_path.exists() and profile.ref_text:
            await asyncio.to_thread(self._encode, profile.reference_path, profile.spk_path.parent)
            self._record_variant(profile.directory, "bf16")
        return profile

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
        ready = [await self._prepare(profile) for profile in unique.values()]
        ready = [profile for profile in ready if profile.ready]
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
        async with self._create_lock:
            if language != "Russian":
                raise ValueError("The active production server is configured for Russian")
            if not ref_text.strip():
                raise ValueError("Exact ref_text is required for ICL cloning")
            safe = _safe_name(profile_name)
            target = self.profiles_root / safe
            target_populated = target.exists() and any(target.iterdir())
            if target_populated and not overwrite:
                raise FileExistsError(f"Profile already exists: {profile_name}")

            previous = next(
                (profile for profile in set(self.profiles.values()) if profile.directory == target and profile.ready),
                None,
            )
            staging = Path(tempfile.mkdtemp(prefix=f".{safe}-staging-", dir=self.profiles_root))
            backup: Path | None = None
            try:
                reference = staging / "reference.wav"
                shutil.copy2(source, reference)
                variant_dir = staging / "variants" / "bf16"
                await asyncio.to_thread(self._encode, reference, variant_dir)
                metadata = {
                    "schema": 2,
                    "profile_id": profile_name,
                    "display_name": profile_name,
                    "character": character_name,
                    "reference_audio": "reference.wav",
                    "ref_text": ref_text.strip(),
                    "language": language,
                    "engine": "qwentts.cpp",
                    "voice_assets": {
                        "variants": {"bf16": {"spk": "variants/bf16/reference.spk", "rvq": "variants/bf16/reference.rvq"}},
                    },
                }
                (staging / "metadata.json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

                if target.exists():
                    backup = self.backups_root / f"{safe}-{datetime.now():%Y%m%d-%H%M%S-%f}"
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    target.replace(backup)
                try:
                    staging.replace(target)
                except Exception:
                    if backup is not None and backup.exists() and not target.exists():
                        backup.replace(target)
                    raise

                try:
                    self.reload()
                    replacement = self.resolve(f"clone:{profile_name}")
                    await self.register(replacement, client)
                except Exception as registration_error:
                    failed = self.profiles_root / f".{safe}-failed-{datetime.now():%Y%m%d-%H%M%S-%f}"
                    rollback_error = None
                    try:
                        target.replace(failed)
                        if backup is not None:
                            backup.replace(target)
                        shutil.rmtree(failed, ignore_errors=True)
                    except OSError as exc:
                        rollback_error = exc
                    self.reload()
                    if rollback_error is None and previous is not None:
                        try:
                            restored = self.resolve(previous.profile_id)
                            await self.register(restored, client)
                        except Exception as exc:  # preserve the original registration failure
                            rollback_error = exc
                    if rollback_error is not None:
                        raise RuntimeError(
                            f"Replacement registration failed and runtime rollback failed: {rollback_error}"
                        ) from registration_error
                    raise
                return replacement
            finally:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
