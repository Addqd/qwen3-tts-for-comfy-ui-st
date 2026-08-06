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
        host = str(self.get("server.host", "127.0.0.1"))
        if host != "127.0.0.1":
            raise ValueError("Безопасность: server.host должен быть строго 127.0.0.1")

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
    with example.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if selected.exists() and selected != example:
        with selected.open("r", encoding="utf-8") as handle:
            data = _merge(data, yaml.safe_load(handle) or {})
    return AppConfig(data, selected if selected.exists() else example)

