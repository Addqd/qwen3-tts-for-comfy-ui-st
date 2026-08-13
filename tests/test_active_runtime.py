from __future__ import annotations

import ast
import json
from pathlib import Path

from qwen3_tts_st.config import load_config
from qwen3_tts_st.runtime_settings import RuntimeSettingsStore
from qwen3_tts_st.voices import VoiceLibrary


ROOT = Path(__file__).resolve().parents[1]


def test_active_python_is_lightweight_and_config_is_qwentts_only(tmp_path):
    forbidden = {"torch", "torchaudio", "transformers", "qwen_tts"}
    imports = set()
    for path in (ROOT / "src" / "qwen3_tts_st").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
    assert imports.isdisjoint(forbidden)
    config = load_config(ROOT / "config" / "config.example.yaml")
    assert config.get("qwentts.backend") == "CUDA0"
    assert config.get("qwentts.model_id") == "tts-1-ru"
    assert config.get("voices.default_voice") == "clone:test_ru_dima_neutral"


def test_legacy_runtime_settings_are_migrated_to_real_qwentts_controls(tmp_path):
    config = load_config(ROOT / "config" / "config.example.yaml")
    config.data["runtime"]["settings_file"] = str(tmp_path / "settings.json")
    Path(config.data["runtime"]["settings_file"]).write_text(json.dumps({
        "active_model": "tts-1-ru-quality",
        "russian_normalization": "basic",
        "pronunciation_defaults": {"Qwen": "куэн"},
    }), encoding="utf-8")
    settings = RuntimeSettingsStore(config).current()
    assert settings["russian_normalization"] == "basic"
    assert settings["pronunciation_defaults"] == {"Qwen": "куэн"}
    assert "active_model" not in settings


def test_synthetic_profile_uses_persisted_qwentts_assets(tmp_path):
    config = load_config(ROOT / "config" / "config.example.yaml")
    profile_dir = tmp_path / "profiles" / "synthetic"
    profile_dir.mkdir(parents=True)
    (profile_dir / "reference.wav").write_bytes(b"RIFFsyntheticWAVE")
    (profile_dir / "reference.spk").write_bytes(b"s" * 8192)
    (profile_dir / "reference.rvq").write_bytes(b"r" * 32)
    (profile_dir / "metadata.json").write_text(json.dumps({
        "profile_id": "synthetic",
        "display_name": "synthetic",
        "character": "Synthetic",
        "reference_audio": "reference.wav",
        "ref_text": "Синтетический тестовый профиль.",
        "language": "Russian",
    }, ensure_ascii=False), encoding="utf-8")
    library = VoiceLibrary(tmp_path, config)
    profile = library.resolve("clone:synthetic")
    assert profile.voice_id == "clone:synthetic"
    assert profile.spk_path.stat().st_size == 8192
    assert profile.rvq_path.stat().st_size > 0
    public = profile.public()
    assert set(public) == {
        "voice_id", "profile_id", "display_name", "character", "language", "ref_text",
        "reference_available", "spk_available", "rvq_available", "ready",
    }
