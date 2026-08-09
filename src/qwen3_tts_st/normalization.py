from __future__ import annotations

import json
import re
from typing import Any, Mapping


_ONES = ("ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять")
_ONES_FEM = ("ноль", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять")
_TEENS = ("десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать")
_TENS = ("", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто")
_HUNDREDS = ("", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот")


def _under_thousand(value: int, feminine: bool = False) -> str:
    parts: list[str] = []
    hundreds, rest = divmod(value, 100)
    if hundreds:
        parts.append(_HUNDREDS[hundreds])
    if 10 <= rest <= 19:
        parts.append(_TEENS[rest - 10])
    else:
        tens, ones = divmod(rest, 10)
        if tens:
            parts.append(_TENS[tens])
        if ones:
            parts.append((_ONES_FEM if feminine else _ONES)[ones])
    return " ".join(parts)


def _plural(value: int, forms: tuple[str, str, str]) -> str:
    last_two = value % 100
    if 11 <= last_two <= 14:
        return forms[2]
    last = value % 10
    if last == 1:
        return forms[0]
    if 2 <= last <= 4:
        return forms[1]
    return forms[2]


def integer_to_words(value: int) -> str:
    if abs(value) > 999_999:
        return str(value)
    if value == 0:
        return _ONES[0]
    prefix = "минус " if value < 0 else ""
    value = abs(value)
    thousands, rest = divmod(value, 1000)
    parts: list[str] = []
    if thousands:
        parts.append(_under_thousand(thousands, feminine=True))
        parts.append(_plural(thousands, ("тысяча", "тысячи", "тысяч")))
    if rest:
        parts.append(_under_thousand(rest))
    return prefix + " ".join(parts)


def _case_replacement(match: re.Match[str], replacement: str) -> str:
    source = match.group(0)
    if source.isupper():
        return replacement.upper()
    if source[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _replace_words(text: str, replacements: Mapping[str, str]) -> tuple[str, int]:
    value = text
    count = 0
    for source, target in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        left = r"(?<!\w)" if source[:1].isalnum() else ""
        right = r"(?!\w)" if source[-1:].isalnum() else ""
        pattern = re.compile(left + re.escape(source) + right, re.IGNORECASE | re.UNICODE)
        value, replaced = pattern.subn(lambda match: _case_replacement(match, target), value)
        count += replaced
    return value, count


def _normalize_basic(text: str, yo_dictionary: Mapping[str, str]) -> str:
    value = text.replace("\u00a0", " ").replace("\u202f", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = value.strip()
    value, _ = _replace_words(value, yo_dictionary)
    return value


def _normalize_full(text: str) -> str:
    def time_value(match: re.Match[str]) -> str:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        return f"{integer_to_words(hours)} {_plural(hours, ('час', 'часа', 'часов'))} {integer_to_words(minutes)} {_plural(minutes, ('минута', 'минуты', 'минут'))}"

    def percent_value(match: re.Match[str]) -> str:
        number = int(match.group(1))
        return f"{integer_to_words(number)} {_plural(abs(number), ('процент', 'процента', 'процентов'))}"

    def decimal_value(match: re.Match[str]) -> str:
        whole = int(match.group(1))
        fraction_text = match.group(2)
        fraction = int(fraction_text)
        denominator = ("десятая", "десятых") if len(fraction_text) == 1 else ("сотая", "сотых")
        whole_form = _plural(abs(whole), ("целая", "целых", "целых"))
        fraction_form = denominator[0] if fraction % 10 == 1 and fraction % 100 != 11 else denominator[1]
        return f"{integer_to_words(whole)} {whole_form} {integer_to_words(fraction)} {fraction_form}"

    value = re.sub(r"(?<!\d)([0-2]?\d):([0-5]\d)(?!\d)", time_value, text)
    value = re.sub(r"(?<![\w.,])(-?\d{1,6})\s*%(?!\w)", percent_value, value)
    value = re.sub(r"(?<![\w.,])(-?\d{1,6})[.,](\d{1,2})(?!\d)", decimal_value, value)
    value = re.sub(r"(?<![\w.,:])-?\d{1,6}(?![\w.,:])", lambda match: integer_to_words(int(match.group(0))), value)
    return value


def normalize_russian_text(text: str, mode: str, yo_dictionary: Mapping[str, str] | None = None) -> str:
    normalized_mode = mode.lower()
    if normalized_mode not in {"off", "basic", "full"}:
        raise ValueError(f"неизвестный режим Russian normalization: {mode}")
    if normalized_mode == "off":
        return text
    value = _normalize_basic(text, yo_dictionary or {})
    return _normalize_full(value) if normalized_mode == "full" else value


def parse_pronunciation_overrides(value: Any) -> dict[str, str]:
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return {}
        if stripped.startswith("{"):
            try:
                value = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"pronunciation_overrides содержит некорректный JSON: {exc.msg}") from exc
        else:
            parsed: dict[str, str] = {}
            for number, line in enumerate(stripped.splitlines(), start=1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    raise ValueError(f"pronunciation_overrides: строка {number} должна иметь вид source = replacement")
                source, replacement = (part.strip() for part in line.split("=", 1))
                parsed[source] = replacement
            value = parsed
    if not isinstance(value, dict):
        raise ValueError("pronunciation_overrides должен быть JSON object или строками source = replacement")
    if len(value) > 200:
        raise ValueError("pronunciation_overrides содержит больше 200 замен")
    result: dict[str, str] = {}
    for source, replacement in value.items():
        if not isinstance(source, str) or not isinstance(replacement, str):
            raise ValueError("pronunciation_overrides принимает только строковые source/replacement")
        source = source.strip()
        replacement = replacement.strip()
        if not source or not replacement:
            raise ValueError("pronunciation_overrides не допускает пустые source/replacement")
        if len(source) > 200 or len(replacement) > 500 or any(ord(char) < 32 for char in source + replacement):
            raise ValueError("pronunciation_overrides содержит слишком длинное значение или control character")
        result[source] = replacement
    return result


def merge_pronunciation_dictionaries(global_dictionary: Any, overrides: Any) -> dict[str, str]:
    base = parse_pronunciation_overrides(global_dictionary)
    extra = parse_pronunciation_overrides(overrides)
    merged = {key.casefold(): (key, value) for key, value in base.items()}
    for key, value in extra.items():
        merged[key.casefold()] = (key, value)
    return {source: replacement for source, replacement in merged.values()}


def apply_pronunciation(text: str, dictionary: Mapping[str, str]) -> tuple[str, int]:
    return _replace_words(text, dictionary)
