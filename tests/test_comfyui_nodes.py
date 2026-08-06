from __future__ import annotations

import importlib.util
import json
from pathlib import Path


NODE_FILE = Path(__file__).parents[1] / "integrations/comfyui/qwen_tts_api_nodes/nodes.py"


def load_nodes():
    spec = importlib.util.spec_from_file_location("qwen_tts_api_nodes_test", NODE_FILE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_node_package_has_expected_mappings():
    nodes = load_nodes()
    assert set(nodes.NODE_CLASS_MAPPINGS) == {
        "QwenTTSServer", "QwenTTSSynthesize", "QwenTTSCloneVoice",
        "QwenTTSVoiceSelector", "QwenTTSEmotionScript", "QwenTTSHealth",
    }


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
    normalized, segments_json, clean = nodes.QwenTTSEmotionScriptNode().parse(
        "Тихо. [voice:happy] Отлично!", '{"neutral":"clone:N","happy":"clone:H"}'
    )
    segments = json.loads(segments_json)
    assert segments[0]["voice"] == "clone:N"
    assert segments[1]["voice"] == "clone:H"
    assert "[voice:happy]" in normalized
    assert "[voice:happy]" not in clean
