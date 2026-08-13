from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .normalization import parse_pronunciation_overrides


class RuntimeSettingsStore:
    """Validated local quality defaults shared by every backend client."""

    def __init__(self, config: Any, model_aliases: list[str], generation_presets: list[str]):
        self.config = config
        self.model_aliases = set(model_aliases)
        self.generation_presets = set(generation_presets)
        self.path = config.path("runtime.settings_file", "runtime/tts-settings.json")
        self.defaults = {
            "active_model": str(config.get("runtime_defaults.active_model", "tts-1-ru")),
            "generation_preset": str(
                config.get("runtime_defaults.generation_preset", config.get("request_defaults.generation_preset", "default"))
            ),
            "russian_normalization": str(
                config.get("runtime_defaults.russian_normalization", config.get("request_defaults.russian_normalization", "off"))
            ),
            "multilingual_mode": str(config.get("runtime_defaults.multilingual_mode", "auto")),
            "chunking_mode": str(config.get("runtime_defaults.chunking_mode", "semantic")),
            "leading_silence_ms": int(config.get("runtime_defaults.leading_silence_ms", 100)),
            "trailing_silence_ms": int(config.get("runtime_defaults.trailing_silence_ms", 150)),
            "pronunciation_defaults": dict(config.get("pronunciation.dictionary", {}) or {}),
        }
        self._settings = self._validate(self.defaults)
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"runtime settings повреждены: {exc}") from exc
            if not isinstance(loaded, dict):
                raise ValueError("runtime settings должны быть JSON object")
            self._settings = self._validate({**self.defaults, **loaded})

    def _validate(self, value: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(value)
        if result["active_model"] not in self.model_aliases:
            raise ValueError(f"неизвестная active_model: {result['active_model']}")
        if result["generation_preset"] not in self.generation_presets:
            raise ValueError(f"неизвестный generation_preset: {result['generation_preset']}")
        if result["russian_normalization"] not in {"off", "basic", "full"}:
            raise ValueError(f"неизвестный russian_normalization: {result['russian_normalization']}")
        if result["multilingual_mode"] not in {"off", "auto"}:
            raise ValueError(f"неизвестный multilingual_mode: {result['multilingual_mode']}")
        if result["chunking_mode"] not in {"off", "semantic"}:
            raise ValueError(f"неизвестный chunking_mode: {result['chunking_mode']}")
        for key in ("leading_silence_ms", "trailing_silence_ms"):
            result[key] = int(result[key])
            if not 0 <= result[key] <= 2000:
                raise ValueError(f"{key} должен быть в диапазоне 0..2000")
        result["pronunciation_defaults"] = parse_pronunciation_overrides(result.get("pronunciation_defaults"))
        return result

    def current(self) -> dict[str, Any]:
        return copy.deepcopy(self._settings)

    def resolve_model(self, requested: str | None) -> str:
        value = (requested or "tts-1-ru").strip()
        if value.lower() != "tts-1-ru":
            return value
        active = str(self._settings["active_model"])
        return active if active.lower() != "tts-1-ru" else "tts-1-ru"

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        unknown = set(changes) - set(self.defaults)
        if unknown:
            raise ValueError(f"неизвестные runtime settings: {', '.join(sorted(unknown))}")
        updated = self._validate({**self._settings, **changes})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(str(self.path) + ".tmp")
        temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
        self._settings = updated
        return self.current()
