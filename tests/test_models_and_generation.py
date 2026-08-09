from __future__ import annotations

from copy import deepcopy

import pytest

from qwen3_tts_st.config import AppConfig, load_config
from qwen3_tts_st.generation import generation_kwargs
from qwen3_tts_st.model_manager import ModelActivationError, ModelManager
from qwen3_tts_st.models import ModelRegistry
from qwen3_tts_st.resources import ResourceSnapshot


def _config(tmp_path, mode: str = "cpu") -> AppConfig:
    data = deepcopy(load_config().data)
    data["resources"]["mode"] = mode
    return AppConfig(data, tmp_path / "models.yaml")


class RecordingWorker:
    def __init__(self, model_id: str, fail: bool = False):
        self.model_id = model_id
        self.fail = fail
        self.loaded = False
        self.load_seconds = None
        self.unloaded = False

    def load(self):
        if self.fail:
            raise RuntimeError("deliberate load failure")
        self.loaded = True
        self.load_seconds = 0.01

    def unload(self):
        self.unloaded = True
        self.loaded = False


def test_registry_resolves_compatibility_fast_and_quality(tmp_path):
    registry = ModelRegistry(_config(tmp_path))
    assert registry.resolve("tts-1-ru").canonical == "qwen3-tts-0.6b"
    assert registry.resolve("tts-1-ru-fast").hf_id.endswith("0.6B-Base")
    assert registry.resolve("tts-1-ru-quality").hf_id.endswith("1.7B-Base")
    with pytest.raises(ValueError, match="unknown-model"):
        registry.resolve("unknown-model")


def test_manager_unloads_before_switch_and_reuses_same_model(tmp_path, monkeypatch):
    config = _config(tmp_path)
    workers: list[RecordingWorker] = []

    def factory(_config, _mode, model_id, _runtime):
        worker = RecordingWorker(model_id)
        workers.append(worker)
        return worker

    monkeypatch.setattr(
        "qwen3_tts_st.resources.snapshot",
        lambda *_: ResourceSnapshot(1, 8, 32000, 20000),
    )
    manager = ModelManager(config, worker_factory=factory)
    first = manager.prepare("tts-1-ru-fast")
    reused = manager.prepare("tts-1-ru-fast")
    second = manager.prepare("tts-1-ru-quality")

    assert first.action == "loaded"
    assert reused.action == "reused"
    assert second.action == "switched"
    assert len(workers) == 2
    assert workers[0].unloaded is True
    assert manager.active_worker is workers[1]


def test_failed_quality_activation_is_honest_and_recoverable(tmp_path):
    config = _config(tmp_path)

    def factory(_config, _mode, model_id, _runtime):
        return RecordingWorker(model_id, fail="1.7B" in model_id)

    manager = ModelManager(config, worker_factory=factory)
    manager.prepare("tts-1-ru-fast")
    with pytest.raises(ModelActivationError) as error:
        manager.prepare("tts-1-ru-quality")
    message = str(error.value)
    assert "tts-1-ru-quality" in message
    assert "1.7B-Base" in message
    assert "deliberate load failure" in message
    assert manager.active_worker is None
    assert manager.prepare("tts-1-ru-fast").resolved.canonical == "qwen3-tts-0.6b"


def test_stable_russian_preset_uses_supported_qwen_kwargs(tmp_path):
    config = _config(tmp_path)
    spec = ModelRegistry(config).resolve("tts-1-ru-fast").spec
    values = generation_kwargs(config, "stable_russian", spec)
    assert values["temperature"] == 0.75
    assert values["top_k"] == 40
    assert values["top_p"] == 0.90
    assert values["repetition_penalty"] == 1.05
    assert values["subtalker_temperature"] == 0.75
    assert values["max_new_tokens"] == 2048
