from __future__ import annotations

import importlib
import json
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable


_STRESS_MARKER = re.compile(r"\+([аеёиоуыэюяАЕЁИОУЫЭЮЯ])([\u0301']?)")


class SileroPreprocessingError(RuntimeError):
    pass


def format_stress_markers(text: str, stress_format: str) -> str:
    if stress_format not in {"plus", "acute", "apostrophe"}:
        raise ValueError("stress_format must be plus, acute, or apostrophe")

    def replace(match: re.Match[str]) -> str:
        vowel = match.group(1)
        if vowel.casefold() == "ё":
            return vowel
        if stress_format == "plus":
            return "+" + vowel
        return vowel + ("\u0301" if stress_format == "acute" else "'")

    return _STRESS_MARKER.sub(replace, text)


class SileroPreprocessor:
    def __init__(self, provision_state: Path):
        self.provision_state = provision_state
        self._lock = threading.Lock()
        self._stress: Callable[[str], str] | None = None
        self._te: Any = None
        self._stress_loaded = False
        self._te_loaded = False
        self._last_stress_seconds = 0.0
        self._last_te_seconds = 0.0

    def _require_provisioned(self) -> None:
        try:
            state = json.loads(self.provision_state.read_text(encoding="utf-8-sig"))
            files = [Path(item) for item in state["model_files"]]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SileroPreprocessingError(
                "Silero preprocessing is not provisioned; run .\\scripts\\install.ps1"
            ) from exc
        missing = [str(path) for path in files if not path.exists()]
        if missing:
            raise SileroPreprocessingError(
                "Silero preprocessing assets are missing; run .\\scripts\\install.ps1"
            )

    @staticmethod
    def _configure_torch() -> Any:
        torch = importlib.import_module("torch")
        torch.set_num_threads(1)
        if getattr(torch.version, "cuda", None) is not None:
            raise SileroPreprocessingError("Silero requires the project CPU-only PyTorch build")
        return torch

    def _load_stress(self) -> Callable[[str], str]:
        with self._lock:
            if self._stress is None:
                self._require_provisioned()
                self._configure_torch()
                module = importlib.import_module("silero_stress")
                self._stress = module.load_accentor()
                if not callable(self._stress):
                    raise SileroPreprocessingError("Silero Stress returned an invalid accentor")
                self._stress_loaded = True
            return self._stress

    def _load_te(self) -> Any:
        with self._lock:
            if self._te is None:
                self._require_provisioned()
                self._configure_torch()
                module = importlib.import_module("silero")
                loaded = module.silero_te()
                self._te = loaded[0] if isinstance(loaded, tuple) else loaded
                if not callable(getattr(self._te, "enhance_text", None)):
                    raise SileroPreprocessingError("Silero Text Enhancement returned an invalid model")
                self._te_loaded = True
            return self._te

    @staticmethod
    def _validated(result: Any, component: str, source: str) -> str:
        if not isinstance(result, str) or (source.strip() and not result.strip()):
            raise SileroPreprocessingError(f"{component} returned invalid text")
        return result

    def process(self, text: str, text_enhancement: str, auto_stress: str, stress_format: str) -> tuple[str, dict[str, float]]:
        value = text
        te_seconds = 0.0
        stress_seconds = 0.0
        if text_enhancement == "silero" and value.strip():
            started = time.perf_counter()
            try:
                value = self._validated(self._load_te().enhance_text(value, "ru"), "Silero Text Enhancement", value)
            except SileroPreprocessingError:
                raise
            except Exception as exc:
                raise SileroPreprocessingError(f"Silero Text Enhancement failed: {exc}") from exc
            te_seconds = time.perf_counter() - started
        elif text_enhancement != "off":
            raise ValueError("text_enhancement must be off or silero")

        if auto_stress == "silero" and value.strip():
            started = time.perf_counter()
            try:
                stressed = self._validated(self._load_stress()(value), "Silero Stress", value)
                value = format_stress_markers(stressed, stress_format)
            except SileroPreprocessingError:
                raise
            except Exception as exc:
                raise SileroPreprocessingError(f"Silero Stress failed: {exc}") from exc
            stress_seconds = time.perf_counter() - started
        elif auto_stress != "off":
            raise ValueError("auto_stress must be off or silero")

        return value, {"text_enhancement_seconds": te_seconds, "stress_seconds": stress_seconds}

    def record_timings(self, stress_seconds: float, text_enhancement_seconds: float) -> None:
        self._last_stress_seconds = stress_seconds
        self._last_te_seconds = text_enhancement_seconds

    def diagnostics(self) -> dict[str, Any]:
        return {
            "stress_model_loaded": self._stress_loaded,
            "stress_model_ready": self._stress_loaded,
            "text_enhancement_model_loaded": self._te_loaded,
            "text_enhancement_model_ready": self._te_loaded,
            "stress_preprocessing_wall_seconds": round(self._last_stress_seconds, 6),
            "text_enhancement_wall_seconds": round(self._last_te_seconds, 6),
        }
