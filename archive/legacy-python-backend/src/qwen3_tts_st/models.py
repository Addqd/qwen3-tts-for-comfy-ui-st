from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT


ALLOWED_DTYPES = frozenset({"auto", "float16", "float32"})
ALLOWED_ATTENTION = frozenset({"sdpa", "eager"})
ALLOWED_RUNTIME_MODES = frozenset({"inherit", "auto", "cpu", "cuda", "cuda_on_demand"})


@dataclass(frozen=True)
class ModelSpec:
    key: str
    hf_id: str
    aliases: tuple[str, ...]
    runtime: dict[str, Any]
    local_path: Path | None = None


@dataclass(frozen=True)
class ResolvedModel:
    requested_alias: str
    spec: ModelSpec

    @property
    def canonical(self) -> str:
        return self.spec.key

    @property
    def hf_id(self) -> str:
        return self.spec.hf_id


class ModelRegistry:
    """Validated model aliases without loading Qwen or touching the network."""

    def __init__(self, config: Any):
        self.config = config
        available = config.get("models.available", {}) or {}
        if not isinstance(available, dict) or not available:
            available = self._legacy_available()

        self.specs: dict[str, ModelSpec] = {}
        self.aliases: dict[str, ModelSpec] = {}
        for raw_key, raw_value in available.items():
            key = str(raw_key).strip().lower()
            value = raw_value or {}
            if not key or not isinstance(value, dict):
                raise ValueError("models.available должен содержать mapping model-key -> config")
            local_value = str(value.get("local_path", "")).strip()
            local_path = None
            if local_value:
                candidate = Path(local_value)
                local_path = candidate.resolve() if candidate.is_absolute() else (PROJECT_ROOT / candidate).resolve()
                hf_id = str(local_path)
            else:
                hf_id = str(value.get("hf_id", "")).strip()
                if not hf_id:
                    raise ValueError(f"для модели {key} не задан hf_id или local_path")
            aliases = tuple(str(item).strip() for item in value.get("aliases", []) if str(item).strip())
            runtime = dict(value.get("runtime", {}) or {})
            self._validate_runtime(key, runtime)
            spec = ModelSpec(key=key, hf_id=hf_id, aliases=aliases, runtime=runtime, local_path=local_path)
            self.specs[key] = spec
            self._register(key, spec)
            for alias in aliases:
                self._register(alias, spec)

        default_key = str(config.get("models.default", next(iter(self.specs)))).strip().lower()
        if default_key not in self.specs:
            raise ValueError(f"models.default указывает на неизвестную модель: {default_key}")
        self.default_key = default_key
        self.default_spec = self.specs[default_key]
        self._register("tts-1-ru", self.default_spec, replace=True)

    def _legacy_available(self) -> dict[str, Any]:
        return {
            "qwen3-tts-0.6b": {
                "hf_id": str(self.config.get("model.id", "Qwen/Qwen3-TTS-12Hz-0.6B-Base")),
                "aliases": ["tts-1-ru-fast"],
                "runtime": {
                    "dtype": self.config.get("model.dtype", "float32"),
                    "attention": self.config.get("model.attention", "sdpa"),
                    "max_new_tokens": self.config.get("model.max_new_tokens", 2048),
                },
            }
        }

    def _register(self, alias: str, spec: ModelSpec, replace: bool = False) -> None:
        normalized = alias.strip().lower()
        existing = self.aliases.get(normalized)
        if existing is not None and existing.key != spec.key and not replace:
            raise ValueError(f"model alias {alias} назначен нескольким моделям")
        self.aliases[normalized] = spec

    @staticmethod
    def _validate_runtime(key: str, runtime: dict[str, Any]) -> None:
        dtype = str(runtime.get("dtype", "auto")).lower()
        attention = str(runtime.get("attention", "sdpa")).lower()
        mode = str(runtime.get("mode", "inherit")).lower()
        if dtype not in ALLOWED_DTYPES:
            raise ValueError(f"неподдерживаемый dtype для {key}: {dtype}")
        if attention not in ALLOWED_ATTENTION:
            raise ValueError(f"неподдерживаемый attention для {key}: {attention}")
        if mode not in ALLOWED_RUNTIME_MODES:
            raise ValueError(f"неподдерживаемый runtime mode для {key}: {mode}")

    def resolve(self, alias: str | None) -> ResolvedModel:
        requested = (alias or "tts-1-ru").strip()
        spec = self.aliases.get(requested.lower())
        if spec is None:
            allowed = ", ".join(self.public_aliases())
            raise ValueError(f"неизвестная model alias: {requested}; доступны: {allowed}")
        return ResolvedModel(requested_alias=requested, spec=spec)

    def public_aliases(self) -> list[str]:
        result = ["tts-1-ru"]
        for spec in self.specs.values():
            for alias in spec.aliases:
                if alias.lower() != "tts-1-ru" and alias not in result:
                    result.append(alias)
        return result

    def public_models(self) -> list[dict[str, Any]]:
        result = []
        for alias in self.public_aliases():
            resolved = self.resolve(alias)
            result.append(
                {
                    "id": alias,
                    "object": "model",
                    "owned_by": "local-qwen",
                    "canonical": resolved.canonical,
                    "resolved_hf_id": resolved.hf_id,
                    "is_default": alias == "tts-1-ru",
                }
            )
        return result
