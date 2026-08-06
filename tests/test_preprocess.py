from qwen3_tts_st.emotion import parse_emotion_script, strip_voice_tags
from qwen3_tts_st.preprocess import preprocess, split_long_text


def test_preprocess_removes_private_blocks_and_markup():
    text = "<think>секрет</think> **Привет** — ох... <b>мир</b>"
    result = preprocess(text, {"remove_think_blocks": True, "remove_markdown": True, "remove_html": True})
    assert "секрет" not in result
    assert "Привет — ох... мир" == result


def test_direct_speech_and_emotion_tags():
    text = "Автор сказал: «[voice:happy] Ах, привет!» Затем ушёл."
    prepared = preprocess(text, {"remove_markdown": True}, "direct_speech")
    segments = parse_emotion_script(prepared)
    assert segments[0].style == "happy"
    assert segments[0].text == "Ах, привет!"
    assert "voice:" not in strip_voice_tags(prepared)


def test_long_split_preserves_text():
    chunks = split_long_text("Первое предложение. Второе предложение! Третье?", 25)
    assert len(chunks) >= 2
    assert "Первое предложение." in chunks[0]

