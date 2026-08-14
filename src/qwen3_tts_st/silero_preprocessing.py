from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Sequence


_STRESS_MARKER = re.compile(r"\+([аеёиоуыэюяАЕЁИОУЫЭЮЯ])([\u0301']?)")


class SileroPreprocessingError(RuntimeError):
    pass


def format_stress_markers(text: str, stress_format: str) -> str:
    if stress_format not in {"plus", "acute", "apostrophe"}:
        raise ValueError("stress_format must be plus, acute, or apostrophe")

    def replace(match: re.Match[str]) -> str:
        vowel = match.group(1)
        if stress_format == "plus":
            return "+" + vowel
        if vowel.casefold() == "ё":
            return vowel
        return vowel + ("\u0301" if stress_format == "acute" else "'")

    return _STRESS_MARKER.sub(replace, text)


class SileroPreprocessor:
    def __init__(self, provision_state: Path, provenance_path: Path | None = None):
        self.provision_state = provision_state
        self.provenance_path = provenance_path or Path(__file__).resolve().parents[2] / "config" / "silero-runtime.json"
        self._lock = threading.Lock()
        self._stress: Callable[[str], str] | None = None
        self._te: Any = None
        self._stress_loaded = False
        self._te_loaded = False
        self._provision_verified: set[str] = set()
        self._last_stress_seconds = 0.0
        self._last_te_seconds = 0.0

    def _require_provisioned(self, component: str = "all") -> None:
        if component == "all":
            self._require_provisioned("stress")
            self._require_provisioned("text_enhancement")
            return
        if component in self._provision_verified:
            return
        try:
            state = json.loads(self.provision_state.read_text(encoding="utf-8-sig"))
            provenance = json.loads(self.provenance_path.read_text(encoding="utf-8-sig"))
            if state.get("schema") != 2 or provenance.get("schema") != 1:
                raise ValueError("unsupported Silero provenance schema")
            files = [Path(item) for item in state["model_files"]]
            asset_sha256 = state["asset_sha256"]
            catalogue = Path(state["catalogue"])
            te_model = Path(state["te_model"])
            expected_catalogue = provenance["catalogue"]
            expected_te = provenance["text_enhancement_model"]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SileroPreprocessingError(
                "Silero preprocessing is not provisioned; run .\\scripts\\install.ps1"
            ) from exc
        if component == "stress":
            required = [path for path in files if path not in {catalogue, te_model}]
            label = "Silero Stress"
        elif component == "text_enhancement":
            required = [catalogue, te_model]
            label = "Silero Text Enhancement"
        else:
            raise ValueError(f"Unknown Silero component: {component}")
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise SileroPreprocessingError(
                f"{label} assets are missing; run .\\scripts\\install.ps1"
            )
        try:
            if component == "text_enhancement" and (
                    state["catalogue_revision"] != expected_catalogue["revision"]
                    or state["catalogue_sha256"] != expected_catalogue["sha256"]
                    or hashlib.sha256(catalogue.read_bytes()).hexdigest() != expected_catalogue["sha256"]
                    or state["te_model_sha256"] != expected_te["sha256"]
                    or hashlib.sha256(te_model.read_bytes()).hexdigest() != expected_te["sha256"]
            ):
                raise ValueError("pinned Silero Text Enhancement provenance mismatch")
            for path in required:
                if hashlib.sha256(path.read_bytes()).hexdigest() != asset_sha256[str(path)]:
                    raise ValueError(f"Silero asset digest mismatch: {path}")
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise SileroPreprocessingError(
                f"{label} integrity validation failed; run .\\scripts\\install.ps1"
            ) from exc
        self._provision_verified.add(component)

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
                self._require_provisioned("stress")
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
                self._require_provisioned("text_enhancement")
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

    def process(
        self,
        text: str,
        text_enhancement: str,
        auto_stress: str,
        stress_format: str,
        protected_terms: Sequence[str] | None = None,
    ) -> tuple[str, dict[str, float]]:
        value = text
        protected = [term for term in (protected_terms or ()) if term]
        te_seconds = 0.0
        stress_seconds = 0.0
        if text_enhancement == "silero" and value.strip():
            started = time.perf_counter()
            try:
                enhanced = self._validated(self._load_te().enhance_text(value, "ru"), "Silero Text Enhancement", value)
                if all(
                    len(re.findall(re.escape(term), enhanced, flags=re.I))
                    == len(re.findall(re.escape(term), value, flags=re.I))
                    for term in protected
                ):
                    value = enhanced
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
                protected_value = value
                protected_phrases: list[tuple[str, str]] = []
                multiword_terms = [
                    term for term in sorted(protected, key=len, reverse=True)
                    if not re.fullmatch(r"[A-Za-zА-Яа-яЁё]+", term)
                ]
                for term in multiword_terms:
                    pattern = re.compile(re.escape(term), flags=re.I)

                    def protect(match: re.Match[str]) -> str:
                        placeholder = f"qwenprotectedtoken{len(protected_phrases)}"
                        protected_phrases.append((placeholder, match.group(0)))
                        return placeholder

                    protected_value = pattern.sub(protect, protected_value)
                words_to_ignore = sorted({
                    term.casefold()
                    for term in protected
                    if re.fullmatch(r"[A-Za-zА-Яа-яЁё]+", term)
                } | {placeholder for placeholder, _original in protected_phrases})
                stressed = self._validated(
                    self._load_stress()(protected_value, words_to_ignore=words_to_ignore or None),
                    "Silero Stress",
                    protected_value,
                )
                for placeholder, original in protected_phrases:
                    if len(re.findall(re.escape(placeholder), stressed, flags=re.I)) != 1:
                        raise SileroPreprocessingError("Silero Stress changed a protected pronunciation phrase")
                    stressed = re.sub(re.escape(placeholder), lambda _match, value=original: value, stressed, count=1, flags=re.I)
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
