"""The scored bubble must show what was recognized — all of it.

Azure Pronunciation Assessment does not hand back a tokenization of the
transcript. It merges characters into words, re-splits them, and sometimes omits
a word entirely. The bubble used to be rebuilt from that list, so anything PA
left out vanished from the screen: a learner said 你要一个苹果便宜吗 and watched
便宜 disappear the moment the scores landed.

That is worse than cosmetic. The text sent to the partner is the full
transcript, so the partner answers words the learner can no longer see — the
screen and the conversation disagree. These tests pin the invariant: the
displayed text equals the transcript, and the scores are attached to it.
"""

import json

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import TIMINGS, USAGE
from tests.smoke.test_thread import _speak, seed

pytestmark = pytest.mark.smoke


def staged(transcript, syllables, overall=88.0):
    """A canned spoken turn with a chosen transcript and PA syllable list."""
    return [
        {
            "stage": "transcript",
            "transcript": transcript,
            "timings": {**TIMINGS, "pa_ms": None, "claude_ms": None, "total_ms": None},
            "elapsed_ms": 310.0,
        },
        {
            "stage": "score",
            "pronunciation": {"overall": overall, "syllables": syllables},
            "tone_errors": [],
            "timings": {**TIMINGS, "claude_ms": None, "total_ms": None},
            "elapsed_ms": 560.0,
        },
        {
            "stage": "reply",
            "reply": {"zh": "好的。", "pinyin": "hǎo de."},
            "annotation": {"tone_errors": []},
            "timings": {**TIMINGS, "total_ms": None},
            "elapsed_ms": 1240.0,
        },
        {"stage": "done", "timings": TIMINGS, "usage": USAGE, "elapsed_ms": 1250.0},
    ]


def speak_turn(page, turn):
    """Load the page with `turn` canned on /api/turn, then say something."""
    seed(page)
    page.evaluate(
        "(t) => { window.__stub.responses['/api/turn'] = t; }", json.loads(json.dumps(turn))
    )
    _speak(page)


def user_bubble(page):
    return page.locator("#thread .bubble.user")


# The reported case: PA scored six of the eight characters and dropped 便宜.
APPLE = {"zh": "你要一个苹果便宜吗", "pinyin": "nǐ yào yí gè píng guǒ pián yi ma"}
APPLE_PA = [
    {"hanzi": h, "pinyin": p, "accuracy": a}
    for h, p, a in [
        ("你", "nǐ", 95.0),
        ("要", "yào", 90.0),
        ("一", "yī", 88.0),
        ("个", "gè", 91.0),
        ("苹", "píng", 70.0),
        ("果", "guǒ", 72.0),
        ("吗", "ma", 55.0),
    ]
]


def test_scored_bubble_keeps_words_azure_never_scored(page):
    """便宜 stays on screen even though PA never reported it."""
    speak_turn(page, staged(APPLE, APPLE_PA))
    bubble = user_bubble(page)
    expect(bubble.locator(".zh")).to_have_text("你要一个苹果便宜吗")
    for char in APPLE["zh"]:
        expect(bubble.locator(".zh")).to_contain_text(char)
    # The pinyin line is the transcript's too — the old render also swapped
    # "yí gè" for PA's "yī gè" and lost "pián yi". Scored rows are per-syllable
    # spans laid out with a flex gap, so the reading is the span list, not the
    # concatenated text.
    expect(bubble.locator(".pinyin .syl")).to_have_text(
        ["nǐ", "yào", "yí", "gè", "píng", "guǒ", "pián", "yi", "ma"]
    )


def test_scores_still_render_on_the_faithful_text(page):
    """Tone colouring is a real feature; faithfulness must not cost it."""
    speak_turn(page, staged(APPLE, APPLE_PA))
    bubble = user_bubble(page)
    expect(bubble.locator(".score-badge")).to_have_text("tone 88/100")
    # 你 (95) is good, 苹 (70) is mid, 吗 (55) is bad — one class each proves the
    # per-character scores landed on the right characters, not just somewhere.
    expect(bubble.locator(".zh .syl.tone-good")).to_have_count(4)
    expect(bubble.locator(".zh .syl.tone-mid")).to_have_count(2)
    expect(bubble.locator(".zh .syl.tone-bad")).to_have_count(1)
    # 便 and 宜 are shown, plainly — rendered but unstyled, not deleted.
    expect(bubble.locator(".zh .syl:not(.tone-good):not(.tone-mid):not(.tone-bad)")) \
        .to_have_count(2)


def test_merged_and_repeated_pa_entries_align_onto_the_transcript(page):
    """PA's real output for 你你好你好。 — a merged 你好 beside single characters.

    Observed locally: 你(28) 你(24) 好(29) 你好(63). Four entries, five hanzi, one
    of them a two-character merge. Positional pairing would misalign; the text
    must survive regardless, punctuation included.
    """
    transcript = {"zh": "你你好你好。", "pinyin": "nǐ nǐ hǎo nǐ hǎo"}
    pa = [
        {"hanzi": "你", "pinyin": "nǐ", "accuracy": 28.0},
        {"hanzi": "你", "pinyin": "nǐ", "accuracy": 24.0},
        {"hanzi": "好", "pinyin": "hǎo", "accuracy": 29.0},
        {"hanzi": "你好", "pinyin": "nǐ hǎo", "accuracy": 63.0},
    ]
    speak_turn(page, staged(transcript, pa, overall=36.0))
    bubble = user_bubble(page)
    expect(bubble.locator(".zh")).to_have_text("你你好你好。")
    # Every hanzi got a score — the three singles are bad (28/24/29), and the
    # merged 你好 (63) colours both characters it covers. The trailing 。 rides
    # along unscored, as it does everywhere else.
    expect(bubble.locator(".zh .syl.tone-bad")).to_have_count(3)
    expect(bubble.locator(".zh .syl.tone-mid")).to_have_count(2)
    expect(bubble.locator(".zh .syl:not([class*=tone-])")).to_have_count(1)
    expect(bubble.locator(".score-badge")).to_have_text("tone 36/100")


def test_pa_entries_absent_from_the_transcript_are_ignored(page):
    """A hallucinated PA token must not inject text or shift the alignment."""
    transcript = {"zh": "谢谢", "pinyin": "xiè xie"}
    pa = [
        {"hanzi": "谢", "pinyin": "xiè", "accuracy": 90.0},
        {"hanzi": "再见", "pinyin": "zài jiàn", "accuracy": 10.0},
        {"hanzi": "谢", "pinyin": "xie", "accuracy": 85.0},
    ]
    speak_turn(page, staged(transcript, pa))
    bubble = user_bubble(page)
    expect(bubble.locator(".zh")).to_have_text("谢谢")
    expect(bubble.locator(".zh .syl.tone-good")).to_have_count(2)


def test_no_syllables_leaves_the_transcript_alone(page):
    """PA returning an empty list must still leave the learner's words up."""
    speak_turn(page, staged(APPLE, []))
    bubble = user_bubble(page)
    expect(bubble.locator(".zh")).to_have_text("你要一个苹果便宜吗")
    expect(bubble.locator(".pinyin")).to_contain_text("pián yi")
