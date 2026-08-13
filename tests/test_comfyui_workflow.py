from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODES_PATH = ROOT / "integrations" / "comfyui" / "qwen_tts_api_nodes" / "nodes.py"
WORKFLOW_PATH = ROOT / "integrations" / "comfyui" / "example_workflows" / "voice_profile_from_wav_ru.json"


def load_nodes():
    spec = importlib.util.spec_from_file_location("qwen_nodes", NODES_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_canonical_voice_lab_matches_current_node_schema():
    assert [path.name for path in WORKFLOW_PATH.parent.glob("*.json")] == [WORKFLOW_PATH.name]
    module = load_nodes()
    workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
    by_type = {node["type"]: node for node in workflow["nodes"]}
    assert workflow["extra"]["qwen_tts_workflow_schema"] == 4
    assert {"QwenTTSServer", "QwenTTSRuntimeSettings", "QwenTTSCloneVoice", "QwenTTSSynthesize", "PreviewAudio"} <= set(by_type)
    assert module.PRODUCTION_MODEL.endswith("(tts-1-ru)")
    assert by_type["QwenTTSServer"]["widgets_values"] == ["http://127.0.0.1:8020", 900, "wav"]
    assert len(by_type["QwenTTSRuntimeSettings"]["widgets_values"]) == 10
    assert len(by_type["QwenTTSCloneVoice"]["widgets_values"]) == 5
    assert by_type["QwenTTSCloneVoice"]["widgets_values"][-1] is False
    assert len(by_type["QwenTTSSynthesize"]["widgets_values"]) == 12
    links = {(link[1], link[3], link[5]) for link in workflow["links"]}
    assert (4, 5, "STRING") in links
    assert (5, 6, "AUDIO") in links


def test_active_node_inputs_are_real_qwentts_controls_only():
    module = load_nodes()
    runtime = module.QwenTTSRuntimeSettingsNode.INPUT_TYPES()
    names = set(runtime["required"]) | set(runtime.get("optional", {}))
    assert {"language", "russian_normalization", "seed", "max_new_tokens", "temperature", "top_k", "top_p", "repetition_penalty"} <= names
    assert names.isdisjoint({"active_model", "generation_preset", "multilingual_mode", "chunking_mode", "style", "clone_mode"})
    clone = module.QwenTTSCloneVoiceNode.INPUT_TYPES()["required"]
    assert list(clone)[-1] == "overwrite"


def test_read_only_nodes_report_unavailable_backend(monkeypatch):
    module = load_nodes()

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("backend down")

    monkeypatch.setattr(module, "_json_request", unavailable)
    server = {"endpoint": "http://127.0.0.1:8020", "timeout": 1}
    assert module.QwenTTSVoiceSelectorNode().select(server, "clone:test")["result"][1].startswith("unavailable:")
    assert module.QwenTTSModelsNode().list_models(server)["result"][0] == ""
    health = module.QwenTTSHealthNode().check(server)["result"]
    assert health[0] is False
    assert health[1] == "unknown"


def test_comfyui_smoke_requires_explicit_input_write_opt_in():
    script = (ROOT / "scripts" / "test-comfyui-integration.ps1").read_text(encoding="utf-8-sig")
    assert "[switch]$AllowComfyUIInputWrite" in script
    assert "if (-not $AllowComfyUIInputWrite)" in script
    assert "find_spec('qwen_tts')" in script
    assert "('qwen_tts','torch','transformers')" not in script


def test_managed_workflow_marker_rejects_empty_target_before_path_resolution():
    script = (ROOT / "scripts" / "comfyui-common.ps1").read_text(encoding="utf-8-sig")
    empty_guard = script.index("[string]::IsNullOrWhiteSpace([string]$Info.target)")
    path_resolution = script.index("[IO.Path]::GetFullPath([string]$Info.target)")
    assert empty_guard < path_resolution


class _Waveform:
    shape = (1, 1, 24000)


def _stub_synthesis(module, monkeypatch, tmp_path):
    monkeypatch.setattr(module, "_temp_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "_json_request", lambda *_args, **_kwargs: (b"source-audio", {}))
    monkeypatch.setattr(module, "_load_comfy_audio", lambda _path: {"waveform": _Waveform(), "sample_rate": 24000})
    monkeypatch.setattr(module, "_audio_to_wav_bytes", lambda _audio: b"converted-wav")
    return module.QwenTTSSynthesizeNode(), {"endpoint": "http://127.0.0.1:8020", "timeout": 1}


def test_synthesize_uses_collision_resistant_paths(monkeypatch, tmp_path):
    module = load_nodes()
    node, server = _stub_synthesis(module, monkeypatch, tmp_path)
    first = node.synthesize(server, "text", "clone:test", 1.0, "wav", "Full Russian")["result"][1]
    second = node.synthesize(server, "text", "clone:test", 1.0, "wav", "Full Russian")["result"][1]
    assert first != second
    assert Path(first).exists() and Path(second).exists()


def test_non_wav_synthesis_removes_source_and_reports_returned_wav(monkeypatch, tmp_path):
    module = load_nodes()
    node, server = _stub_synthesis(module, monkeypatch, tmp_path)
    result = node.synthesize(server, "text", "clone:test", 1.0, "mp3", "Full Russian")["result"]
    returned = Path(result[1])
    assert returned.suffix == ".wav" and returned.read_bytes() == b"converted-wav"
    assert not list(tmp_path.glob("*.mp3"))
    assert json.loads(result[2])["format"] == "wav"


def test_synthesis_failure_removes_all_provisional_audio(monkeypatch, tmp_path):
    module = load_nodes()
    node, server = _stub_synthesis(module, monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_json_request", lambda *_args, **_kwargs: (b"source-audio", {"X-Audio-Duration": "invalid"}))
    with pytest.raises(ValueError):
        node.synthesize(server, "text", "clone:test", 1.0, "mp3", "Full Russian")
    assert not list(tmp_path.iterdir())


def test_clone_rejects_incomplete_api_response(monkeypatch):
    module = load_nodes()
    monkeypatch.setattr(module, "_audio_to_wav_bytes", lambda _audio: b"wav")
    monkeypatch.setattr(module, "_json_request", lambda *_args, **_kwargs: ({"voice_id": "clone:test"}, {}))
    with pytest.raises(RuntimeError, match=r"missing required field.*validation, metadata"):
        module.QwenTTSCloneVoiceNode().clone(
            {"endpoint": "http://127.0.0.1:8020", "timeout": 1}, {}, "text", "test", "Test", "Russian", False
        )
