from __future__ import annotations

import importlib.util
import ast
import io
import json
from pathlib import Path
import wave

import numpy as np

from qwen3_tts_st.emotion import ALLOWED_STYLES as BACKEND_STYLES, parse_emotion_script_detailed


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
        "compare_voice_samples_ru.json",
        "emotion_router_test_ru.json",
        "emotion_script_preview.json",
        "text_to_speech_ru.json",
        "text_to_speech_models_ru.json",
        "text_to_speech_with_emotions_ru.json",
        "voice_profile_from_wav_ru.json",
        "voice_clone_and_synthesize_ru.json",
    }
    paths = {path.name: path for path in WORKFLOW_DIR.glob("*.json")}
    assert required <= paths.keys()
    builtins = {"LoadAudio", "PreviewAudio", "SaveAudio"}
    for path in paths.values():
        workflow = json.loads(path.read_text(encoding="utf-8"))
        assert workflow["nodes"]
        node_ids = {node["id"] for node in workflow["nodes"]}
        link_ids = {link[0] for link in workflow["links"]}
        assert workflow["last_node_id"] >= max(node_ids)
        assert workflow["last_link_id"] >= max(link_ids, default=0)
        assert all(link[1] in node_ids and link[3] in node_ids for link in workflow["links"])
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
        'Тихо. [voice:happy] "Отлично!" Потом спокойно.', '{"neutral":"clone:N","happy":"clone:H"}'
    ))
    segments = json.loads(segments_json)
    assert segments[0]["voice"] == "clone:N"
    assert segments[0]["kind"] == "narration"
    assert segments[1]["voice"] == "clone:H"
    assert segments[1]["kind"] == "dialogue"
    assert segments[2]["voice"] == "clone:N"
    assert segments[2]["style"] == "neutral"
    assert '[voice:happy] "Отлично!"' in normalized
    assert "[voice:happy]" not in clean
    assert styles == "neutral, happy"


def test_comfy_parser_matches_backend_contract_and_styles():
    nodes = load_nodes()
    assert set(nodes.ALLOWED_STYLES) == set(BACKEND_STYLES)
    corpus = [
        'Она улыбнулась. [voice:pleasure] "М-м... хорошо." Она приблизилась. '
        '[voice:intimate] "Это только между нами."',
        '[voice:pleasure]\n"Тест."',
        '[voice:intimate]    "Тест."',
        '[voice:unknown] "Что?"',
        '[voice: happy] "Malformed."',
        '[voice:angry] Она отвернулась. "Обычная реплика."',
        '[voice:tense] "Незакрытая реплика',
    ]
    for text in corpus:
        backend_segments, backend_warnings = parse_emotion_script_detailed(text)
        comfy_segments, comfy_warnings = nodes._quote_aware_segments(text)
        assert comfy_segments == [item.to_dict() for item in backend_segments]
        assert comfy_warnings == backend_warnings


def test_clone_voice_dropdown_contains_all_delivery_styles():
    nodes = load_nodes()
    styles = nodes.QwenTTSCloneVoiceNode.INPUT_TYPES()["required"]["style"][0]
    assert styles == list(nodes.ALLOWED_STYLES)
    assert styles[-2:] == ["pleasure", "intimate"]


