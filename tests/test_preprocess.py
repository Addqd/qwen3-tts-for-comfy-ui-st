import pytest

from qwen3_tts_st.emotion import parse_emotion_script, strip_voice_tags
from qwen3_tts_st.preprocess import preprocess, split_language_spans, split_long_text


def test_preprocess_removes_private_blocks_and_markup():
    text = "<think>секрет</think> **Привет** — ох... <b>мир</b>"
    result = preprocess(text, {"remove_think_blocks": True, "remove_markdown": True, "remove_html": True})
    assert "секрет" not in result
    assert "Привет — ох... мир" == result


def test_direct_speech_and_emotion_tags():
    text = 'Автор сказал: [voice:happy] **"Ах, привет!"** Затем ушёл.'
    prepared = preprocess(text, {"remove_markdown": True}, "direct_speech")
    segments = parse_emotion_script(prepared)
    assert segments[0].kind == "dialogue"
    assert segments[0].style == "happy"
    assert segments[0].text == "Ах, привет!"
    assert "voice:" not in strip_voice_tags(prepared)


def test_long_split_preserves_text():
    chunks = split_long_text("Первое предложение. Второе предложение! Третье?", 25)
    assert len(chunks) >= 2
    assert "Первое предложение." in chunks[0]


def test_chunking_rejects_invalid_mode_and_too_small_semantic_limit_before_short_return():
    with pytest.raises(ValueError, match="chunking mode"):
        split_long_text("text", mode="invalid")
    with pytest.raises(ValueError, match="не меньше 8"):
        split_long_text("text", max_chars=7, mode="semantic")


def test_mixed_russian_english_spans_keep_native_words_and_punctuation():
    spans = split_language_spans("Она открыла Visual Studio Code и сказала hello.")
    assert [(span.text, span.language) for span in spans] == [
        ("Она открыла", "Russian"),
        ("Visual Studio Code", "English"),
        ("и сказала", "Russian"),
        ("hello.", "English"),
    ]


def test_semantic_chunking_never_breaks_short_russian_words():
    text = "Она пришла. Это она. Он и она пришли вместе. Она сказала, что она останется."
    chunks = split_long_text(text, 28, "semantic")
    assert len(chunks) > 1
    assert " ".join(chunks) == text
    assert all(not chunk.startswith(("на ", "а ")) for chunk in chunks)


def test_semantic_chunking_uses_the_same_safe_boundaries_for_english():
    text = "She opened Visual Studio Code. Then she said hello, and stayed for the review."
    chunks = split_long_text(text, 32, "semantic")
    assert len(chunks) > 1
    assert " ".join(chunks) == text
    assert all("Visual" not in chunk or "Studio" in chunk for chunk in chunks)
