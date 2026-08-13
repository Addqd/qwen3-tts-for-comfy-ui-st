from __future__ import annotations

import os
from pathlib import Path

from qwen3_tts_st import worker as worker_module
from qwen3_tts_st.voices import VoiceProfile
from qwen3_tts_st.worker import QwenWorker


class FakeModel:
    def __init__(self):
        self.calls = []

    def create_voice_clone_prompt(self, **kwargs):
        self.calls.append(kwargs)
        return f"prompt-{len(self.calls)}"


def profile_for(path: Path) -> VoiceProfile:
    return VoiceProfile(
        voice_id="clone:CacheTest",
        character="CacheTest",
        profile_id="cache_test",
        display_name="CacheTest",
        style="neutral",
        reference_audio=path.name,
        ref_text="Текст А.",
        language="Russian",
        clone_mode="icl",
        directory=path.parent,
    )


def test_prompt_cache_tracks_audio_text_and_clone_mode(tmp_path):
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"RIFF-audio-A")
    profile = profile_for(reference)
    worker = QwenWorker(config=None, mode="cpu")
    worker.model = FakeModel()

    first = worker._prompt(profile)
    assert worker._prompt(profile) == first
    assert len(worker.model.calls) == 1

    profile.ref_text = "Текст Б."
    second = worker._prompt(profile)
    assert second != first
    assert len(worker.model.calls) == 2
    assert worker.model.calls[-1]["ref_text"] == "Текст Б."
    assert worker._prompt(profile) == second
    assert len(worker.model.calls) == 2

    profile.clone_mode = "x_vector"
    third = worker._prompt(profile)
    assert third != second
    assert len(worker.model.calls) == 3
    assert worker.model.calls[-1]["x_vector_only_mode"] is True
    assert worker._prompt(profile) == third
    assert len(worker.model.calls) == 3

    previous = reference.stat().st_mtime_ns
    reference.write_bytes(b"RIFF-audio-B-with-different-size")
    os.utime(reference, ns=(previous + 1_000_000, previous + 1_000_000))
    fourth = worker._prompt(profile)
    assert fourth != third
    assert len(worker.model.calls) == 4
    assert worker._prompt(profile) == fourth
    assert len(worker.model.calls) == 4


def test_cpu_interop_threads_are_configured_only_once(monkeypatch):
    calls = []

    class FakeTorch:
        @staticmethod
        def set_num_threads(value):
            calls.append(("threads", value))

        @staticmethod
        def set_num_interop_threads(value):
            calls.append(("interop", value))

    monkeypatch.setattr(worker_module, "_INTEROP_THREADS_CONFIGURED", False)
    worker_module._configure_cpu_threads(FakeTorch, 6)
    worker_module._configure_cpu_threads(FakeTorch, 4)
    assert calls == [("threads", 6), ("interop", 2), ("threads", 4)]
