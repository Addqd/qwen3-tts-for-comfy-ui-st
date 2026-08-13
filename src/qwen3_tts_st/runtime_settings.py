from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .normalization import parse_pronunciation_overrides


class RuntimeSettingsStore:
    def __init__(self, config: Any):
        self.path = config.path("runtime.settings_file", "runtime/tts-settings.json")
        self.defaults = {
            "language": "Russian",
            "russian_normalization": str(config.get("runtime_defaults.russian_normalization", "full")),
            "pronunciation_defaults": dict(config.get("runtime_defaults.pronunciation_defaults", {}) or {}),
            "seed": int(config.get("runtime_defaults.seed", -1)),
            "max_new_tokens": int(config.get("runtime_defaults.max_new_tokens", 4096)),
            "temperature": float(config.get("runtime_defaults.temperature", 0.75)),
            "top_k": int(config.get("runtime_defaults.top_k", 40)),
            "top_p": float(config.get("runtime_defaults.top_p", 0.9)),
            "repetition_penalty": float(config.get("runtime_defaults.repetition_penalty", 1.05)),
        }
        loaded: dict[str, Any] = {}
        if self.path.exists():
            try:
                candidate = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if isinstance(candidate, dict):
                    loaded = {key: candidate[key] for key in self.defaults if key in candidate}
                self._settings = self._validate({**self.defaults, **loaded})
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                loaded = {}
                self._settings = self._validate(self.defaults)
        else:
            self._settings = self._validate(self.defaults)

    def _validate(self, value: dict[str, Any]) -> dict[str, Any]:
        result = dict(value)
        if result.get("language") != "Russian":
            raise ValueError("This production runtime uses the Russian qwentts language route")
        if result.get("russian_normalization") not in {"off", "basic", "full"}:
            raise ValueError("russian_normalization must be off, basic, or full")
        result["pronunciation_defaults"] = parse_pronunciation_overrides(result.get("pronunciation_defaults"))
        result["seed"] = int(result["seed"])
        result["max_new_tokens"] = int(result["max_new_tokens"])
        result["temperature"] = float(result["temperature"])
        result["top_k"] = int(result["top_k"])
        result["top_p"] = float(result["top_p"])
        result["repetition_penalty"] = float(result["repetition_penalty"])
        if result["max_new_tokens"] <= 0 or result["top_k"] < 0:
            raise ValueError("Invalid qwentts integer sampling setting")
        if result["temperature"] < 0 or not 0 < result["top_p"] <= 1 or result["repetition_penalty"] <= 0:
            raise ValueError("Invalid qwentts sampling setting")
        return result

    def current(self) -> dict[str, Any]:
        return dict(self._settings)

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        unknown = set(changes) - set(self.defaults)
        if unknown:
            raise ValueError(f"Unknown runtime settings: {', '.join(sorted(unknown))}")
        self._settings = self._validate({**self._settings, **changes})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(f"{self.path}.tmp")
        temporary.write_text(json.dumps(self._settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
        return self.current()
