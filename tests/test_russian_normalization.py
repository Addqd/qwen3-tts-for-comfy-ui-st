from __future__ import annotations

import pytest

from qwen3_tts_st.config import load_config
from qwen3_tts_st.normalization import (
    apply_pronunciation,
    merge_pronunciation_dictionaries,
    normalize_russian_text,
    parse_pronunciation_overrides,
)


def test_off_is_exact_pass_through():
    text = "  В 12:30 — ещё 25%...  "
    assert normalize_russian_text(text, "off", {"ещё": "ещё"}) == text


def test_basic_cleans_spacing_and_uses_only_curated_yo_words():
    dictionary = {"еще": "ещё", "ее": "её", "елка": "ёлка", "ежик": "ёжик"}
    result = normalize_russian_text(" Все люди  , еще ее елка и ежик. Всё хорошо. ", "basic", dictionary)
    assert result == "Все люди, ещё её ёлка и ёжик. Всё хорошо."


def test_configured_yo_dictionary_is_utf8_and_does_not_change_vse_semantics():
    dictionary = load_config().get("normalization.yo_dictionary")
    assert dictionary == {"еще": "ещё", "ее": "её", "елка": "ёлка", "ежик": "ёжик"}
    assert normalize_russian_text("все люди; всё хорошо", "basic", dictionary) == "все люди; всё хорошо"


def test_full_expands_bounded_numbers_times_decimals_and_percentages():
    result = normalize_russian_text("В 12:30 осталось 25% и 3,14.", "full")
    assert "двенадцать часов тридцать минут" in result
    assert "двадцать пять процентов" in result
    assert "три целых четырнадцать сотых" in result


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("1.1", "одна целая одна десятая"),
        ("2.01", "две целых одна сотая"),
        ("21.2", "двадцать одна целая две десятых"),
        ("22.22", "двадцать две целых двадцать две сотых"),
        ("31.01", "тридцать одна целая одна сотая"),
        ("12:01", "двенадцать часов одна минута"),
        ("12:02", "двенадцать часов две минуты"),
    ],
)
def test_full_uses_feminine_number_forms_for_decimals_and_minutes(source, expected):
    assert normalize_russian_text(source, "full") == expected


def test_request_pronunciation_overrides_global_case_insensitively():
    merged = merge_pronunciation_dictionaries(
        {"Qwen": "кью вэн", "API": "эй пи ай"},
        "qwen = куэн\nComfyUI = комфи ю ай",
    )
    result, count = apply_pronunciation("Qwen и API в ComfyUI", merged)
    assert result == "Куэн и ЭЙ ПИ АЙ в Комфи ю ай"
    assert count == 3


def test_pronunciation_parser_rejects_malformed_or_non_string_values():
    with pytest.raises(ValueError, match="source = replacement"):
        parse_pronunciation_overrides("broken line")
    with pytest.raises(ValueError, match="строковые"):
        parse_pronunciation_overrides({"ok": 12})
