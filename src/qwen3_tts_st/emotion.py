from __future__ import annotations

from dataclasses import asdict, dataclass
import re


ALLOWED_STYLES = {"neutral", "soft", "whisper", "breathy", "happy", "sad", "angry", "tense"}
TAG_RE = re.compile(r"\[voice:([a-z][a-z0-9_-]*)\]", re.I)


@dataclass
class EmotionSegment:
    style: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def parse_emotion_script(text: str, default_style: str = "neutral") -> list[EmotionSegment]:
    matches = list(TAG_RE.finditer(text))
    if not matches:
        clean = text.strip()
        return [EmotionSegment(default_style, clean)] if clean else []
    segments: list[EmotionSegment] = []
    style = default_style
    cursor = 0
    for match in matches:
        preceding = text[cursor:match.start()].strip()
        if preceding:
            segments.append(EmotionSegment(style, preceding))
        requested_style = match.group(1).lower()
        style = requested_style if requested_style in ALLOWED_STYLES else "neutral"
        cursor = match.end()
    trailing = text[cursor:].strip()
    if trailing:
        segments.append(EmotionSegment(style, trailing))
    return segments


def strip_voice_tags(text: str) -> str:
    return TAG_RE.sub("", text).strip()