def test_synthesize_exposes_model_quality_and_russian_controls(monkeypatch):
    nodes = load_nodes()
    inputs = nodes.QwenTTSSynthesizeNode.INPUT_TYPES()
    assert inputs["required"]["model"][0] == [
        "Inherit Server model",
        "Backend Default (tts-1-ru)",
        "0.6B Fast (tts-1-ru-fast)",
        "1.7B Quality (tts-1-ru-quality)",
    ]
    assert inputs["optional"]["generation_preset"][0] == ["Default", "Stable Russian"]
    assert inputs["optional"]["russian_normalization"][0] == [
        "Off",
        "Basic Russian",
        "Full Russian",
    ]
    assert inputs["required"]["model"][1]["default"] == "Inherit Server model"

    captured = {}

    class Headers(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    def response(_server, path, payload=None):
        assert path == "/v1/audio/speech"
        captured.update(payload)
        return b"RIFF", Headers({"X-Audio-Duration": "1.25", "X-TTS-Resolved-Model": "qwen3-tts-1.7b"})

    class FakeWaveform:
        shape = (1, 1, 30000)

    monkeypatch.setattr(nodes, "_json_request", response)
    monkeypatch.setattr(nodes, "_load_comfy_audio", lambda _path: {"waveform": FakeWaveform(), "sample_rate": 24000})
    result = result_of(
        nodes.QwenTTSSynthesizeNode().synthesize(
            {"endpoint": "http://127.0.0.1:8020", "timeout": 10, "model": "tts-1-ru"},
            "Qwen готов.",
            "clone:QwenDemoRussianNeutral",
            1.0,
            "1.7B Quality (tts-1-ru-quality)",
            "wav",
            "all",
            generation_preset="Stable Russian",
            russian_normalization="Full Russian",
            pronunciation_overrides="Qwen = куэн",
        )
    )
    assert captured["model"] == "tts-1-ru-quality"
    assert captured["generation_preset"] == "stable_russian"
    assert captured["russian_normalization"] == "full"
    assert captured["pronunciation_overrides"] == {"Qwen": "куэн"}
    metadata = json.loads(result[2])
    assert metadata["resolved_model"] == "qwen3-tts-1.7b"
    assert metadata["pronunciation_override_count"] == 1


def test_synthesize_inherits_quality_server_model_and_keeps_legacy_values(monkeypatch):
    nodes = load_nodes()
    captured = {}

    class FakeWaveform:
        shape = (1, 1, 2400)

    def response(_server, _path, payload=None):
        captured.update(payload)
        return b"RIFF", {"X-Audio-Duration": "0.1"}

    monkeypatch.setattr(nodes, "_json_request", response)
    monkeypatch.setattr(
        nodes,
        "_load_comfy_audio",
        lambda _path: {"waveform": FakeWaveform(), "sample_rate": 24000},
    )
    nodes.QwenTTSSynthesizeNode().synthesize(
        {"endpoint": "http://127.0.0.1:8020", "timeout": 10, "model": "tts-1-ru-quality"},
        "Тест.",
        "clone:QwenDemoRussianNeutral",
        1.0,
        "Inherit Server model",
        "wav",
        "all",
    )
    assert captured["model"] == "tts-1-ru-quality"
    legacy_values = {
        "tts-1-ru": "tts-1-ru",
        "tts-1-ru-fast": "tts-1-ru-fast",
        "tts-1-ru-quality": "tts-1-ru-quality",
        "Backend Default (tts-1-ru)": "tts-1-ru",
        "0.6B Fast (tts-1-ru-fast)": "tts-1-ru-fast",
        "1.7B Quality (tts-1-ru-quality)": "tts-1-ru-quality",
    }
    assert {value: nodes._model_alias(value, {}) for value in legacy_values} == legacy_values


def test_comfy_clone_conversion_preserves_input_sample_rate():
    nodes = load_nodes()

    class FakeTensor:
        def __init__(self, value):
            self.value = value

        def detach(self):
            return self

        def cpu(self):
            return self

        def float(self):
            return self

        def numpy(self):
            return self.value

    audio = {
        "waveform": FakeTensor(np.zeros((1, 2, 4410), dtype=np.float32)),
        "sample_rate": 44100,
    }
    payload = nodes._audio_to_wav_bytes(audio)
    with wave.open(io.BytesIO(payload), "rb") as handle:
        assert handle.getframerate() == 44100
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2


def test_emotion_script_unknown_tag_is_removed_and_falls_back_to_neutral():
    nodes = load_nodes()
    normalized, segments_json, clean, styles = result_of(nodes.QwenTTSEmotionScriptNode().parse(
        'Начало. [voice:excited] "Что?"', '{"neutral":"clone:N"}'
    ))
    segments = json.loads(segments_json)
    assert segments[-1]["style"] == "neutral"
    assert segments[-1]["kind"] == "dialogue"
    assert segments[-1]["voice"] == "clone:N"
    assert "voice:excited" not in normalized
    assert "voice:excited" not in clean
    assert styles == "neutral"


def test_emotion_script_ignores_tag_before_narration_and_uses_neutral_mapping():
    nodes = load_nodes()
    value = nodes.QwenTTSEmotionScriptNode().parse(
        '[voice:angry] Она отвернулась. "Я не хочу говорить."',
        '{"neutral":"clone:N","angry":"clone:A"}',
    )
    normalized, segments_json, clean, styles = result_of(value)
    segments = json.loads(segments_json)
    assert [(item["kind"], item["style"], item["voice"]) for item in segments] == [
        ("narration", "neutral", "clone:N"),
        ("dialogue", "neutral", "clone:N"),
    ]
    assert "voice:" not in normalized
    assert "voice:" not in clean
    assert styles == "neutral"
    assert value["ui"]["qwen_tts_emotion"][0]["warnings"] == [
        "voice_tag_ignored_no_following_quoted_dialogue"
    ]


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
