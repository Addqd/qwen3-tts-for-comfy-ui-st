from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
from typing import Any

import torch
from huggingface_hub import snapshot_download

from training.russian_adaptation.common import iter_jsonl, load_plan, project_path, write_jsonl_atomic


def _load_partial(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    return {row["id"]: row for row in iter_jsonl(path)}


def encode_manifest(raw_manifest: Path, output_manifest: Path, tokenizer: Any, batch_size: int) -> int:
    rows = list(iter_jsonl(raw_manifest))
    if output_manifest.is_file():
        existing = list(iter_jsonl(output_manifest))
        if [row.get("id") for row in existing] != [row.get("id") for row in rows]:
            raise ValueError(f"Existing code manifest does not match raw selection; preserved: {output_manifest}")
        if not all(row.get("audio_codes") for row in existing):
            raise ValueError(f"Existing code manifest is incomplete; preserved: {output_manifest}")
        print(f"Reusing {len(existing)} encoded rows from {output_manifest.name}", flush=True)
        return len(existing)
    partial_path = output_manifest.with_suffix(".partial.jsonl")
    completed = _load_partial(partial_path)
    pending = [row for row in rows if row["id"] not in completed]

    for offset in range(0, len(pending), batch_size):
        batch = pending[offset : offset + batch_size]
        audio_paths = [str(project_path(row["audio"])) for row in batch]
        encoded = tokenizer.encode(audio_paths, return_dict=True)
        if len(encoded.audio_codes) != len(batch):
            raise RuntimeError("Speech tokenizer returned a mismatched batch")
        for row, codes in zip(batch, encoded.audio_codes):
            output_row = dict(row)
            output_row["audio_codes"] = codes.detach().to("cpu", dtype=torch.int32).tolist()
            completed[row["id"]] = output_row
        ordered = [completed[row["id"]] for row in rows if row["id"] in completed]
        write_jsonl_atomic(partial_path, ordered)
        print(f"Encoded {len(completed)}/{len(rows)} rows from {raw_manifest.name}", flush=True)

    if len(completed) != len(rows):
        raise RuntimeError(f"Incomplete code manifest: {len(completed)}/{len(rows)}")
    final_rows = [completed[row["id"]] for row in rows]
    write_jsonl_atomic(output_manifest, final_rows)
    partial_path.unlink(missing_ok=True)
    return len(final_rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Encode selected audio with the official Qwen 12Hz tokenizer")
    parser.parse_args()
    plan = load_plan()
    tokenizer_config = plan["tokenizer"]
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for the user-run tokenizer preparation step")

    from qwen_tts.inference.qwen3_tts_tokenizer import Qwen3TTSTokenizer

    tokenizer_snapshot = snapshot_download(
        repo_id=tokenizer_config["repo_id"],
        revision=tokenizer_config["revision"],
        cache_dir=project_path("model_cache"),
    )
    tokenizer = Qwen3TTSTokenizer.from_pretrained(
        str(tokenizer_snapshot),
        revision=tokenizer_config["revision"],
        cache_dir=project_path("model_cache"),
        device_map="cuda:0",
        torch_dtype=torch.float16,
    )
    manifest_dir = project_path("training_data/golos_balalaika/manifests")
    counts = {}
    for split in ("train", "eval"):
        counts[split] = encode_manifest(
            manifest_dir / f"{split}_raw.jsonl",
            manifest_dir / f"{split}_with_codes.jsonl",
            tokenizer,
            int(tokenizer_config["batch_size"]),
        )
    del tokenizer
    gc.collect()
    torch.cuda.empty_cache()
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
