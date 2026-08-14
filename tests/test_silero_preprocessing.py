from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import qwen3_tts_st.app as app_module
import qwen3_tts_st.silero_preprocessing as silero_module
from qwen3_tts_st.config import load_config
from qwen3_tts_st.runtime_settings import RuntimeSettingsStore
from qwen3_tts_st.service import TTSService
from qwen3_tts_st.silero_preprocessing import SileroPreprocessingError, SileroPreprocessor, format_stress_markers


ROOT = Path(__file__).resolve().parents[1]


def test_stress_marker_formats_and_yo():
    source = "молок+о, Л+ёва, уж+е"
    assert format_stress_markers(source, "plus") == "молок+о, Л+ёва, уж+е"
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
    monkeypatch.setattr(runtime, "_load_stress", lambda: lambda text, **_kwargs: text.replace("я", "+я"))
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

    calls = []

    def automatic(text, *_settings):
        calls.append((text, _settings[-1]))
        return f"AUTO[{text}]", {"stress_seconds": 0.01, "text_enhancement_seconds": 0.02}

    monkeypatch.setattr(service.silero, "process", automatic)
    request = SimpleNamespace(
        input="До Qwen after",
        pronunciation_overrides={"Qwen": r"request\1"},
        russian_normalization=None,
    )
    prepared, replacements, _, stress_seconds, te_seconds = await service._prepare_text(request, service.settings.current())
    assert prepared == r"AUTO[До request\1 after]"
    assert replacements == 1
    assert calls == [("До Qwen after", ["Qwen"])]
    assert stress_seconds == pytest.approx(0.01)
    assert te_seconds == pytest.approx(0.02)
    await service.client.aclose()


def test_whole_context_te_preserves_manual_terms_and_stress_ignores_them(monkeypatch, tmp_path):
    runtime = SileroPreprocessor(tmp_path / "unused.json")
    calls = {"te": [], "stress": []}

    class TE:
        @staticmethod
        def enhance_text(text, language):
            calls["te"].append((text, language))
            return text

    def stress(text, **kwargs):
        calls["stress"].append((text, kwargs["words_to_ignore"]))
        return text.replace("текст", "т+екст")

    monkeypatch.setattr(runtime, "_load_te", lambda: TE())
    monkeypatch.setattr(runtime, "_load_stress", lambda: stress)
    result, _ = runtime.process("До Qwen идёт текст", "silero", "silero", "plus", ["Qwen"])
    assert result == "До Qwen идёт т+екст"
    assert calls["te"] == [("До Qwen идёт текст", "ru")]
    assert calls["stress"] == [("До Qwen идёт текст", ["qwen"])]


@pytest.mark.asyncio
async def test_te_that_adds_a_protected_occurrence_is_a_backend_failure(tmp_path, monkeypatch):
    config = load_config()
    config.data["voices"]["library_dir"] = str(tmp_path / "voices")
    config.data["runtime"]["settings_file"] = str(tmp_path / "settings.json")
    service = TTSService(config)
    service.settings.update({
        "russian_normalization": "off",
        "auto_stress": "off",
        "text_enhancement": "silero",
        "pronunciation_defaults": {"Qwen": "куэн"},
    })

    class TE:
        @staticmethod
        def enhance_text(text, _language):
            return f"{text} и Qwen"

    monkeypatch.setattr(service.silero, "_load_te", lambda: TE())
    request = SimpleNamespace(input="Qwen работает", pronunciation_overrides={}, russian_normalization=None)
    with pytest.raises(SileroPreprocessingError, match="changed a protected pronunciation term"):
        await service._prepare_text(request, service.settings.current())
    await service.client.aclose()


def test_multiword_override_does_not_disable_stress_for_same_standalone_word(monkeypatch, tmp_path):
    runtime = SileroPreprocessor(tmp_path / "unused.json")

    def stress(text, **kwargs):
        assert "старый" not in kwargs["words_to_ignore"]
        assert "замок" not in kwargs["words_to_ignore"]
        return text.replace("замок", "зам+ок")

    monkeypatch.setattr(runtime, "_load_stress", lambda: stress)
    result, _ = runtime.process("старый замок, а замок открыт", "off", "silero", "plus", ["старый замок"])
    assert result == "старый замок, а зам+ок открыт"


def _silero_assets(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    catalogue = tmp_path / "models.yml"
    te_model = tmp_path / "te.pt"
    stress_model = tmp_path / "accentor.pt"
    for path, payload in ((catalogue, b"catalogue"), (te_model, b"te"), (stress_model, b"stress")):
        path.write_bytes(payload)
    digests = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (catalogue, te_model, stress_model)}
    provenance = tmp_path / "silero-runtime.json"
    provenance.write_text(json.dumps({
        "schema": 1,
        "catalogue": {"revision": "pinned", "sha256": digests[str(catalogue)]},
        "text_enhancement_model": {"sha256": digests[str(te_model)]},
    }), encoding="utf-8")
    state = tmp_path / "provisioned.json"
    state.write_text(json.dumps({
        "schema": 3,
        "catalogue": str(catalogue),
        "catalogue_revision": "pinned",
        "catalogue_sha256": digests[str(catalogue)],
        "te_model": str(te_model),
        "te_model_sha256": digests[str(te_model)],
        "model_files": [str(catalogue), str(te_model), str(stress_model)],
        "stress_assets": [str(stress_model)],
        "text_enhancement_assets": [str(catalogue), str(te_model)],
        "asset_sha256": digests,
    }), encoding="utf-8")
    return state, provenance, catalogue, te_model, stress_model


def test_stress_verification_does_not_depend_on_te_asset(tmp_path):
    state, provenance, _catalogue, te_model, _stress_model = _silero_assets(tmp_path)
    te_model.write_bytes(b"broken")
    SileroPreprocessor(state, provenance)._require_provisioned("stress")


def test_stress_verification_rejects_empty_stress_asset_set(tmp_path):
    state, provenance, catalogue, te_model, _stress_model = _silero_assets(tmp_path)
    payload = json.loads(state.read_text(encoding="utf-8"))
    payload["stress_assets"] = []
    state.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SileroPreprocessingError, match="Silero Stress assets are missing"):
        SileroPreprocessor(state, provenance)._require_provisioned("stress")


def test_multiword_placeholders_do_not_collide_with_user_text(monkeypatch, tmp_path):
    runtime = SileroPreprocessor(tmp_path / "unused.json")
    tokens = iter((SimpleNamespace(hex="collision"), SimpleNamespace(hex="unique")))
    monkeypatch.setattr(silero_module, "uuid4", lambda: next(tokens))
    monkeypatch.setattr(runtime, "_load_stress", lambda: lambda text, **_kwargs: text)
    source = "qwenprotectedcollisiontoken и старый замок"
    result, _ = runtime.process(source, "off", "silero", "plus", ["старый замок"])
    assert result == source


def test_te_verification_does_not_depend_on_stress_asset(tmp_path):
    state, provenance, _catalogue, _te_model, stress_model = _silero_assets(tmp_path)
    stress_model.write_bytes(b"broken")
    SileroPreprocessor(state, provenance)._require_provisioned("text_enhancement")


def test_enabled_broken_silero_component_reports_its_own_error(tmp_path):
    state, provenance, _catalogue, te_model, stress_model = _silero_assets(tmp_path)
    te_model.write_bytes(b"broken")
    with pytest.raises(SileroPreprocessingError, match="Silero Text Enhancement integrity"):
        SileroPreprocessor(state, provenance)._require_provisioned("text_enhancement")
    stress_model.write_bytes(b"broken")
    with pytest.raises(SileroPreprocessingError, match="Silero Stress integrity"):
        SileroPreprocessor(state, provenance)._require_provisioned("stress")


