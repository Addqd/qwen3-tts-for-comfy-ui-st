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
        self.qwentts_model()

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

    def qwentts_model(self) -> tuple[str, Path, Path]:
        variant = str(self.get("qwentts.active_model", "bf16")).strip().lower()
        if variant not in {"bf16", "q8"}:
            raise ValueError(f"Unknown qwentts.active_model: {variant}. Supported values: bf16, q8")
        prefix = f"qwentts.models.{variant}"
        talker = self.get(f"{prefix}.talker_model")
        codec = self.get(f"{prefix}.codec_model")
        if not talker or not codec:
            raise ValueError(f"qwentts model registry entry is incomplete: {variant}")
        return variant, self.path(f"{prefix}.talker_model", ""), self.path(f"{prefix}.codec_model", "")

    def configured_qwentts_variant(self) -> str:
        override: dict[str, Any] = {}
        if self.source.exists():
            override = yaml.safe_load(self.source.read_text(encoding="utf-8")) or {}
        variant = str(override.get("qwentts", {}).get("active_model", self.qwentts_model()[0])).strip().lower()
        if variant not in {"bf16", "q8"}:
            raise ValueError(f"Unknown qwentts.active_model: {variant}. Supported values: bf16, q8")
        return variant

    def persist_qwentts_variant(self, variant: str) -> str:
        selected = variant.strip().lower()
        if selected not in {"bf16", "q8"}:
            raise ValueError(f"Unknown qwentts.active_model: {selected}. Supported values: bf16, q8")
        override: dict[str, Any] = {}
        if self.source.exists():
            override = yaml.safe_load(self.source.read_text(encoding="utf-8")) or {}
        override.setdefault("qwentts", {})["active_model"] = selected
        self.source.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(f"{self.source}.tmp")
        temporary.write_text(yaml.safe_dump(override, allow_unicode=True, sort_keys=False), encoding="utf-8")
        temporary.replace(self.source)
        return selected


def load_config(path: str | Path | None = None) -> AppConfig:
    example = PROJECT_ROOT / "config" / "config.example.yaml"
    selected = Path(path).resolve() if path else PROJECT_ROOT / "config" / "config.local.yaml"
    data = yaml.safe_load(example.read_text(encoding="utf-8")) or {}
    if selected.exists() and selected != example:
        override = yaml.safe_load(selected.read_text(encoding="utf-8")) or {}
        data = _merge(data, override)
    return AppConfig(data, selected)
