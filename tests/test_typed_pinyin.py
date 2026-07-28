"""Tone errors derived from *typed* pinyin — the text-mode counterpart to `tones`.

The spoken path can only say a syllable was off (`tones.SAID_UNKNOWN`): Azure PA
reports accuracy, not a produced tone. Typing is different — when the learner
writes `ni2hao3` they have stated their belief outright, so `said` carries a real
tone and the misconception is nameable.

Pure string/number work, no API: real red-green TDD per CLAUDE.md.
"""
import pytest

from backend import typed_pinyin


# --- splitting typed input into syllables ---------------------------------
#
# Tone digits terminate a syllable, which is what makes segmentation tractable:
# "ni3hao3" needs no pinyin dictionary to split, only the digits.


@pytest.mark.parametrize(
    "typed,expected",
    [
        ("ni3hao3", ["ni3", "hao3"]),
        ("ni3 hao3", ["ni3", "hao3"]),
        ("wo3 jiao4 xiao3 ming2", ["wo3", "jiao4", "xiao3", "ming2"]),
        ("NI3HAO3", ["ni3", "hao3"]),          # case-folded
        ("ni3,hao3!", ["ni3", "hao3"]),        # punctuation is not a syllable
        ("xi1'an1", ["xi1", "an1"]),           # the apostrophe separator
        ("nihao", ["nihao"]),                  # no digits — one unsplit run
        ("", []),
    ],
)
def test_split_typed_syllables(typed, expected):
    assert typed_pinyin.split_typed_syllables(typed) == expected


# --- reading the typed tones ----------------------------------------------


def test_typed_tones_reads_every_digit():
    assert typed_pinyin.typed_tones("wo3 jiao4 xiao3 ming2") == [3, 4, 3, 2]


def test_typed_tones_accepts_neutral_five():
    assert typed_pinyin.typed_tones("ma5") == [5]


@pytest.mark.parametrize("typed", ["nihao", "", "ni3hao", "ni hao3"])
def test_typed_tones_is_none_unless_fully_marked(typed):
    """Partial marking is refused, not guessed.

    A toneless run can't be segmented without a pinyin dictionary ("haoma" is
    one syllable or two?), so a partly-marked string has an unknown syllable
    count and any alignment would be a guess. Since tone digits are optional by
    design, the honest answer is "no tone signal" — better than inventing an
    error and teaching a beginner the wrong thing.
    """
    assert typed_pinyin.typed_tones(typed) is None


@pytest.mark.parametrize("typed", ["ni6", "ni0"])
def test_typed_tones_rejects_out_of_range_digits(typed):
    assert typed_pinyin.typed_tones(typed) is None


# --- the payoff: a real `said` --------------------------------------------


def test_tone_errors_names_the_misconception():
    # 你 is tone 3; the learner typed tone 2. Unlike the PA path, `said` is real.
    errors = typed_pinyin.tone_errors_from_typed("ni2hao3", "你好")
    assert [e.model_dump() for e in errors] == [
        {"syllable": "你", "expected": 3, "said": 2}
    ]


def test_tone_errors_empty_when_every_tone_is_right():
    assert typed_pinyin.tone_errors_from_typed("ni3hao3", "你好") == []


def test_tone_errors_flags_several_syllables():
    errors = typed_pinyin.tone_errors_from_typed("wo3 jiao4 xiao1 ming3", "我叫小明")
    assert [(e.syllable, e.expected, e.said) for e in errors] == [
        ("小", 3, 1),
        ("明", 2, 3),
    ]


def test_tone_errors_ignores_punctuation_in_the_reading():
    # The worker's reading carries 。！ etc.; only hanzi are syllables.
    errors = typed_pinyin.tone_errors_from_typed("ni2hao3", "你好！")
    assert [e.syllable for e in errors] == ["你"]


@pytest.mark.parametrize(
    "typed,reading",
    [
        ("nihao", "你好"),        # toneless — no signal, not an error
        ("ni3hao3", "你好吗"),     # count mismatch: the worker read more than typed
        ("ni3hao3ma5", "你好"),    # count mismatch the other way
        ("ni3hao3", ""),          # nothing read
    ],
)
def test_tone_errors_degrade_to_empty(typed, reading):
    """Never guess. A mismatch means we can't align, so we report nothing."""
    assert typed_pinyin.tone_errors_from_typed(typed, reading) == []
