from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import threading

import pytest

from qwen3_tts_st.config import load_config
from qwen3_tts_st.normalization import apply_pronunciation
from qwen3_tts_st.preprocess import preprocess
from qwen3_tts_st.runtime_settings import RuntimeSettingsStore
from qwen3_tts_st.service import TTSService
from qwen3_tts_st.voices import VoiceLibrary


ROOT = Path(__file__).resolve().parents[1]


class Response:
    def raise_for_status(self) -> None:
        return None


class RecordingClient:
    def __init__(self, fail_ref_text: str | None = None):
        self.fail_ref_text = fail_ref_text
        self.ref_texts: list[str] = []

    async def post(self, _path: str, json: dict):
        self.ref_texts.append(json["ref_text"])
        if json["ref_text"] == self.fail_ref_text:
            raise RuntimeError("registration failed")
        return Response()


def write_profile(root: Path, ref_text: str = "old") -> Path:
    target = root / "profiles" / "voice"
    target.mkdir(parents=True)
    (target / "reference.wav").write_bytes(b"old-wav")
    (target / "reference.spk").write_bytes(b"old-spk")
    (target / "reference.rvq").write_bytes(b"old-rvq")
    (target / "metadata.json").write_text(json.dumps({
        "profile_id": "voice", "display_name": "voice", "character": "Voice",
        "reference_audio": "reference.wav", "ref_text": ref_text, "language": "Russian",
    }), encoding="utf-8")
    return target


def fake_encode(reference: Path) -> tuple[Path, Path]:
    spk, rvq = reference.with_suffix(".spk"), reference.with_suffix(".rvq")
    spk.write_bytes(b"new-spk")
    rvq.write_bytes(b"new-rvq")
    return spk, rvq


@pytest.mark.asyncio
async def test_voice_overwrite_is_prepared_off_loop_and_rolls_back_registration(tmp_path, monkeypatch):
    target = write_profile(tmp_path)
    source = tmp_path / "new.wav"
    source.write_bytes(b"new-wav")
    library = VoiceLibrary(tmp_path, load_config(ROOT / "config" / "config.example.yaml"))
    event_thread = threading.get_ident()
    encoding_threads: list[int] = []

    def encode(reference: Path):
        encoding_threads.append(threading.get_ident())
        return fake_encode(reference)

    monkeypatch.setattr(library, "_encode", encode)
    client = RecordingClient(fail_ref_text="new")
    with pytest.raises(RuntimeError, match="registration failed"):
        await library.create(source, "voice", "Voice", "new", "Russian", True, client)

    assert encoding_threads and encoding_threads[0] != event_thread
    assert (target / "reference.wav").read_bytes() == b"old-wav"
    assert library.resolve("clone:voice").ref_text == "old"
    assert client.ref_texts == ["new", "old"]
    assert not list((tmp_path / "profiles").glob(".*-staging-*"))
    assert not list((tmp_path / "profiles").glob(".*-failed-*"))


@pytest.mark.asyncio
async def test_voice_preparation_failure_does_not_mutate_active_profile(tmp_path, monkeypatch):
    target = write_profile(tmp_path)
    source = tmp_path / "new.wav"
    source.write_bytes(b"new-wav")
    library = VoiceLibrary(tmp_path, load_config(ROOT / "config" / "config.example.yaml"))

    def fail_encode(_reference: Path):
        raise RuntimeError("codec failed")

    monkeypatch.setattr(library, "_encode", fail_encode)
    with pytest.raises(RuntimeError, match="codec failed"):
        await library.create(source, "voice", "Voice", "new", "Russian", True, RecordingClient())
    assert (target / "reference.wav").read_bytes() == b"old-wav"
    assert library.resolve("clone:voice").ref_text == "old"


@pytest.mark.asyncio
async def test_health_uses_pinned_revision_and_does_not_claim_device_when_engine_is_down(tmp_path):
    config = load_config(ROOT / "config" / "config.example.yaml")
    config.data["voices"]["library_dir"] = str(tmp_path / "voices")
    config.data["runtime"]["settings_file"] = str(tmp_path / "settings.json")
    service = TTSService(config)
    await service.client.aclose()

    class DownClient:
        async def get(self, _path):
            raise __import__("httpx").ConnectError("down")

    service.client = DownClient()
    health = await service.health()
    manifest = json.loads((ROOT / "config" / "qwentts-runtime.json").read_text(encoding="utf-8"))
    assert health["engine_revision"] == manifest["upstream"]["revision"]
    assert health["qwentts_ready"] is False
    assert health["device"] is None


def test_conversion_and_literal_replacement_guards(monkeypatch):
    assert preprocess("API", {"russian_abbreviations": {"API": r"а\1"}}) == r"а\1"
    assert apply_pronunciation("Qwen", {"Qwen": r"ку\1"}) == (r"ку\1", 1)
    with pytest.raises(ValueError, match="speed"):
        TTSService._convert(b"wav", "wav", 0)
    with pytest.raises(ValueError, match="Unsupported"):
        TTSService._convert(b"wav", "ogg", 1)

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr("qwen3_tts_st.service.subprocess.run", missing)
    with pytest.raises(RuntimeError, match="FFmpeg.*not found"):
        TTSService._convert(b"wav", "mp3", 1)


def test_invalid_persisted_runtime_settings_fall_back_to_defaults(tmp_path):
    config = load_config(ROOT / "config" / "config.example.yaml")
    config.data["runtime"]["settings_file"] = str(tmp_path / "settings.json")
    Path(config.data["runtime"]["settings_file"]).write_text('{"top_p": 7}', encoding="utf-8")
    assert RuntimeSettingsStore(config).current()["top_p"] == 0.9


def test_runner_state_failure_stops_child(tmp_path):
    path = ROOT / "scripts" / "qwentts-runner.py"
    spec = importlib.util.spec_from_file_location("qwentts_runner_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)

    class Process:
        terminated = False
        waited = False

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            self.waited = True
            return 0

    process = Process()
    state_path = tmp_path / "qwentts.json"
    temporary = tmp_path / "missing" / "qwentts.json.tmp"
    with pytest.raises(OSError):
        module.persist_state_or_stop(process, state_path, temporary, {"pid": 1})
    assert process.terminated and process.waited
    assert not state_path.exists()
    assert not temporary.exists()
