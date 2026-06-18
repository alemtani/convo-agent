"""Pinyin romanization helper — pure deterministic logic, no I/O."""
from backend.pinyin import to_pinyin, tone_numbers


def test_basic_greeting():
    assert to_pinyin("你好") == "nǐ hǎo"


def test_drops_punctuation_between_syllables():
    assert to_pinyin("你好，老师。") == "nǐ hǎo lǎo shī"


def test_empty_input_returns_empty():
    assert to_pinyin("") == ""


def test_tone_numbers_per_syllable():
    # 你好老师吗 -> 3 3 3 1, with the neutral 吗 reported as 5.
    assert tone_numbers("你好老师吗") == [3, 3, 3, 1, 5]


def test_tone_numbers_ignores_punctuation():
    assert tone_numbers("你好，老师。") == [3, 3, 3, 1]


def test_tone_numbers_empty_input():
    assert tone_numbers("") == []
