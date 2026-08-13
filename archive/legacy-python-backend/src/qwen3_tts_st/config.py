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


def _apply_legacy_model_overrides(data: dict[str, Any], override: dict[str, Any]) -> None:
    """Map explicitly supplied single-model keys into the default registry entry."""

    legacy = override.get("model")
    if not isinstance(legacy, dict):
        return
    models = data.get("models")
    if not isinstance(models, dict):
        return
    default_key = str(models.get("default", ""))
    available = models.get("available")
    if not default_key or not isinstance(available, dict) or not isinstance(available.get(default_key), dict):
        return

    modern = override.get("models") if isinstance(override.get("models"), dict) else {}
    modern_available = modern.get("available") if isinstance(modern.get("available"), dict) else {}
    modern_spec = modern_available.get(default_key) if isinstance(modern_available.get(default_key), dict) else {}
    modern_runtime = modern_spec.get("runtime") if isinstance(modern_spec.get("runtime"), dict) else {}
    spec = available[default_key]
    runtime = spec.setdefault("runtime", {})

    if "id" in legacy and "hf_id" not in modern_spec:
        spec["hf_id"] = legacy["id"]
    for key in ("dtype", "attention", "max_new_tokens"):
        if key in legacy and key not in modern_runtime:
            runtime[key] = legacy[key]
    if "cache_dir" in legacy and "cache_dir" not in modern:
        models["cache_dir"] = legacy["cache_dir"]


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
            override = yaml.safe_load(handle) or {}
        data = _merge(data, override)
        _apply_legacy_model_overrides(data, override)
    return AppConfig(data, selected if selected.exists() else example)
