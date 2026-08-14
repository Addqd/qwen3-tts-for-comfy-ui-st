from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import qwen3_tts_st.app as app_module
from qwen3_tts_st.config import load_config
from qwen3_tts_st.runtime_settings import RuntimeSettingsStore
from qwen3_tts_st.service import TTSService
from qwen3_tts_st.silero_preprocessing import SileroPreprocessingError, SileroPreprocessor, format_stress_markers


def test_stress_marker_formats_and_yo():
    source = "молок+о, Л+ёва, уж+е"
    assert format_stress_markers(source, "plus") == "молок+о, Лёва, уж+е"
    assert format_stress_markers(source, "acute") == "молоко́, Лёва, уже́"
    assert format_stress_markers(source, "apostrophe") == "молоко', Лёва, уже'"


@pytest.mark.parametrize(
    ("text_enhancement", "auto_stress", "expected"),
    [
        ("off", "off", "я hello"),
        ("silero", "off", "TE[я hello]"),
        ("off", "silero", "+я hello"),
        ("silero", "silero", "TE[+я hello]"),
    ],
)
def test_silero_components_are_independent(monkeypatch, tmp_path, text_enhancement, auto_stress, expected):
    runtime = SileroPreprocessor(tmp_path / "unused.json")

    class TE:
        @staticmethod
        def enhance_text(text, language):
            assert language == "ru"
            return f"TE[{text}]"

    monkeypatch.setattr(runtime, "_load_te", lambda: TE())
    monkeypatch.setattr(runtime, "_load_stress", lambda: lambda text: text.replace("я", "+я"))
    result, _ = runtime.process("я hello", text_enhancement, auto_stress, "plus")
    assert result == expected


def test_enabled_silero_failure_is_not_silent(monkeypatch, tmp_path):
    runtime = SileroPreprocessor(tmp_path / "unused.json")

    def fail():
        raise RuntimeError("load failed")

    monkeypatch.setattr(runtime, "_load_stress", fail)
    with pytest.raises(SileroPreprocessingError, match="Silero Stress failed: load failed"):
        runtime.process("текст", "off", "silero", "plus")


@pytest.mark.asyncio
async def test_manual_pronunciation_is_protected_and_request_override_wins(tmp_path, monkeypatch):
    config = load_config()
    config.data["voices"]["library_dir"] = str(tmp_path / "voices")
    config.data["runtime"]["settings_file"] = str(tmp_path / "settings.json")
    service = TTSService(config)
    service.settings.update({
        "russian_normalization": "off",
        "auto_stress": "silero",
        "stress_format": "plus",
        "text_enhancement": "silero",
        "pronunciation_defaults": {"Qwen": "default"},
    })

    def automatic(text, *_settings):
        return f"AUTO[{text}]", {"stress_seconds": 0.01, "text_enhancement_seconds": 0.02}

    monkeypatch.setattr(service.silero, "process", automatic)
    request = SimpleNamespace(
        input="До Qwen after",
        pronunciation_overrides={"Qwen": r"request\1"},
        russian_normalization=None,
    )
    prepared, replacements, _, stress_seconds, te_seconds = await service._prepare_text(request, service.settings.current())
    assert prepared == r"AUTO[До ]request\1AUTO[ after]"
    assert replacements == 1
    assert stress_seconds == pytest.approx(0.02)
    assert te_seconds == pytest.approx(0.04)
    await service.client.aclose()


def test_runtime_settings_defaults_persistence_and_old_file_compatibility(tmp_path):
    config = load_config()
    config.data["runtime"]["settings_file"] = str(tmp_path / "settings.json")
    store = RuntimeSettingsStore(config)
    assert store.current()["auto_stress"] == "silero"
    assert store.current()["stress_format"] == "plus"
    assert store.current()["text_enhancement"] == "off"
    store.update({"auto_stress": "off", "stress_format": "acute", "text_enhancement": "silero"})
    reloaded = RuntimeSettingsStore(config).current()
    assert (reloaded["auto_stress"], reloaded["stress_format"], reloaded["text_enhancement"]) == ("off", "acute", "silero")

    store.path.write_text('{"russian_normalization":"basic"}', encoding="utf-8")
    legacy = RuntimeSettingsStore(config).current()
    assert (legacy["auto_stress"], legacy["stress_format"], legacy["text_enhancement"]) == ("silero", "plus", "off")


def test_runtime_settings_api_exposes_new_keys_and_rejects_model_variant(tmp_path, monkeypatch):
    config = load_config()
    config.data["runtime"]["settings_file"] = str(tmp_path / "settings.json")

    class FakeService:
        def __init__(self, active_config):
            self.settings = RuntimeSettingsStore(active_config)

        async def startup(self):
            return None

        async def shutdown(self):
            return None

    monkeypatch.setattr(app_module, "TTSService", FakeService)
    payload = {
        "language": "Russian",
        "russian_normalization": "full",
        "auto_stress": "off",
        "stress_format": "apostrophe",
        "text_enhancement": "silero",
        "pronunciation_defaults": {},
        "seed": -1,
        "max_new_tokens": 4096,
        "temperature": 0.75,
        "top_k": 40,
        "top_p": 0.9,
        "repetition_penalty": 1.05,
    }
    with TestClient(app_module.create_app(config=config)) as client:
        defaults = client.get("/admin/runtime-settings").json()["settings"]
        assert {"auto_stress", "stress_format", "text_enhancement"} <= set(defaults)
        saved = client.put("/admin/runtime-settings", json=payload)
        assert saved.status_code == 200
        assert saved.json()["settings"]["text_enhancement"] == "silero"
        rejected = client.put("/admin/runtime-settings", json={**payload, "model_variant": "q8"})
        assert rejected.status_code == 422
