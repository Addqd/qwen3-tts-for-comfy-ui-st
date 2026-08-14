from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
QWENTTS_MODEL_DIR = PROJECT_ROOT / "runtime" / "qwentts" / "models"
QWENTTS_BIN_DIR = PROJECT_ROOT / "runtime" / "qwentts" / "bin"
QWENTTS_MANIFEST = PROJECT_ROOT / "config" / "qwentts-runtime.json"
QWENTTS_BF16_FILES = (
    "qwen-talker-1.7b-base-BF16.gguf",
    "qwen-tokenizer-12hz-BF16.gguf",
)
QWENTTS_EXECUTABLE_FILES = ("tts-server.exe", "qwen-codec.exe")
QWENTTS_BACKEND = "CUDA0"
QWENTTS_MODEL_ID = "tts-1-ru"
QWENTTS_LANGUAGE = "Russian"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


class AppConfig:
    def __init__(self, data: dict[str, Any], source: Path):
        self.data = data
        self.source = source
        for key in ("server.host", "qwentts.host", "comfyui.host"):
            if str(self.get(key, "127.0.0.1")) != "127.0.0.1":
                raise ValueError(f"Security: {key} must be exactly 127.0.0.1")
        self.qwentts_manifest()

    def get(self, dotted: str, default: Any = None) -> Any:
        current: Any = self.data
        for part in dotted.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def path(self, dotted: str, default: str) -> Path:
        value = Path(str(self.get(dotted, default)))
        return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    def qwentts_manifest(self) -> dict[str, Any]:
        try:
            manifest = json.loads(QWENTTS_MANIFEST.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("schema") != 3:
                raise TypeError("qwentts manifest schema must be 3")
            upstream = manifest["upstream"]
            models = manifest["models"]
            files = manifest["files"]
            nvidia = manifest["nvidia"]
            if not all(isinstance(value, dict) for value in (upstream, models, files, nvidia)):
                raise TypeError("qwentts manifest mappings must be objects")
            model_files = models["files"]
            if not isinstance(model_files, dict):
                raise TypeError("qwentts model files must be an object")
            if set(model_files) != set(QWENTTS_BF16_FILES):
                raise ValueError("qwentts manifest must contain only the production BF16 pair")
            if any(name not in files for name in QWENTTS_EXECUTABLE_FILES):
                raise ValueError("qwentts manifest is missing a production executable")
            digests = [*files.values(), *model_files.values(), nvidia["archive_sha256"]]
            if not all(isinstance(value, str) and _SHA256_RE.fullmatch(value) for value in digests):
                raise ValueError("qwentts manifest contains an invalid SHA-256 digest")
            revisions = (upstream["revision"], models["revision"])
            if not all(isinstance(value, str) and _REVISION_RE.fullmatch(value) for value in revisions):
                raise ValueError("qwentts manifest contains an invalid pinned revision")
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("The pinned qwentts runtime manifest is invalid") from exc
        return manifest

    def qwentts_models(self) -> tuple[Path, Path]:
        self.qwentts_manifest()
        return tuple(QWENTTS_MODEL_DIR / name for name in QWENTTS_BF16_FILES)

    def qwentts_executables(self) -> tuple[Path, Path]:
        self.qwentts_manifest()
        return tuple(QWENTTS_BIN_DIR / name for name in QWENTTS_EXECUTABLE_FILES)

    def qwentts_backend(self) -> str:
        return QWENTTS_BACKEND

    def qwentts_model_id(self) -> str:
        return QWENTTS_MODEL_ID

    def qwentts_language(self) -> str:
        return QWENTTS_LANGUAGE


def load_config(path: str | Path | None = None) -> AppConfig:
    example = PROJECT_ROOT / "config" / "config.example.yaml"
    selected = Path(path).resolve() if path else PROJECT_ROOT / "config" / "config.local.yaml"
    data = yaml.safe_load(example.read_text(encoding="utf-8")) or {}
    if selected.exists() and selected != example:
        override = yaml.safe_load(selected.read_text(encoding="utf-8")) or {}
        data = _merge(data, override)
    return AppConfig(data, selected)
