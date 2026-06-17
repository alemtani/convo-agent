"""Pinyin romanization helper — pure deterministic logic, no I/O."""
from backend.pinyin import to_pinyin


def test_basic_greeting():
    assert to_pinyin("你好") == "nǐ hǎo"


def test_drops_punctuation_between_syllables():
    assert to_pinyin("你好，老师。") == "nǐ hǎo lǎo shī"


def test_empty_input_returns_empty():
    assert to_pinyin("") == ""
