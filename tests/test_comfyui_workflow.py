from __future__ import annotations

import importlib.util
import json
from pathlib import Path


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
