from __future__ import annotations

import asyncio
from copy import deepcopy

import numpy as np
import pytest

from qwen3_tts_st.audio import stitch
from qwen3_tts_st.config import AppConfig, load_config
from qwen3_tts_st.resources import ResourceSnapshot, choose_mode
from qwen3_tts_st.service import TTSService


def config_with(tmp_path, mode="cpu"):
    data = deepcopy(load_config().data)
    data["model"]["backend"] = "mock"
    data["resources"]["mode"] = mode
    data["voices"]["library_dir"] = str(tmp_path / "voices")
    return AppConfig(data, tmp_path / "test.yaml")


def test_stitch_resamples_and_crossfades():
    first = np.full(2400, 0.2, dtype=np.float32)
    second = np.full(1600, -0.2, dtype=np.float32)
    output, rate = stitch([(first, 24000), (second, 16000)], pause_ms=10, crossfade_ms=5)
    assert rate == 24000
    assert output.ndim == 1
    assert np.isfinite(output).all()
    assert np.max(np.abs(output)) <= 1.0


@pytest.mark.asyncio
async def test_queue_full_and_wait_timeout(tmp_path):
    service = TTSService(config_with(tmp_path))
    service.waiting = 4
    with pytest.raises(RuntimeError, match="очередь"):
        await service._acquire()
    service.waiting = 0
    service.config.data["queue"]["wait_timeout_seconds"] = 0.01
    await service.semaphore.acquire()
    with pytest.raises(asyncio.TimeoutError):
        await service._acquire()
    service.semaphore.release()


def test_auto_resource_modes(monkeypatch, tmp_path):
    cfg = config_with(tmp_path, "auto")

    def snap(free, processes=0):
        return ResourceSnapshot(10, 8, 48000, 30000, "GPU", 8192, free, 8192 - free, 5, processes)

    monkeypatch.setattr("qwen3_tts_st.resources.snapshot", lambda _=0: snap(5000))
    assert choose_mode(cfg)[0] == "cpu"
    monkeypatch.setattr("qwen3_tts_st.resources.snapshot", lambda _=0: snap(7000, 2))
    assert choose_mode(cfg)[0] == "cuda_on_demand"
    monkeypatch.setattr("qwen3_tts_st.resources.snapshot", lambda _=0: snap(7000, 0))
    assert choose_mode(cfg)[0] == "cuda"


def test_config_rejects_non_localhost(tmp_path):
    data = deepcopy(load_config().data)
    data["server"]["host"] = "0.0.0.0"
    with pytest.raises(ValueError, match="127.0.0.1"):
        AppConfig(data, tmp_path / "unsafe.yaml")


def test_service_shutdown_unloads_worker(tmp_path, monkeypatch):
    service = TTSService(config_with(tmp_path))
    called = []
    monkeypatch.setattr(service.worker, "unload", lambda: called.append(True))
    service.shutdown()
    assert called == [True]
