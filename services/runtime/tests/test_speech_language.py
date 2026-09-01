from chatwaifu_runtime.conversation.speech import synthesis_language_for_text


def test_japanese_kana_selects_the_japanese_tts_hint() -> None:
    assert synthesis_language_for_text("こんにちは、綾地寧々です。", "zh") == "ja"
    assert synthesis_language_for_text("ニンネンと一緒に頑張ろう。", "zh") == "ja"


def test_shared_ideographs_and_punctuation_keep_the_character_default() -> None:
    assert synthesis_language_for_text("你好，我是绫地宁宁。", "zh") == "zh"
    assert synthesis_language_for_text("東京", "zh") == "zh"
    assert synthesis_language_for_text("Qwen3-TTS · 宁宁", "zh") == "zh"
