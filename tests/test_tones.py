"""Tone-error builder — pure logic mapping PA accuracy onto expected/said tones."""
from backend import tones
from backend.models import PronunciationScore, SyllableScore, ToneError


def _syl(hanzi: str, accuracy: float) -> SyllableScore:
    # pinyin is display-only here; the builder derives the expected tone from hanzi.
    return SyllableScore(hanzi=hanzi, pinyin="", accuracy=accuracy)


def _score(*syllables: SyllableScore) -> PronunciationScore:
    return PronunciationScore(overall=0.0, syllables=list(syllables))


def test_below_threshold_syllable_becomes_a_tone_error():
    score = _score(_syl("你", 40.0), _syl("好", 95.0))
    # Only the under-threshold syllable is flagged; 你 is tone 3.
    assert tones.tone_errors_from_score(score, threshold=60) == [
        ToneError(syllable="你", expected=3, said=0)
    ]


def test_all_above_threshold_yields_no_errors():
    score = _score(_syl("你", 90.0), _syl("好", 95.0))
    assert tones.tone_errors_from_score(score, threshold=60) == []


def test_empty_syllables_yields_no_errors():
    assert tones.tone_errors_from_score(_score(), threshold=60) == []


def test_multi_char_grapheme_expands_to_per_character_tones():
    # Azure can score a word as one grapheme; a flagged chunk expands per char.
    score = _score(_syl("老师", 30.0))
    assert tones.tone_errors_from_score(score, threshold=60) == [
        ToneError(syllable="老", expected=3, said=0),
        ToneError(syllable="师", expected=1, said=0),
    ]


def test_neutral_tone_reported_as_five():
    score = _score(_syl("吗", 20.0))
    assert tones.tone_errors_from_score(score, threshold=60) == [
        ToneError(syllable="吗", expected=5, said=0)
    ]


def test_detected_resolver_fills_said_when_provided():
    # Branch A: a resolver supplies the produced tone instead of the 0 sentinel.
    score = _score(_syl("你", 40.0))
    errors = tones.tone_errors_from_score(
        score, threshold=60, detected=lambda hanzi: 2
    )
    assert errors == [ToneError(syllable="你", expected=3, said=2)]
