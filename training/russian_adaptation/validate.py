from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from accelerate import init_empty_weights
from transformers import AutoConfig
import torch

from qwen_tts.core.models.configuration_qwen3_tts import Qwen3TTSConfig
from qwen_tts.core.models.modeling_qwen3_tts import Qwen3TTSForConditionalGeneration
from training.russian_adaptation.common import PACKAGE_DIR, iter_jsonl, load_plan, project_path


FORBIDDEN_TARGET_PARTS = (
    "speaker_encoder",
    "code_predictor",
    "text_embedding",
    "codec_embedding",
    "text_projection",
    "codec_head",
    "speech_tokenizer",
)


def _cached_model_path(repo_id: str) -> Path | None:
    snapshots = project_path("model_cache") / f"models--{repo_id.replace('/', '--')}" / "snapshots"
    if not snapshots.is_dir():
        return None
    matches = sorted(path for path in snapshots.iterdir() if (path / "config.json").is_file())
    return matches[-1] if matches else None


def validate_model(model_key: str, spec: dict[str, Any], pattern: re.Pattern[str], local_only: bool) -> dict[str, Any]:
    cached = _cached_model_path(spec["repo_id"])
    if local_only and cached is None:
        raise FileNotFoundError(f"No local snapshot for {spec['repo_id']}")
    source: str | Path = cached or spec["repo_id"]
    AutoConfig.register("qwen3_tts", Qwen3TTSConfig)
    config = AutoConfig.from_pretrained(source, cache_dir=project_path("model_cache"), local_files_only=local_only)
    checks = {
        "tts_model_type": getattr(config, "tts_model_type", None),
        "tts_model_size": getattr(config, "tts_model_size", None),
        "talker_hidden_size": config.talker_config.hidden_size,
        "talker_layers": config.talker_config.num_hidden_layers,
    }
    expected = {
        "tts_model_type": spec["expected_tts_model_type"],
        "tts_model_size": spec["expected_tts_model_size"],
        "talker_hidden_size": spec["expected_talker_hidden_size"],
        "talker_layers": spec["expected_talker_layers"],
    }
    if checks != expected:
        raise ValueError(f"Architecture mismatch for {model_key}: actual={checks}, expected={expected}")

    with init_empty_weights():
        model = Qwen3TTSForConditionalGeneration(config)
    matched = sorted(name for name, module in model.named_modules() if pattern.fullmatch(name))
    expected_count = spec["expected_talker_layers"] * 7
    if len(matched) != expected_count:
        raise ValueError(f"LoRA target mismatch for {model_key}: {len(matched)} != {expected_count}")
    if any(part in name for name in matched for part in FORBIDDEN_TARGET_PARTS):
        raise ValueError(f"Forbidden LoRA target for {model_key}")
    del model
    return {"model": model_key, "source": str(source), "architecture": checks, "target_modules": len(matched)}


def validate(local_only: bool, require_cuda: bool = False) -> dict[str, Any]:
    plan = load_plan()
    if plan["dataset"]["repo_id"] != "lab260/golos_balalaika":
        raise ValueError("Only lab260/golos_balalaika is permitted")
    if plan["training"]["epochs"] != 1:
        raise ValueError("The conservative preparation must remain one epoch")
    if plan["training"]["precision"] != "fp16" or plan["training"]["attention"] != "sdpa":
        raise ValueError("RTX 2070 Super preparation must use FP16 and SDPA")

    pattern = re.compile(plan["training"]["target_module_pattern"])
    model_results = []
    for model_key, spec in plan["models"].items():
        output = project_path(spec["output_dir"])
        if project_path("model_cache") in output.parents:
            raise ValueError(f"Tuned output may not be inside model_cache: {output}")
        model_results.append(validate_model(model_key, spec, pattern, local_only))

    regression_rows = list(iter_jsonl(PACKAGE_DIR / "regression_ru.jsonl"))
    required = {
        "Она пришла.",
        "Это она.",
        "Он и она пришли вместе.",
        "Она сказала, что она останется.",
        "Но она этого не знала.",
    }
    texts = {row["text"] for row in regression_rows}
    if not required.issubset(texts) or sum("игриво" in text.lower() for text in texts) < 3:
        raise ValueError("Russian regression corpus is incomplete")

    result = {
        "dataset": plan["dataset"]["repo_id"],
        "revision": plan["dataset"]["revision"],
        "models": model_results,
        "regression_texts": len(regression_rows),
        "workflow_files_touched_by_pipeline": 0,
    }
    if require_cuda:
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable; refusing to download data or begin training")
        free_bytes, total_bytes = torch.cuda.mem_get_info(0)
        result["cuda"] = {
            "device": torch.cuda.get_device_name(0),
            "free_vram_mb_at_preflight": round(free_bytes / 1024**2),
            "total_vram_mb": round(total_bytes / 1024**2),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Russian adaptation preparation")
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--require-cuda", action="store_true")
    args = parser.parse_args()
    print(json.dumps(validate(args.local_only, args.require_cuda), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
