from __future__ import annotations

import html
import re


THINK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.I | re.S)
CODE_RE = re.compile(r"```.*?```", re.S)
CHATML_RE = re.compile(r"<\|(?:im_start|im_end|endoftext)\|>", re.I)
HTML_RE = re.compile(r"<[^>]+>")
LINK_RE = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
MARKDOWN_RE = re.compile(r"(?m)^(?:#{1,6}\s*|>\s*|[-*+]\s+)|[*_~`]+")
BOUNDARY_PATTERNS = (
    re.compile(r"(?<=[.!?…])\s+|\n+"),
    re.compile(r"(?<=[;:])\s+"),
    re.compile(r"(?<=[,—])\s+"),
    re.compile(r"\s+"),
)


def preprocess(text: str, settings: dict) -> str:
    value = str(text)
    if settings.get("remove_think_blocks", True):
        value = THINK_RE.sub(" ", value)
    if settings.get("remove_code_blocks", True):
        value = CODE_RE.sub(" ", value)
    if settings.get("remove_chatml", True):
        value = CHATML_RE.sub(" ", value)
    if settings.get("remove_html", True):
        value = HTML_RE.sub(" ", html.unescape(value))
    if settings.get("remove_markdown", True):
        value = LINK_RE.sub(r"\1", value)
        value = MARKDOWN_RE.sub("", value)
    for source, replacement in (settings.get("russian_abbreviations", {}) or {}).items():
        literal = str(replacement)
        value = re.sub(re.escape(str(source)), lambda _match, replacement=literal: replacement, value, flags=re.I)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\s*\n\s*", "\n", value)
    return value.strip()


def _semantic_boundary(value: str, limit: int) -> int | None:
    minimum = max(1, limit // 2)
    for pattern in BOUNDARY_PATTERNS:
        candidates = [match.end() for match in pattern.finditer(value, 0, min(len(value), limit + 1))]
        safe = [position for position in candidates if position >= minimum]
        if safe:
            return safe[-1]
    match = re.search(r"\s+", value[limit:])
    return limit + match.end() if match else None


def split_long_text(text: str, max_chars: int = 320) -> list[str]:
    """Split long text on language-agnostic semantic boundaries without cutting words."""

    value = text.strip()
    if not value:
        return []
    if max_chars < 8:
        raise ValueError("qwentts.max_chunk_chars must be at least 8")
    chunks: list[str] = []
    remaining = value
    while len(remaining) > max_chars:
        boundary = _semantic_boundary(remaining, max_chars)
        if boundary is None:
            break
        chunk = remaining[:boundary].strip()
        if not chunk:
            break
        chunks.append(chunk)
        remaining = remaining[boundary:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks
