from __future__ import annotations

import html
import re


THINK_RE = re.compile(r"<(?:think|reasoning)>.*?</(?:think|reasoning)>", re.I | re.S)
CHATML_RE = re.compile(r"<\|(?:im_start|im_end|assistant|user|system)[^|]*\|>", re.I)
HTML_RE = re.compile(r"<[^>]+>")
FENCE_RE = re.compile(r"```.*?```", re.S)
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
VOICE_TAG_RE = re.compile(r"\[voice:(neutral|soft|whisper|breathy|happy|sad|angry|tense)\]", re.I)


def _direct_speech(text: str) -> str:
    groups = re.findall(r"«([^»]+)»|“([^”]+)”|\"([^\"]+)\"", text, flags=re.S)
    return "\n".join(next(value for value in group if value) for group in groups)


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
    if active_mode == "direct_speech":
        extracted = _direct_speech(value)
        value = extracted if extracted.strip() else value
    if settings.get("remove_markdown", True):
        value = MARKDOWN_LINK_RE.sub(r"\1", value)
        value = re.sub(r"(^|\s)[#>`]+", r"\1", value)
        value = re.sub(r"(?<!\*)\*{1,3}([^*]+)\*{1,3}", r"\1", value)
        value = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", value)
    abbreviations = settings.get("russian_abbreviations", {}) or {}
    for source, target in abbreviations.items():
        value = re.sub(rf"(?<!\w){re.escape(str(source))}(?!\w)", str(target), value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    if not value:
        raise ValueError("после preprocessing не осталось произносимого текста")
    return value


def split_long_text(text: str, max_chars: int = 320) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[.!?…])\s+|\n+", text)
    chunks: list[str] = []
    current = ""
    for sentence in filter(None, (part.strip() for part in sentences)):
        if len(sentence) > max_chars:
            pieces = re.split(r"(?<=[,;:—])\s+", sentence)
        else:
            pieces = [sentence]
        for piece in pieces:
            candidate = f"{current} {piece}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks

