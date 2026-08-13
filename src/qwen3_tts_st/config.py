from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


def load_config(path: str | Path | None = None) -> AppConfig:
    example = PROJECT_ROOT / "config" / "config.example.yaml"
    selected = Path(path).resolve() if path else PROJECT_ROOT / "config" / "config.local.yaml"
    data = yaml.safe_load(example.read_text(encoding="utf-8")) or {}
    if selected.exists() and selected != example:
        override = yaml.safe_load(selected.read_text(encoding="utf-8")) or {}
        data = _merge(data, override)
    return AppConfig(data, selected if selected.exists() else example)
