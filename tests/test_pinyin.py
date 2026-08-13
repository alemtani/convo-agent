"""Pinyin romanization helper — pure deterministic logic, no I/O."""
from backend import pinyin
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


# --- M2-D: annotating prose that quotes 汉字 ------------------------------


def test_annotate_adds_pinyin_after_each_run_of_hanzi():
    """The verdict card quotes the learner's own phrases back at them.

    Bare characters in an English sentence are unreadable to a band-1 learner —
    which is exactly who the card is for — so the romanization is added here,
    deterministically, rather than asked of the model.
    """
    out = pinyin.annotate_hanzi("You introduced yourself with 我叫小明, nicely done.")
    assert out == "You introduced yourself with 我叫小明 (wǒ jiào xiǎo míng), nicely done."


def test_annotate_handles_several_runs():
    out = pinyin.annotate_hanzi("You said 你好 and then 谢谢.")
    assert "你好 (nǐ hǎo)" in out
    assert "谢谢 (xiè xiè)" in out


def test_annotate_leaves_prose_without_hanzi_alone():
    text = "You never asked what it cost."
    assert pinyin.annotate_hanzi(text) == text


def test_annotate_does_not_double_up_on_an_existing_parenthetical():
    """The model sometimes glosses a phrase itself; two readings is worse than
    one, and the one it wrote is in context."""
    text = "In 最近 (zuìjìn), the second syllable falls."
    assert pinyin.annotate_hanzi(text) == text


def test_annotate_is_safe_on_empty_input():
    assert pinyin.annotate_hanzi("") == ""
