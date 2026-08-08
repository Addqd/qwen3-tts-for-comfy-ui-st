from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Iterator, Literal, get_args


VoiceStyle = Literal[
    "neutral",
    "soft",
    "whisper",
    "breathy",
    "happy",
    "sad",
    "angry",
    "tense",
    "pleasure",
    "intimate",
]
ALLOWED_STYLES = frozenset(get_args(VoiceStyle))

# The machine contract is intentionally strict: [voice:style]. A broader
# service-tag matcher removes malformed variants as well, so they can never be
# forwarded to TTS as spoken text.
TAG_RE = re.compile(r"\[voice:([a-z][a-z0-9_-]*)\]", re.I)
SERVICE_TAG_RE = re.compile(
    r"\[\s*voice(?=\s|:|\])(?:\s*:\s*|\s+)?([^\]\r\n]*)\]",
    re.I,
)
UNTERMINATED_TAG_RE = re.compile(
    r"\[\s*voice(?=\s|:)(?:\s*:\s*|\s+)[a-z0-9_-]*",
    re.I,
)


@dataclass
class EmotionSegment:
    style: str
    text: str
    kind: str = "narration"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str = ""
    style: str = "neutral"
    warnings: tuple[str, ...] = ()


def _is_escaped(text: str, position: int) -> bool:
    backslashes = 0
    position -= 1
    while position >= 0 and text[position] == "\\":
        backslashes += 1
        position -= 1
    return bool(backslashes % 2)


def _closing_quote(text: str, start: int) -> int | None:
    for position in range(start + 1, len(text)):
        if text[position] == '"' and not _is_escaped(text, position):
            return position
    return None


def _tag_token(text: str, position: int) -> tuple[int, _Token] | None:
    strict = TAG_RE.match(text, position)
    if strict:
        requested = strict.group(1).lower()
        if requested in ALLOWED_STYLES:
            return strict.end(), _Token("tag", style=requested)
        return strict.end(), _Token(
            "tag",
            style="neutral",
            warnings=("unknown_voice_tag_neutral_fallback",),
        )

    malformed = SERVICE_TAG_RE.match(text, position)
    if malformed:
        return malformed.end(), _Token(
            "tag",
            style="neutral",
            warnings=("malformed_voice_tag_neutral_fallback",),
        )

    unterminated = UNTERMINATED_TAG_RE.match(text, position)
    if unterminated:
        return unterminated.end(), _Token(
            "tag",
            style="neutral",
            warnings=("unterminated_voice_tag_removed",),
        )
    return None


def _tokens(text: str) -> Iterator[_Token]:
    cursor = 0
    position = 0
    while position < len(text):
        if text[position] == "[":
            tag = _tag_token(text, position)
            if tag:
                if cursor < position:
                    yield _Token("text", text[cursor:position])
                position, token = tag
                yield token
                cursor = position
                continue

        if text[position] == '"' and not _is_escaped(text, position):
            closing = _closing_quote(text, position)
            if cursor < position:
                yield _Token("text", text[cursor:position])
            if closing is None:
                # An unclosed outer quote is not a valid dialogue delimiter.
                # Keep the remaining text audible as neutral narration while
                # dropping only the delimiter itself.
                yield _Token(
                    "text",
                    text[position + 1 :],
                    warnings=("unclosed_dialogue_treated_as_neutral",),
                )
                return
            yield _Token("dialogue", text[position + 1 : closing])
            position = closing + 1
            cursor = position
            continue
        position += 1

    if cursor < len(text):
        yield _Token("text", text[cursor:])


def strip_voice_tags(text: str) -> str:
    """Remove recognized, unknown, malformed, and unterminated voice tags."""

    value = SERVICE_TAG_RE.sub("", text)
    value = UNTERMINATED_TAG_RE.sub("", value)
    return value.strip()


def _clean_spoken_text(text: str) -> str:
    return strip_voice_tags(text).replace('\\"', '"').strip()


def _append_segment(segments: list[EmotionSegment], style: str, text: str, kind: str) -> None:
    clean = _clean_spoken_text(text)
    if not clean:
        return
    if kind == "narration" and segments and segments[-1].style == style and segments[-1].kind == kind:
        segments[-1].text = f"{segments[-1].text} {clean}".strip()
        return
    segments.append(EmotionSegment(style=style, text=clean, kind=kind))


def parse_emotion_script_detailed(text: str) -> tuple[list[EmotionSegment], list[str]]:
    """Parse quote-scoped delivery tags without leaking service text.

    A valid tag applies only to the immediately following complete dialogue
    block delimited by ordinary ASCII double quotes. Narration and untagged
    dialogue are always neutral. Closing a dialogue automatically clears the
    pending tag.
    """

    if not isinstance(text, str):
        raise TypeError("emotion script должен быть строкой")

    segments: list[EmotionSegment] = []
    warnings: list[str] = []
    pending_style: str | None = None

    for token in _tokens(text):
        warnings.extend(token.warnings)
        if token.kind == "tag":
            if pending_style is not None:
                warnings.append("multiple_voice_tags_last_wins")
            pending_style = token.style
            continue

        if token.kind == "dialogue":
            style = pending_style or "neutral"
            clean = _clean_spoken_text(token.text)
            if clean:
                _append_segment(segments, style, clean, "dialogue")
            else:
                warnings.append("empty_dialogue_ignored")
            pending_style = None
            continue

        if token.text.strip():
            if pending_style is not None:
                warnings.append("voice_tag_ignored_no_following_quoted_dialogue")
                pending_style = None
            _append_segment(segments, "neutral", token.text, "narration")

    if pending_style is not None:
        warnings.append("voice_tag_ignored_no_following_quoted_dialogue")

    # Stable ordering without repeated log noise; warnings contain no user text.
    warnings = list(dict.fromkeys(warnings))
    return segments, warnings


def parse_emotion_script(text: str, default_style: str = "neutral") -> list[EmotionSegment]:
    # default_style remains in the signature for source compatibility. The new
    # contract deliberately makes all untagged content neutral.
    del default_style
    return parse_emotion_script_detailed(text)[0]
