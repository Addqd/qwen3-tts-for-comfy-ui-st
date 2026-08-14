from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class PronunciationSpan:
    text: str
    replacement: str | None = None


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
    if not dictionary:
        return text, 0
    sources = sorted(dictionary, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(source) for source in sources), re.I)
    replacements = {source.casefold(): replacement for source, replacement in dictionary.items()}
    return pattern.subn(lambda match: replacements[match.group(0).casefold()], text)


def split_pronunciation_spans(text: str, dictionary: Mapping[str, str]) -> tuple[list[PronunciationSpan], int]:
    if not dictionary:
        return [PronunciationSpan(text)], 0
    sources = sorted(dictionary, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(source) for source in sources), re.I)
    replacements = {source.casefold(): replacement for source, replacement in dictionary.items()}
    spans: list[PronunciationSpan] = []
    cursor = 0
    count = 0
    for match in pattern.finditer(text):
        if match.start() > cursor:
            spans.append(PronunciationSpan(text[cursor:match.start()]))
        spans.append(PronunciationSpan(match.group(0), replacements[match.group(0).casefold()]))
        cursor = match.end()
        count += 1
    if cursor < len(text):
        spans.append(PronunciationSpan(text[cursor:]))
    return spans or [PronunciationSpan(text)], count


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
