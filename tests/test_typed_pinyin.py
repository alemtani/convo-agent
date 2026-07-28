"""Tone errors derived from *typed* pinyin — the text-mode counterpart to `tones`.

The spoken path can only say a syllable was off (`tones.SAID_UNKNOWN`): Azure PA
reports accuracy, not a produced tone. Typing is different — when the learner
writes `ni2hao3` they have stated their belief outright, so `said` carries a real
tone and the misconception is nameable.

Alignment is against the syllable sequence of the hanzi the worker read, never a
blind parse of the typed string — which is what lets tone digits be optional per
syllable. Pure string/number work, no API: real red-green TDD per CLAUDE.md.
"""
import pytest

from backend import typed_pinyin


# --- aligning typed input against a known syllable sequence -----------------


@pytest.mark.parametrize(
    "typed,expected_syllables,expected",
    [
        ("ni3hao3", ["ni", "hao"], [3, 3]),
        ("ni3 hao3", ["ni", "hao"], [3, 3]),
        ("NI3HAO3", ["ni", "hao"], [3, 3]),          # case-folded
        ("ni3,hao3!", ["ni", "hao"], [3, 3]),        # punctuation separates
        ("xi1'an1", ["xi", "an"], [1, 1]),           # the apostrophe separator
        ("nihao", ["ni", "hao"], [None, None]),      # toneless — no signal
        ("lv4", ["lv"], [4]),                        # pypinyin's v-for-ü
        ("lü4", ["lv"], [4]),                        # typed ü folds to v
    ],
)
def test_align_reads_tone_digits(typed, expected_syllables, expected):
    assert typed_pinyin.align_typed_tones(typed, expected_syllables) == expected


def test_align_accepts_partial_marking():
    """`peng2you` is a normal way to write 朋友 — the neutral syllable stays bare.

    Knowing the expected syllables is what makes this work: `you` is matched by
    position, not by guessing where an unmarked run divides. Marking every
    syllable would be the only option if we had to segment blindly, which would
    make the feature useless for the many band-1 words ending in a neutral tone
    (朋友, 名字, 谢谢, 什么).
    """
    assert typed_pinyin.align_typed_tones("peng2you", ["peng", "you"]) == [2, None]


@pytest.mark.parametrize(
    "typed,expected_syllables",
    [
        ("sanghai", ["shang", "hai"]),   # misspelled — can't trust the alignment
        ("nihaoma", ["ni", "hao"]),      # trailing input we didn't account for
        ("ni", ["ni", "hao"]),           # ran out of input
        ("ni6hao3", ["ni", "hao"]),      # tone digit out of range
        ("lu4", ["lv"]),                 # 鹿 lu vs 绿 lv stay distinct
        ("", ["ni", "hao"]),
    ],
)
def test_align_refuses_when_input_does_not_line_up(typed, expected_syllables):
    assert typed_pinyin.align_typed_tones(typed, expected_syllables) is None


# --- the payoff: a real `said` --------------------------------------------


def test_tone_errors_names_the_misconception():
    # 你 is tone 3; the learner typed tone 2. Unlike the PA path, `said` is real.
    errors = typed_pinyin.tone_errors_from_typed("ni2hao3", "你好")
    assert [e.model_dump() for e in errors] == [
        {"syllable": "你", "expected": 3, "said": 2, "index": 0}
    ]


def test_tone_errors_empty_when_every_tone_is_right():
    assert typed_pinyin.tone_errors_from_typed("ni3hao3", "你好") == []


def test_tone_errors_flags_several_syllables():
    errors = typed_pinyin.tone_errors_from_typed("wo3 jiao4 xiao1 ming3", "我叫小明")
    assert [(e.syllable, e.expected, e.said) for e in errors] == [
        ("小", 3, 1),
        ("明", 2, 3),
    ]


def test_tone_errors_locate_a_repeated_character():
    """谢谢 repeats a character, so the position is what makes it markable.

    Both syllables read `xie`; the learner got the first right and the second
    wrong. Keyed by character alone the UI would underline whichever it found
    first — `index` is what lets it mark the one they actually missed.
    """
    errors = typed_pinyin.tone_errors_from_typed("xie4xie1", "谢谢")
    assert [(e.syllable, e.index, e.expected, e.said) for e in errors] == [
        ("谢", 1, 4, 1)
    ]


def test_tone_errors_ignores_punctuation_in_the_reading():
    # The worker's reading carries 。！ etc.; only hanzi are syllables.
    errors = typed_pinyin.tone_errors_from_typed("ni2hao3", "你好！")
    assert [e.syllable for e in errors] == ["你"]


# --- mixing marked and unmarked syllables ---------------------------------
#
# Tone digits are optional *per syllable*, so a turn can be fully marked, fully
# bare, or anything between. Every case must judge exactly the syllables the
# learner actually committed to and stay silent on the rest.


def test_partial_marking_flags_only_the_marked_syllable():
    # 朋友 is péngyou. `peng` is typed wrong; `you` carries no claim at all.
    errors = typed_pinyin.tone_errors_from_typed("peng1you", "朋友")
    assert [(e.syllable, e.index, e.expected, e.said) for e in errors] == [
        ("朋", 0, 2, 1)
    ]


def test_partial_marking_is_silent_when_the_marked_syllable_is_right():
    assert typed_pinyin.tone_errors_from_typed("peng2you", "朋友") == []


def test_unmarked_syllable_is_never_flagged_even_when_its_tone_differs():
    """An unmarked syllable is a non-statement, not a wrong answer.

    你 is tone 3 and 好 is tone 3; the learner marked 你 wrongly as 2 and left 好
    bare. Only 你 is a claim, so only 你 can be wrong.
    """
    errors = typed_pinyin.tone_errors_from_typed("ni2hao", "你好")
    assert [(e.syllable, e.index) for e in errors] == [("你", 0)]


def test_marking_only_the_later_syllable_still_aligns():
    errors = typed_pinyin.tone_errors_from_typed("nihao2", "你好")
    assert [(e.syllable, e.index, e.expected, e.said) for e in errors] == [
        ("好", 1, 3, 2)
    ]


def test_mixed_marking_across_a_longer_turn():
    # 我叫小明 — two syllables marked (one wrong), two left bare.
    errors = typed_pinyin.tone_errors_from_typed("wo3 jiao xiao1 ming", "我叫小明")
    assert [(e.syllable, e.index, e.expected, e.said) for e in errors] == [
        ("小", 2, 3, 1)
    ]


# --- degrading rather than guessing ---------------------------------------


@pytest.mark.parametrize(
    "typed,reading",
    [
        ("nihao", "你好"),        # toneless — no signal, not an error
        ("ni3hao3", "你好吗"),     # the worker read more than was typed
        ("ni3hao3ma5", "你好"),    # …and the other way round
        ("ni3hao3", ""),          # nothing read
        ("sanghai", "上海"),       # misspelled: can't trust any alignment
    ],
)
def test_tone_errors_degrade_to_empty(typed, reading):
    """Never guess. If we can't align, we report nothing."""
    assert typed_pinyin.tone_errors_from_typed(typed, reading) == []


@pytest.mark.parametrize("typed", ["xie4xie5", "xie4xie4"])
def test_neutral_tone_is_never_flagged(typed):
    """Our expected tone and the KB's curated pinyin genuinely disagree here.

    pypinyin reads 谢谢 as `xie4xie4`; the KB — and every textbook — teaches
    `xièxie`, neutral on the second. Flagging either way would tell the learner
    that what they were correctly taught is wrong, so neutral is left alone in
    both directions.
    """
    assert typed_pinyin.tone_errors_from_typed(typed, "谢谢") == []
