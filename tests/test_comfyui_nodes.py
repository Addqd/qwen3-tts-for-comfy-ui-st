from __future__ import annotations

import importlib.util
import ast
import json
from pathlib import Path


NODE_FILE = Path(__file__).parents[1] / "integrations/comfyui/qwen_tts_api_nodes/nodes.py"
WORKFLOW_DIR = NODE_FILE.parents[1] / "example_workflows"


def load_nodes():
    spec = importlib.util.spec_from_file_location("qwen_tts_api_nodes_test", NODE_FILE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def result_of(value):
    return value["result"] if isinstance(value, dict) else value


def test_node_package_has_expected_mappings():
    nodes = load_nodes()
    assert set(nodes.NODE_CLASS_MAPPINGS) == {
        "QwenTTSServer", "QwenTTSSynthesize", "QwenTTSCloneVoice",
        "QwenTTSVoiceSelector", "QwenTTSEmotionScript", "QwenTTSModels", "QwenTTSHealth",
    }
    output_nodes = {name for name, node in nodes.NODE_CLASS_MAPPINGS.items() if getattr(node, "OUTPUT_NODE", False)}
    assert {"QwenTTSSynthesize", "QwenTTSVoiceSelector", "QwenTTSEmotionScript", "QwenTTSModels", "QwenTTSHealth"} <= output_nodes


def test_nodes_have_no_backend_or_gpu_imports():
    tree = ast.parse(NODE_FILE.read_text(encoding="utf-8"))
    imported = set()
    for statement in ast.walk(tree):
        if isinstance(statement, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom) and statement.module:
            imported.add(statement.module.split(".", 1)[0])
    assert imported.isdisjoint({"torch", "transformers", "qwen_tts", "cuda"})


def test_example_workflows_are_valid_json_with_available_node_types():
    nodes = load_nodes()
    required = {
        "backend_health_and_voices.json",
        "emotion_script_preview.json",
        "text_to_speech_ru.json",
        "voice_clone_and_synthesize_ru.json",
    }
    paths = {path.name: path for path in WORKFLOW_DIR.glob("*.json")}
    assert required <= paths.keys()
    builtins = {"LoadAudio", "PreviewAudio"}
    for path in paths.values():
        workflow = json.loads(path.read_text(encoding="utf-8"))
        assert workflow["nodes"]
        types = {node["type"] for node in workflow["nodes"]}
        assert types <= set(nodes.NODE_CLASS_MAPPINGS) | builtins


def test_endpoint_rejects_non_local_address():
    nodes = load_nodes()
    assert nodes._endpoint("http://127.0.0.1:8020/") == "http://127.0.0.1:8020"
    try:
        nodes._endpoint("http://0.0.0.0:8020")
    except ValueError as exc:
        assert "127.0.0.1" in str(exc)
    else:
        raise AssertionError("non-local endpoint was accepted")
    for hostile in ("http://127.0.0.1:8020@evil.example", "http://127.0.0.1:8020/proxy"):
        try:
            nodes._endpoint(hostile)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe endpoint was accepted: {hostile}")


def test_emotion_script_parsing():
    nodes = load_nodes()
    normalized, segments_json, clean, styles = result_of(nodes.QwenTTSEmotionScriptNode().parse(
        "Тихо. [voice:happy] Отлично!", '{"neutral":"clone:N","happy":"clone:H"}'
    ))
    segments = json.loads(segments_json)
    assert segments[0]["voice"] == "clone:N"
    assert segments[1]["voice"] == "clone:H"
    assert "[voice:happy]" in normalized
    assert "[voice:happy]" not in clean
    assert styles == "neutral, happy"


def test_emotion_script_unknown_tag_is_removed_and_falls_back_to_neutral():
    nodes = load_nodes()
    normalized, segments_json, clean, styles = result_of(nodes.QwenTTSEmotionScriptNode().parse(
        "Начало. [voice:excited] Продолжение.", '{"neutral":"clone:N"}'
    ))
    segments = json.loads(segments_json)
    assert segments[-1]["style"] == "neutral"
    assert segments[-1]["voice"] == "clone:N"
    assert "voice:excited" not in normalized
    assert "voice:excited" not in clean
    assert styles == "neutral"


def test_nodes_report_unavailable_backend(monkeypatch):
    nodes = load_nodes()

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(nodes, "_json_request", unavailable)
    server, status = nodes.QwenTTSServerNode().connect("http://127.0.0.1:8020", 1, "tts-1-ru", "wav")
    assert server["endpoint"] == "http://127.0.0.1:8020"
    assert status.startswith("unavailable:")
    voice, available = result_of(nodes.QwenTTSVoiceSelectorNode().select(server, "clone:Fallback"))
    assert voice == "clone:Fallback"
    assert "backend unavailable" in available
    health = result_of(nodes.QwenTTSHealthNode().check(server))
    assert health[0] is False
    assert health[1] == "unknown"
    assert result_of(nodes.QwenTTSModelsNode().list_models(server))[0] == ""


def test_models_and_voices_are_returned_as_unicode(monkeypatch):
    nodes = load_nodes()
    server = {"endpoint": "http://127.0.0.1:8020", "timeout": 1, "model": "tts-1-ru"}

    def response(_server, path, payload=None):
        assert payload is None
        if path == "/v1/models":
            return {"data": [{"id": "tts-1-ru"}, {"id": "Qwen/Русский"}]}, {}
        return {"data": [{"voice_id": "clone:Тест"}]}, {}

    monkeypatch.setattr(nodes, "_json_request", response)
    model_ids, models_json = result_of(nodes.QwenTTSModelsNode().list_models(server))
    assert model_ids == "tts-1-ru, Qwen/Русский"
    assert "Русский" in models_json
    voice, available = result_of(nodes.QwenTTSVoiceSelectorNode().select(server, "clone:Тест"))
    assert voice == "clone:Тест"
    assert available == "clone:Тест"
