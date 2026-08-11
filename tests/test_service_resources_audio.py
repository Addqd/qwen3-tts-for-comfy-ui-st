from __future__ import annotations

import asyncio
from copy import deepcopy

import numpy as np
import pytest

from qwen3_tts_st.audio import AudioPart, pad_edges, stitch
from qwen3_tts_st.config import AppConfig, load_config
from qwen3_tts_st.resources import ResourceSnapshot, choose_mode
from qwen3_tts_st.service import TTSService


def config_with(tmp_path, mode="cpu"):
    data = deepcopy(load_config().data)
    data["model"]["backend"] = "mock"
    data["resources"]["mode"] = mode
    data["voices"]["library_dir"] = str(tmp_path / "voices")
    data["runtime"]["settings_file"] = str(tmp_path / "runtime-settings.json")
    return AppConfig(data, tmp_path / "test.yaml")


def test_stitch_resamples_and_crossfades():
    first = np.full(2400, 0.2, dtype=np.float32)
    second = np.full(1600, -0.2, dtype=np.float32)
    output, rate = stitch([(first, 24000), (second, 16000)], crossfade_ms=5)
    assert rate == 24000
    assert output.ndim == 1
    assert np.isfinite(output).all()
    assert np.max(np.abs(output)) <= 1.0
    assert len(output) == 2400 + 2400 - 120


def test_one_sample_overlap_blends_both_parts():
    output, rate = stitch(
        [(np.array([0.2], dtype=np.float32), 1000), (np.array([0.8], dtype=np.float32), 1000)],
        crossfade_ms=1,
    )
    assert rate == 1000
    assert output == pytest.approx([(0.2 + 0.8) / np.sqrt(2)])


def test_internal_join_has_no_artificial_silence_and_padding_is_outer_only():
    first = np.full(100, 0.25, dtype=np.float32)
    second = np.full(100, 0.5, dtype=np.float32)
    joined, rate = stitch([(first, 1000), (second, 1000)], crossfade_ms=0)
    assert len(joined) == 200
    assert np.all(joined != 0)
    padded = pad_edges(joined, rate, leading_silence_ms=100, trailing_silence_ms=150)
    assert np.all(padded[:100] == 0)
    assert np.array_equal(padded[100:300], joined)
    assert np.all(padded[300:] == 0)


def test_performance_boundaries_trim_only_edges_and_bound_gain_correction():
    rate = 1000
    pulse = np.tile(np.array([0.2, -0.2], dtype=np.float32), 30)
    first = np.concatenate((np.zeros(20), pulse, np.zeros(30), pulse, np.zeros(20))).astype(np.float32)
    second = np.concatenate(
        (np.zeros(20), np.tile(np.array([0.01, -0.01], dtype=np.float32), 50), np.zeros(20))
    ).astype(np.float32)
    config = {
        "edge_window_ms": 40,
        "edge_silence_threshold": 0.0025,
        "edge_min_silence_ms": 12,
        "edge_safety_ms": 2,
        "dc_offset_threshold": 0.01,
        "level_window_ms": 20,
        "level_rms_floor": 0.003,
        "max_gain_correction_db": 2.0,
        "crossfade_ms": {"speech_to_sound": 10},
    }

    output, output_rate = stitch(
        [
            AudioPart(first, rate, "speech", "profile-a"),
            AudioPart(second, rate, "sound", "profile-b"),
        ],
        boundary_config=config,
    )

    assert output_rate == rate
    assert np.isfinite(output).all()
    assert np.max(np.abs(output)) <= 1.0
    assert int(np.argmax(np.abs(output) > 1e-7)) <= 2
    assert int(np.argmax(np.abs(output[::-1]) > 1e-7)) <= 2
    assert np.count_nonzero(np.abs(output) < 1e-7) >= 25  # internal pause remains
    assert np.max(np.abs(output[-80:])) <= 0.01 * 10 ** (2.0 / 20.0) * 1.001


def test_fully_silent_edge_windows_are_trimmed_without_touching_internal_pause():
    rate = 1000
    pulse = np.tile(np.array([0.2, -0.2], dtype=np.float32), 10)
    waveform = np.concatenate((np.zeros(40), pulse, np.zeros(30), pulse, np.zeros(40))).astype(np.float32)
    output, _ = stitch(
        [AudioPart(waveform, rate)],
        boundary_config={
            "edge_window_ms": 40,
            "edge_silence_threshold": 0.0025,
            "edge_min_silence_ms": 12,
            "edge_safety_ms": 4,
        },
    )
    assert len(output) == 78
    assert np.all(output[:4] == 0)
    assert np.all(output[24:54] == 0)
    assert np.all(output[-4:] == 0)


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


def test_single_model_manager_clamps_full_request_concurrency(tmp_path):
    config = config_with(tmp_path)
    config.data["queue"]["max_concurrent"] = 3
    service = TTSService(config)
    health = service.health()
    assert service.configured_max_concurrent == 3
    assert service.effective_max_concurrent == 1
    assert health["queue_max_concurrent_configured"] == 3
    assert health["queue_max_concurrent_effective"] == 1


@pytest.mark.asyncio
async def test_second_request_cannot_enter_single_manager_lifecycle(tmp_path):
    config = config_with(tmp_path)
    config.data["queue"]["max_concurrent"] = 2
    config.data["queue"]["wait_timeout_seconds"] = 0.01
    service = TTSService(config)
    await service._acquire()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await service._acquire()
    finally:
        service.semaphore.release()
