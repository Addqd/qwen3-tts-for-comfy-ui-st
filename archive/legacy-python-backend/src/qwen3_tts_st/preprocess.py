from __future__ import annotations

import html
import re
from dataclasses import dataclass

from .emotion import ALLOWED_STYLES, SOUND_TYPES


THINK_RE = re.compile(r"<(?:think|reasoning)>.*?</(?:think|reasoning)>", re.I | re.S)
CHATML_RE = re.compile(r"<\|(?:im_start|im_end|assistant|user|system)[^|]*\|>", re.I)
HTML_RE = re.compile(r"<[^>]+>")
FENCE_RE = re.compile(r"```.*?```", re.S)
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
VOICE_TAG_RE = re.compile(
    r"\[voice:(" + "|".join(map(re.escape, sorted(ALLOWED_STYLES))) + r")\]",
    re.I,
)


@dataclass(frozen=True)
class LanguageSpan:
    text: str
    language: str


CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
LATIN_RE = re.compile(r"[A-Za-z]")
BOUNDARY_PATTERNS = (
    re.compile(r"(?<=[.!?…])\s+|\n+"),
    re.compile(r"(?<=[;:])\s+"),
    re.compile(r"(?<=[,—])\s+"),
    re.compile(r"\s+"),
)


def _direct_speech(text: str) -> str:
    pattern = re.compile(
        r"(?P<sound>\[sound:(?:" + "|".join(map(re.escape, SOUND_TYPES)) + r")\])|"
        r"(?:(?P<voice>\[voice:[a-z][a-z0-9_-]*\])\s*)?"
        r'(?:"(?P<ascii>(?:\\.|[^"\\])*)"|«(?P<angle>[^»]+)»|“(?P<curly>[^”]+)”)',
        re.I | re.S,
    )
    blocks = []
    for match in pattern.finditer(text):
        if match.group("sound"):
            blocks.append(match.group("sound"))
            continue
        tag = match.group("voice")
        spoken = next(match.group(name) for name in ("ascii", "angle", "curly") if match.group(name) is not None)
        blocks.append(f"{tag + ' ' if tag else ''}\"{spoken}\"")
    return "\n".join(blocks)


def preprocess(text: str, settings: dict, mode: str | None = None) -> str:
    if not isinstance(text, str):
        raise TypeError("input должен быть строкой")
    value = text.replace("\r\n", "\n").replace("\r", "\n")
    active_mode = mode or str(settings.get("mode", "all"))
    if settings.get("remove_think_blocks", True):
        value = THINK_RE.sub(" ", value)
    if settings.get("remove_chatml", True):
        value = CHATML_RE.sub(" ", value)
    if settings.get("remove_code_blocks", True):
        value = FENCE_RE.sub(" ", value)
    if settings.get("remove_html", True):
        value = HTML_RE.sub(" ", value)
        value = html.unescape(value)
    if settings.get("remove_markdown", True):
        value = MARKDOWN_LINK_RE.sub(r"\1", value)
        value = re.sub(r"(^|\s)[#>`]+", r"\1", value)
        value = re.sub(r"(?<!\*)\*{1,3}([^*]+)\*{1,3}", r"\1", value)
        value = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", value)
    if active_mode == "direct_speech":
        extracted = _direct_speech(value)
        value = extracted if extracted.strip() else value
    abbreviations = settings.get("russian_abbreviations", {}) or {}
    for source, target in abbreviations.items():
        value = re.sub(rf"(?<!\w){re.escape(str(source))}(?!\w)", str(target), value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    if not value:
        raise ValueError("после preprocessing не осталось произносимого текста")
    return value


def _semantic_boundary(value: str, limit: int) -> int | None:
    """Choose the strongest safe boundary near the size limit."""

    minimum = max(1, limit // 2)
    for pattern in BOUNDARY_PATTERNS:
        candidates = [match.end() for match in pattern.finditer(value, 0, min(len(value), limit + 1))]
        safe = [position for position in candidates if position >= minimum]
        if safe:
            return safe[-1]
    # Prefer a nearby boundary after the limit instead of cutting a word.
    tail = value[limit:]
    match = re.search(r"\s+", tail)
    return limit + match.end() if match else None


def split_long_text(text: str, max_chars: int = 320, mode: str = "semantic") -> list[str]:
    """Split on semantic/whitespace boundaries and never inside a word."""

    if mode not in {"semantic", "off"}:
        raise ValueError(f"неизвестный chunking mode: {mode}")
    value = text.strip()
    if mode == "off":
        return [value] if value else []
    if max_chars < 8:
        raise ValueError("chunking.max_chars должен быть не меньше 8")
    if not value:
        return []
    if len(value) <= max_chars:
        return [value]

    chunks: list[str] = []
    remaining = value
    while len(remaining) > max_chars:
        boundary = _semantic_boundary(remaining, max_chars)
        if boundary is None:
            # A single unusually long token is safer whole than artificially cut.
            break
        chunk = remaining[:boundary].strip()
        if not chunk:
            break
        chunks.append(chunk)
        remaining = remaining[boundary:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def split_language_spans(text: str, mode: str = "auto") -> list[LanguageSpan]:
    """Split mixed Cyrillic/Latin text while retaining punctuation and word integrity."""

    value = text.strip()
    if not value:
        return []
    if mode == "off":
        return [LanguageSpan(value, "Russian")]
    if mode != "auto":
        raise ValueError(f"неизвестный multilingual mode: {mode}")

    spans: list[LanguageSpan] = []
    buffer = ""
    active_language: str | None = None
    pending_neutral = ""

    def flush() -> None:
        nonlocal buffer
        clean = buffer.strip()
        if clean and active_language:
            spans.append(LanguageSpan(clean, active_language))
        buffer = ""

    for character in value:
        language = "Russian" if CYRILLIC_RE.match(character) else "English" if LATIN_RE.match(character) else None
        if language is None:
            pending_neutral += character
            continue
        if active_language is None:
            active_language = language
            buffer = pending_neutral + character
            pending_neutral = ""
            continue
        if language == active_language:
            buffer += pending_neutral + character
            pending_neutral = ""
            continue
        buffer += pending_neutral
        pending_neutral = ""
        flush()
        active_language = language
        buffer = character

    buffer += pending_neutral
    flush()
    return spans or [LanguageSpan(value, "Russian")]
