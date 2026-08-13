from __future__ import annotations

from pathlib import Path

import yaml

from qwen3_tts_st.config import load_config
from qwen3_tts_st.models import ModelRegistry


ROOT = Path(__file__).parents[1]


def test_benchmark_configs_keep_bounded_tokens_in_model_runtime():
    for name in ("config.cuda-fp32-benchmark.yaml", "config.cuda-on-demand-benchmark.yaml"):
        path = ROOT / "config" / name
        override = yaml.safe_load(path.read_text(encoding="utf-8"))
        runtime = override["models"]["available"]["qwen3-tts-0.6b"]["runtime"]
        assert runtime["max_new_tokens"] == 64
        assert "max_new_tokens" not in override["models"]


def test_old_style_local_model_overrides_update_default_registry(tmp_path):
    legacy = tmp_path / "config.local.yaml"
    legacy.write_text(
        """
model:
  id: Local/Legacy-Qwen
  cache_dir: legacy-cache
  dtype: float16
  attention: eager
  max_new_tokens: 77
""".strip(),
        encoding="utf-8",
    )
    config = load_config(legacy)
    resolved = ModelRegistry(config).resolve("tts-1-ru")
    assert resolved.hf_id == "Local/Legacy-Qwen"
    assert resolved.spec.runtime["dtype"] == "float16"
    assert resolved.spec.runtime["attention"] == "eager"
    assert resolved.spec.runtime["max_new_tokens"] == 77
    assert config.get("models.cache_dir") == "legacy-cache"


def test_modern_model_override_wins_over_legacy_key_in_same_file(tmp_path):
    mixed = tmp_path / "config.local.yaml"
    mixed.write_text(
        """
model:
  id: Local/Legacy-Qwen
  dtype: float16
models:
  available:
    qwen3-tts-0.6b:
      hf_id: Local/Modern-Qwen
      runtime:
        dtype: float32
""".strip(),
        encoding="utf-8",
    )
    resolved = ModelRegistry(load_config(mixed)).resolve("tts-1-ru")
    assert resolved.hf_id == "Local/Modern-Qwen"
    assert resolved.spec.runtime["dtype"] == "float32"
