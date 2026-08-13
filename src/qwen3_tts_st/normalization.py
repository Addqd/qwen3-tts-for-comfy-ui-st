from __future__ import annotations

import re
from typing import Any, Mapping


def parse_pronunciation_overrides(value: Any) -> dict[str, str]:
    if value is None or value == "":
        return {}
    if isinstance(value, Mapping):
        result = {str(k).strip(): str(v).strip() for k, v in value.items()}
    elif isinstance(value, str):
        result = {}
        for number, raw in enumerate(value.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise ValueError(f"Pronunciation line {number} must use source = replacement")
            source, replacement = (part.strip() for part in line.split("=", 1))
            result[source] = replacement
    else:
        raise ValueError("Pronunciation overrides must be an object or source = replacement text")
    if any(not source or not replacement for source, replacement in result.items()):
        raise ValueError("Pronunciation source and replacement must be non-empty")
    return result


def merge_pronunciation(*values: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        result.update(parse_pronunciation_overrides(value))
    return result


def apply_pronunciation(text: str, dictionary: Mapping[str, str]) -> tuple[str, int]:
    result = text
    count = 0
    for source in sorted(dictionary, key=len, reverse=True):
        replacement = dictionary[source]
        result, changed = re.subn(
            re.escape(source), lambda _match, literal=replacement: literal, result, flags=re.I
        )
        count += changed
    return result, count


def normalize_russian_text(text: str, mode: str) -> str:
    if mode == "off":
        return text
    value = re.sub(r"\s+", " ", text).strip()
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    if mode == "basic":
        return value
    if mode != "full":
        raise ValueError(f"Unknown Russian normalization mode: {mode}")
    value = re.sub(r"(?<=\d)\s*%", " процентов", value)
    value = re.sub(r"№\s*(\d+)", r"номер \1", value)
    return value
