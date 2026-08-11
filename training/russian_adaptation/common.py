from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable, Iterator


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parents[1]
PLAN_PATH = PACKAGE_DIR / "plan.json"


def load_plan() -> dict[str, Any]:
    with PLAN_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def project_path(relative: str | Path) -> Path:
    path = (PROJECT_ROOT / relative).resolve()
    path.relative_to(PROJECT_ROOT)
    return path


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def write_jsonl_atomic(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def stable_fraction(seed: int, value: str) -> float:
    digest = hashlib.sha256(f"{seed}:{value}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64 - 1)


def ordinary_text(metadata: dict[str, Any]) -> str:
    text = str(metadata.get("punct") or metadata.get("rover") or "").strip()
    return re.sub(r"\s+", " ", text)


def has_russian_text(text: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", text))


def source_id(split: str, shard: str, key: str) -> str:
    return hashlib.sha256(f"{split}/{shard}/{key}".encode("utf-8")).hexdigest()[:24]
