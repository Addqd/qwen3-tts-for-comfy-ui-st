from __future__ import annotations

import html
import re


THINK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.I | re.S)
CODE_RE = re.compile(r"```.*?```", re.S)
CHATML_RE = re.compile(r"<\|(?:im_start|im_end|endoftext)\|>", re.I)
HTML_RE = re.compile(r"<[^>]+>")
LINK_RE = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
MARKDOWN_RE = re.compile(r"(?m)^(?:#{1,6}\s*|>\s*|[-*+]\s+)|[*_~`]+")


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
