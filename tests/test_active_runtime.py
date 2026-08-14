from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import qwen3_tts_st.config as config_module

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
    talker, codec = config.qwentts_models()
    assert talker.name == "qwen-talker-1.7b-base-BF16.gguf"
    assert codec.name == "qwen-tokenizer-12hz-BF16.gguf"
    assert config.get("qwentts.active_model") is None
    manifest = json.loads((ROOT / "config" / "qwentts-runtime.json").read_text(encoding="utf-8"))
    assert set(manifest["models"]["files"]) == {
        "qwen-talker-1.7b-base-BF16.gguf", "qwen-tokenizer-12hz-BF16.gguf"
    }
    assert config.get("voices.default_voice") == "clone:test_ru_dima_neutral"
    active_text = "\n".join(
        path.read_text(encoding="utf-8-sig")
        for path in [
            ROOT / "config" / "config.example.yaml",
            ROOT / "config" / "qwentts-runtime.json",
            ROOT / "scripts" / "ensure-qwentts-models.ps1",
            ROOT / "scripts" / "verify-qwentts-runtime.ps1",
            ROOT / "scripts" / "qwentts-runner.py",
            ROOT / "start.ps1",
        ]
    )
    assert "Q8_0" not in active_text
    assert "active_model" not in active_text


def test_local_model_paths_cannot_override_the_pinned_bf16_pair(tmp_path):
    local = tmp_path / "config.local.yaml"
    local.write_text(
        "qwentts:\n"
        "  talker_model: C:/custom/other-talker.gguf\n"
        "  codec_model: C:/custom/other-codec.gguf\n"
        "  active_model: q8\n",
        encoding="utf-8",
    )
    talker, codec = load_config(local).qwentts_models()
    assert talker.name == "qwen-talker-1.7b-base-BF16.gguf"
    assert codec.name == "qwen-tokenizer-12hz-BF16.gguf"
    assert talker.parent == codec.parent == ROOT / "runtime" / "qwentts" / "models"


def test_local_executable_paths_cannot_override_the_pinned_runtime(tmp_path):
    local = tmp_path / "config.local.yaml"
    local.write_text(
        "qwentts:\n"
        "  executable: C:/custom/tts-server.exe\n"
        "  codec_executable: C:/custom/qwen-codec.exe\n",
        encoding="utf-8",
    )
    server, codec = load_config(local).qwentts_executables()
    assert server == ROOT / "runtime" / "qwentts" / "bin" / "tts-server.exe"
    assert codec == ROOT / "runtime" / "qwentts" / "bin" / "qwen-codec.exe"
    runner = (ROOT / "scripts" / "qwentts-runner.py").read_text(encoding="utf-8")
    voices = (ROOT / "src" / "qwen3_tts_st" / "voices.py").read_text(encoding="utf-8")
    assert "config.qwentts_executables()" in runner
    assert "self.config.qwentts_executables()" in voices
    assert 'path("qwentts.executable"' not in runner
    assert 'path("qwentts.codec_executable"' not in voices


def test_local_backend_override_cannot_disable_cuda0(tmp_path):
    local = tmp_path / "config.local.yaml"
    local.write_text("qwentts:\n  backend: CPU\n", encoding="utf-8")
    config = load_config(local)
    assert config.qwentts_backend() == "CUDA0"
    runner = (ROOT / "scripts" / "qwentts-runner.py").read_text(encoding="utf-8")
    assert 'environment["GGML_BACKEND"] = config.qwentts_backend()' in runner


@pytest.mark.parametrize("files", [None, "bad", 3])
def test_executable_manifest_files_must_be_an_object(tmp_path, monkeypatch, files):
    manifest = tmp_path / "qwentts-runtime.json"
    manifest.write_text(json.dumps({
        "files": files,
        "models": {"files": list(config_module.QWENTTS_BF16_FILES)},
    }), encoding="utf-8")
    monkeypatch.setattr(config_module, "QWENTTS_MANIFEST", manifest)
    with pytest.raises(ValueError, match="pinned qwentts runtime manifest is invalid"):
        load_config(manifest).qwentts_executables()


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
    variant_dir = profile_dir / "variants" / "bf16"
    variant_dir.mkdir(parents=True)
    (profile_dir / "reference.wav").write_bytes(b"RIFFsyntheticWAVE")
    (variant_dir / "reference.spk").write_bytes(b"s" * 8192)
    (variant_dir / "reference.rvq").write_bytes(b"r" * 32)
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