def test_provisioned_catalogue_and_model_digests_are_verified(tmp_path):
    catalogue = tmp_path / "models.yml"
    te_model = tmp_path / "te.pt"
    catalogue.write_bytes(b"catalogue")
    te_model.write_bytes(b"te-model")
    catalogue_hash = hashlib.sha256(catalogue.read_bytes()).hexdigest()
    te_hash = hashlib.sha256(te_model.read_bytes()).hexdigest()
    provenance = tmp_path / "silero-runtime.json"
    provenance.write_text(json.dumps({
        "schema": 1,
        "catalogue": {"revision": "pinned", "sha256": catalogue_hash},
        "text_enhancement_model": {"sha256": te_hash},
    }), encoding="utf-8")
    state = tmp_path / "provisioned.json"
    state.write_text(json.dumps({
        "schema": 3,
        "catalogue": str(catalogue),
        "catalogue_revision": "pinned",
        "catalogue_sha256": catalogue_hash,
        "te_model": str(te_model),
        "te_model_sha256": te_hash,
        "model_files": [str(catalogue), str(te_model)],
        "stress_assets": [],
        "text_enhancement_assets": [str(catalogue), str(te_model)],
        "asset_sha256": {str(catalogue): catalogue_hash, str(te_model): te_hash},
    }), encoding="utf-8")
    SileroPreprocessor(state, provenance)._require_provisioned("text_enhancement")
    catalogue.write_bytes(b"tampered")
    with pytest.raises(SileroPreprocessingError, match="integrity validation failed"):
        SileroPreprocessor(state, provenance)._require_provisioned("text_enhancement")


def test_failed_stress_load_does_not_poison_lazy_cache(monkeypatch, tmp_path):
    runtime = SileroPreprocessor(tmp_path / "unused.json")
    monkeypatch.setattr(runtime, "_require_provisioned", lambda _component: None)
    monkeypatch.setattr(runtime, "_configure_torch", lambda: object())
    calls = 0

    class StressModule:
        @staticmethod
        def load_accentor():
            nonlocal calls
            calls += 1
            return None if calls == 1 else (lambda text, **_kwargs: text)

    monkeypatch.setattr(silero_module.importlib, "import_module", lambda _name: StressModule())
    with pytest.raises(SileroPreprocessingError, match="invalid accentor"):
        runtime._load_stress()
    assert runtime._stress is None
    assert callable(runtime._load_stress())
    assert calls == 2


def test_text_enhancement_loader_rejects_runtime_download_and_remains_retryable(monkeypatch, tmp_path):
    runtime = SileroPreprocessor(tmp_path / "unused.json")
    monkeypatch.setattr(runtime, "_require_provisioned", lambda _component: None)

    class Hub:
        @staticmethod
        def download_url_to_file(*_args, **_kwargs):
            return None

    torch = SimpleNamespace(hub=Hub())
    monkeypatch.setattr(runtime, "_configure_torch", lambda: torch)

    mode = {"download": True}

    class TE:
        @staticmethod
        def enhance_text(text, _language):
            return text

    class TEModule:
        @staticmethod
        def silero_te():
            if mode["download"]:
                torch.hub.download_url_to_file("https://example.invalid/model", "model")
            return TE()

    monkeypatch.setattr(silero_module.importlib, "import_module", lambda _name: TEModule())
    with pytest.raises(SileroPreprocessingError, match="attempted a network download"):
        runtime._load_te()
    assert runtime._te is None
    mode["download"] = False
    assert isinstance(runtime._load_te(), TE)


def test_silero_catalogue_is_commit_pinned_with_known_digest():
    provenance = json.loads((ROOT / "config" / "silero-runtime.json").read_text(encoding="utf-8"))
    catalogue = provenance["catalogue"]
    assert catalogue["revision"] == "d9355348e2781dc8fa25a135d1602c530afae24c"
    assert catalogue["revision"] in catalogue["url"]
    assert catalogue["sha256"] == "64ccc436c72fd8c538e1a37de00301cc164a82c3d8ef0356532fe4c662ed1aa7"


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
