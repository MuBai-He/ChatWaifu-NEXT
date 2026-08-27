"""Streaming text segmentation regression tests."""

import pytest
from chatwaifu_runtime.conversation.text_segmenter import StreamingTextSegmenter


def test_multiple_sentences_inside_one_provider_delta_are_emitted_individually() -> None:
    segmenter = StreamingTextSegmenter()

    ready = segmenter.feed(
        "早晨适合拉开窗帘呼吸一下新鲜空气。"
        "午后可以泡杯茶，慢慢整理手边的事情。"
        "夜晚就让自己安静地休息吧。"
    )

    assert ready == (
        "早晨适合拉开窗帘呼吸一下新鲜空气。",
        "午后可以泡杯茶，慢慢整理手边的事情。",
        "夜晚就让自己安静地休息吧。",
    )
    assert segmenter.flush() == ()


def test_short_acknowledgement_waits_for_enough_speech() -> None:
    segmenter = StreamingTextSegmenter()

    assert segmenter.feed("嗯。") == ()
    assert segmenter.feed("好呀。") == ("嗯。好呀。",)


def test_decimal_url_and_english_abbreviation_are_not_false_boundaries() -> None:
    segmenter = StreamingTextSegmenter()

    assert segmenter.feed("版本是3.") == ()
    assert segmenter.feed("14，请打开 https://example.com/docs。") == (
        "版本是3.14，请打开 https://example.com/docs。",
    )
    assert segmenter.feed("Dr. Nene will check it. ") == ("Dr. Nene will check it.",)
    assert segmenter.feed("Thank you!") == ("Thank you!",)


def test_japanese_quotes_and_ellipsis_stay_with_the_spoken_sentence() -> None:
    segmenter = StreamingTextSegmenter()

    assert segmenter.feed("「そうですね……」次はどうしますか？") == (
        "「そうですね……」",
        "次はどうしますか？",
    )


def test_unpunctuated_text_uses_soft_breaks_and_a_hard_upper_bound() -> None:
    segmenter = StreamingTextSegmenter(min_characters=4, max_characters=12)

    ready = segmenter.feed("前半内容很自然，后半仍然继续而且没有句号还在延伸")

    assert ready == (
        "前半内容很自然，",
        "后半仍然继续而且没有句号",
    )
    assert segmenter.flush() == ("还在延伸",)
    assert all(len(item) <= 12 for item in ready)


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(0, 90), (10, 9)],
)
def test_invalid_segment_limits_are_rejected(minimum: int, maximum: int) -> None:
    with pytest.raises(ValueError):
        StreamingTextSegmenter(min_characters=minimum, max_characters=maximum)
