from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from qwen3_tts_st import worker_process


def _job(tmp_path):
    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps(
            {
                "config": str(tmp_path / "config.yaml"),
                "text": "Проверка.",
                "voice": "clone:Test",
                "language": "English",
                "model": "tts-1-ru-fast",
                "generation_preset": "default",
                "output": str(tmp_path / "output.wav"),
                "result": str(tmp_path / "result.json"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return job_path


def _patch_dependencies(monkeypatch, workers, fail=False):
    resolved = SimpleNamespace(
        requested_alias="tts-1-ru-fast",
        canonical="qwen3-tts-0.6b",
        hf_id="Qwen/Test",
        spec=SimpleNamespace(runtime={}),
    )
    monkeypatch.setattr(worker_process, "load_config", lambda _path: SimpleNamespace(path=lambda *_args: None))
    monkeypatch.setattr(worker_process, "ModelRegistry", lambda _config: SimpleNamespace(resolve=lambda _model: resolved))
    monkeypatch.setattr(worker_process, "VoiceLibrary", lambda _path: SimpleNamespace(resolve=lambda _voice: object()))
    monkeypatch.setattr(worker_process, "generation_kwargs", lambda *_args: {"max_new_tokens": 64})

    class FakeWorker:
        def __init__(self, *_args, **_kwargs):
            self.unloaded = False
            self.language = None
            workers.append(self)

        def load(self):
            raise AssertionError("worker_process must rely on QwenWorker.synthesize lazy load")

        def synthesize(self, _text, _profile, language, generation_kwargs):
            self.language = language
            assert generation_kwargs == {"max_new_tokens": 64}
            if fail:
                raise RuntimeError("deliberate synthesis failure")
            return np.zeros(240, dtype=np.float32), 24000, {}

        def unload(self):
            self.unloaded = True

    monkeypatch.setattr(worker_process, "QwenWorker", FakeWorker)


def test_worker_process_forces_russian_and_unloads_after_success(tmp_path, monkeypatch):
    workers = []
    _patch_dependencies(monkeypatch, workers)
    monkeypatch.setattr(sys, "argv", ["worker_process", "--job", str(_job(tmp_path))])
    assert worker_process.main() == 0
    assert workers[0].language == "Russian"
    assert workers[0].unloaded is True


def test_worker_process_unloads_after_synthesis_failure(tmp_path, monkeypatch):
    workers = []
    _patch_dependencies(monkeypatch, workers, fail=True)
    monkeypatch.setattr(sys, "argv", ["worker_process", "--job", str(_job(tmp_path))])
    with pytest.raises(RuntimeError, match="deliberate synthesis failure"):
        worker_process.main()
    assert workers[0].unloaded is True


def test_worker_process_unloads_after_output_write_failure(tmp_path, monkeypatch):
    workers = []
    _patch_dependencies(monkeypatch, workers)
    monkeypatch.setattr(worker_process.sf, "write", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(sys, "argv", ["worker_process", "--job", str(_job(tmp_path))])
    with pytest.raises(OSError, match="disk full"):
        worker_process.main()
    assert workers[0].language == "Russian"
    assert workers[0].unloaded is True
