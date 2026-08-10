from __future__ import annotations

from dataclasses import dataclass
import inspect
import threading
from typing import Any, Callable

from .models import ModelRegistry, ResolvedModel
from .resources import ResourceSnapshot, choose_mode
from .worker import MockWorker, QwenWorker


class ModelActivationError(RuntimeError):
    pass


@dataclass
class ModelActivation:
    resolved: ResolvedModel
    mode: str
    mode_reason: str
    resources: ResourceSnapshot
    device: str
    dtype: str
    attention: str
    action: str
    worker: QwenWorker | None

    def metadata(self) -> dict[str, Any]:
        return {
            "requested_model": self.resolved.requested_alias,
            "resolved_model": self.resolved.canonical,
            "resolved_hf_id": self.resolved.hf_id,
            "model_action": self.action,
            "mode": self.mode,
            "mode_reason": self.mode_reason,
            "device": self.device,
            "dtype": self.dtype,
            "attention": self.attention,
            "model_load_seconds": self.worker.load_seconds if self.worker else None,
        }


WorkerFactory = Callable[[Any, str, str, dict[str, Any]], QwenWorker]


def _default_worker_factory(config: Any, mode: str, model_id: str, runtime: dict[str, Any]) -> QwenWorker:
    backend = str(config.get("model.backend", "qwen")).lower()
    worker_type = MockWorker if backend == "mock" else QwenWorker
    return worker_type(config, mode, model_id=model_id, runtime=runtime)


class ModelManager:
    """Owns at most one persistent heavy Qwen worker at a time."""

    def __init__(
        self,
        config: Any,
        registry: ModelRegistry | None = None,
        worker_factory: WorkerFactory | None = None,
    ):
        self.config = config
        self.registry = registry or ModelRegistry(config)
        self.worker_factory = worker_factory or _default_worker_factory
        self.lock = threading.RLock()
        self.active_worker: QwenWorker | None = None
        self.active_resolved: ResolvedModel | None = None
        self.active_activation: ModelActivation | None = None

    @staticmethod
    def _runtime_values(resolved: ResolvedModel, mode: str) -> tuple[str, str, str]:
        runtime = resolved.spec.runtime
        device = "cuda:0" if mode in {"cuda", "cuda_on_demand"} else "cpu"
        dtype = str(runtime.get("dtype", "auto")).lower()
        if dtype == "auto":
            # Project-safe default for Windows/Turing. FP16 remains opt-in.
            dtype = "float32"
        attention = str(runtime.get("attention", "sdpa")).lower()
        return device, dtype, attention

    def _plan(self, resolved: ResolvedModel) -> ModelActivation:
        mode, reason, resources = choose_mode(self.config, resolved.spec.runtime)
        device, dtype, attention = self._runtime_values(resolved, mode)
        return ModelActivation(
            resolved=resolved,
            mode=mode,
            mode_reason=reason,
            resources=resources,
            device=device,
            dtype=dtype,
            attention=attention,
            action="planned",
            worker=None,
        )

    def prepare(self, alias: str | None) -> ModelActivation:
        with self.lock:
            resolved = self.registry.resolve(alias)
            if (
                self.active_resolved is not None
                and self.active_resolved.canonical == resolved.canonical
                and self.active_activation is not None
            ):
                action = "reused" if self.active_worker is not None else "on_demand"
                activation = ModelActivation(
                    **{
                        **self.active_activation.__dict__,
                        "resolved": resolved,
                        "action": action,
                        "worker": self.active_worker,
                    }
                )
                self.active_activation = activation
                return activation

            had_active = self.active_resolved is not None
            self._unload_locked()
            plan = self._plan(resolved)
            if plan.mode == "cuda_on_demand" and str(self.config.get("model.backend", "qwen")).lower() != "mock":
                plan.action = "on_demand"
                self.active_resolved = resolved
                self.active_activation = plan
                return plan

            worker = self.worker_factory(self.config, plan.mode, resolved.hf_id, resolved.spec.runtime)
            try:
                worker.load()
            except Exception as exc:
                try:
                    worker.unload()
                finally:
                    self.active_worker = None
                    self.active_resolved = None
                    self.active_activation = None
                raise ModelActivationError(
                    "не удалось активировать запрошенную модель; "
                    f"requested={resolved.requested_alias}; resolved={resolved.hf_id}; "
                    f"mode={plan.mode}; device={plan.device}; dtype={plan.dtype}; "
                    f"attention={plan.attention}; reason={type(exc).__name__}: {exc}"
                ) from exc

            plan.action = "switched" if had_active else "loaded"
            plan.worker = worker
            self.active_worker = worker
            self.active_resolved = resolved
            self.active_activation = plan
            return plan

    def synthesize(
        self,
        alias: str | None,
        text: str,
        profile: Any,
        language: str,
        generation_kwargs: dict[str, Any],
    ) -> tuple[Any, int, dict[str, Any], ModelActivation]:
        with self.lock:
            activation = self.prepare(alias)
            result = self._synthesize_prepared_locked(
                activation,
                text,
                profile,
                language,
                generation_kwargs,
            )
            waveform, sample_rate, metrics = result
            return waveform, sample_rate, metrics, activation

    def synthesize_prepared(
        self,
        activation: ModelActivation,
        text: str,
        profile: Any,
        language: str,
        generation_kwargs: dict[str, Any],
    ) -> tuple[Any, int, dict[str, Any]]:
        with self.lock:
            if (
                activation.worker is not self.active_worker
                or self.active_resolved is None
                or activation.resolved.spec != self.active_resolved.spec
            ):
                raise RuntimeError("prepared model activation is no longer active")
            return self._synthesize_prepared_locked(
                activation,
                text,
                profile,
                language,
                generation_kwargs,
            )

    @staticmethod
    def _synthesize_prepared_locked(
        activation: ModelActivation,
        text: str,
        profile: Any,
        language: str,
        generation_kwargs: dict[str, Any],
    ) -> tuple[Any, int, dict[str, Any]]:
        if activation.worker is None:
            raise RuntimeError("persistent synthesize вызван для cuda_on_demand activation")
        parameters = inspect.signature(activation.worker.synthesize).parameters
        if "generation_kwargs" in parameters or any(
            item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values()
        ):
            result = activation.worker.synthesize(
                text,
                profile,
                language,
                generation_kwargs=generation_kwargs,
            )
        else:
            # Compatibility with existing local test/instrumentation wrappers.
            result = activation.worker.synthesize(text, profile, language)
        return result

    def worker_for_compatibility(self) -> QwenWorker:
        activation = self.prepare("tts-1-ru")
        if activation.worker is None:
            raise RuntimeError("compatibility worker недоступен в cuda_on_demand")
        return activation.worker

    def preview(self, alias: str | None = "tts-1-ru") -> ModelActivation:
        return self._plan(self.registry.resolve(alias))

    def preview_default(self) -> ModelActivation:
        return self.preview("tts-1-ru")

    def _unload_locked(self) -> None:
        worker = self.active_worker
        self.active_worker = None
        self.active_resolved = None
        self.active_activation = None
        if worker is not None:
            worker.unload()

    def shutdown(self) -> None:
        with self.lock:
            self._unload_locked()
