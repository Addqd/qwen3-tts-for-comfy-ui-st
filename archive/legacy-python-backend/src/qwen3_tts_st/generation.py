from __future__ import annotations

from typing import Any

from .models import ModelSpec


GENERATION_PRESETS: dict[str, dict[str, Any]] = {
    "default": {},
    "stable_russian": {
        "do_sample": True,
        "temperature": 0.75,
        "top_k": 40,
        "top_p": 0.90,
        "repetition_penalty": 1.05,
        "subtalker_dosample": True,
        "subtalker_temperature": 0.75,
        "subtalker_top_k": 40,
        "subtalker_top_p": 0.90,
    },
}

ALLOWED_GENERATION_KWARGS = frozenset(
    {
        "do_sample",
        "temperature",
        "top_k",
        "top_p",
        "repetition_penalty",
        "subtalker_dosample",
        "subtalker_temperature",
        "subtalker_top_k",
        "subtalker_top_p",
        "max_new_tokens",
    }
)


def generation_kwargs(config: Any, preset: str, spec: ModelSpec) -> dict[str, Any]:
    if preset not in GENERATION_PRESETS:
        raise ValueError(f"неизвестный generation preset: {preset}")
    result = dict(GENERATION_PRESETS[preset])
    configured = config.get(f"generation.presets.{preset}", {}) or {}
    if not isinstance(configured, dict):
        raise ValueError(f"generation.presets.{preset} должен быть mapping")
    unknown = sorted(set(configured) - ALLOWED_GENERATION_KWARGS)
    if unknown:
        raise ValueError(f"неподдерживаемые generation параметры: {', '.join(unknown)}")
    result.update(configured)
    max_tokens = spec.runtime.get("max_new_tokens", config.get("model.max_new_tokens", 2048))
    result.setdefault("max_new_tokens", int(max_tokens))
    return result
