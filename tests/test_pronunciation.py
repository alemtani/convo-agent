"""Azure Pronunciation Assessment boundary — contract tests, SDK fully mocked.

We assert the *request we build* (HundredMark grading, Phoneme granularity,
reference text = the STT transcript, and that the PA config is applied to the
recognizer) and that we *parse* a result into a `PronunciationScore`. We never
hit Azure and never assert real scores (that's the `live` smoke test).

The fakes mirror the real zh-CN SDK shape observed live: Azure leaves the
romanized `syllable` empty and reports the scored hanzi in `grapheme`, and it
does not always split a word into one syllable per character (`你好` came back as
a single grapheme unit, `老师` as two).
"""
import types

import pytest

from backend.models import PronunciationScore
from backend.speech import _azure
from backend.speech import pronunciation as pa
from tests.fakes_speech import canceled_event, make_recognizer_class, recognized_event


def _syl(grapheme, accuracy, syllable=""):
    return types.SimpleNamespace(
        syllable=syllable, grapheme=grapheme, accuracy_score=accuracy
    )


def _word(word, accuracy, syllables):
    return types.SimpleNamespace(
        word=word, accuracy_score=accuracy, syllables=syllables
    )


def _result(reason, accuracy=0.0, words=None, error_details=""):
    # `_pa` carries the data the fake PronunciationAssessmentResult reads back.
    return types.SimpleNamespace(
        reason=reason,
        error_details=error_details,
        _pa=types.SimpleNamespace(accuracy_score=accuracy, words=words or []),
    )


def _make_fake_speechsdk(recognizer_class, recorder):
    class FakeSpeechConfig:
        def __init__(self, subscription, region):
            self.subscription = subscription
            self.region = region
            self.speech_recognition_language = None
            self.properties = {}

        def set_property(self, property_id, value):
            self.properties[property_id] = value

    class FakeAudioConfig:
        def __init__(self, filename):
            self.filename = filename

    class FakePaConfig:
        def __init__(self, reference_text, grading_system, granularity):
            recorder["reference_text"] = reference_text
            recorder["grading_system"] = grading_system
            recorder["granularity"] = granularity

        def apply_to(self, recognizer):
            recorder["applied_to"] = recognizer

    class FakePaResult:
        def __init__(self, res):
            self.accuracy_score = res._pa.accuracy_score
            self.words = res._pa.words

    grading = types.SimpleNamespace(HundredMark="HundredMark")
    granularity = types.SimpleNamespace(Phoneme="Phoneme")
    reasons = types.SimpleNamespace(
        RecognizedSpeech="RecognizedSpeech", NoMatch="NoMatch", Canceled="Canceled"
    )
    def cancellation(r):  # SDK 1.42.0: constructor, not `.from_result`
        return types.SimpleNamespace(
            reason="Error", error_details=getattr(r, "error_details", "")
        )

    return types.SimpleNamespace(
        SpeechConfig=FakeSpeechConfig,
        audio=types.SimpleNamespace(AudioConfig=FakeAudioConfig),
        PronunciationAssessmentConfig=FakePaConfig,
        PronunciationAssessmentGradingSystem=grading,
        PronunciationAssessmentGranularity=granularity,
        PronunciationAssessmentResult=FakePaResult,
        SpeechRecognizer=recognizer_class,
        ResultReason=reasons,
        CancellationReason=types.SimpleNamespace(
            EndOfStream="EndOfStream", Error="Error"
        ),
        PropertyId=types.SimpleNamespace(
            Speech_SegmentationSilenceTimeoutMs="SegmentationSilenceTimeoutMs"
        ),
        CancellationDetails=cancellation,
    )


@pytest.fixture
def patched(monkeypatch):
    """One fake SDK serves both `pronunciation` (PA config + result parsing) and
    `_azure` (the shared construction and continuous loop PA delegates to).

    ``install`` takes the assessed segments the audio produces, in order — one
    per stretch of speech Azure separates, which is what a learner's pause makes.
    """
    monkeypatch.setattr(_azure.config, "AZURE_SPEECH_KEY", "test-key")
    monkeypatch.setattr(_azure.config, "AZURE_SPEECH_REGION", "test-region")

    def install(*results, canceled=None):
        events = [("recognized", recognized_event(r)) for r in results]
        if canceled is None:
            events.append(("canceled", canceled_event("EndOfStream")))
        else:
            events.append(("canceled", canceled_event("Error", result=canceled)))
        events.append(("session_stopped", None))

        recorder = {}
        fake = _make_fake_speechsdk(
            make_recognizer_class(events, recorder), recorder
        )
        monkeypatch.setattr(pa, "speechsdk", fake)
        monkeypatch.setattr(_azure, "speechsdk", fake)
        return recorder

    return install


async def test_builds_pa_request_against_the_transcript(patched):
    result = _result(
        "RecognizedSpeech",
        accuracy=80.0,
        words=[_word("老师", 76.0, [_syl("老", 97.0), _syl("师", 67.0)])],
    )
    recorder = patched(result)

    await pa.assess(b"FAKEWAV", "老师")

    # PA-specific request shape (shared construction is covered in test_azure).
    assert recorder["reference_text"] == "老师"
    assert recorder["grading_system"] == "HundredMark"
    assert recorder["granularity"] == "Phoneme"
    # The PA config must be applied to the recognizer that was built.
    assert recorder["applied_to"] is not None


async def test_parses_syllables_with_derived_pinyin(patched):
    # Mirrors the live shape: `老师` splits into two scored hanzi; pinyin is
    # derived locally (Azure leaves `syllable` empty for zh-CN).
    result = _result(
        "RecognizedSpeech",
        accuracy=80.0,
        words=[_word("老师", 76.0, [_syl("老", 97.0), _syl("师", 67.0)])],
    )
    patched(result)

    score = await pa.assess(b"FAKEWAV", "老师")

    assert isinstance(score, PronunciationScore)
    assert score.overall == 80.0
    assert [(s.hanzi, s.pinyin, s.accuracy) for s in score.syllables] == [
        ("老", "lǎo", 97.0),
        ("师", "shī", 67.0),
    ]


async def test_word_without_syllables_falls_back_to_word_grapheme(patched):
    # `你好` came back live as a single grapheme unit; with no syllable list we
    # score the whole word as one chunk.
    result = _result(
        "RecognizedSpeech",
        accuracy=88.0,
        words=[_word("你好", 94.0, [])],
    )
    patched(result)

    score = await pa.assess(b"FAKEWAV", "你好")

    assert [(s.hanzi, s.pinyin, s.accuracy) for s in score.syllables] == [
        ("你好", "nǐ hǎo", 94.0),
    ]


async def test_scores_span_speech_on_both_sides_of_a_pause(patched):
    """PA had the same `recognize_once` bug as STT, and it has to be fixed with it.

    The frontend now aligns these syllables onto the transcript and renders
    anything unscored as plain text. So if STT returns the whole sentence while
    PA still covers only the phrase before the pause, the second half is
    permanently unscored — the learner is told, silently, that half of what they
    said was not worth grading.
    """
    patched(
        _result(
            "RecognizedSpeech",
            accuracy=90.0,
            words=[_word("我想要", 90.0, [_syl("我", 90.0), _syl("想要", 90.0)])],
        ),
        _result(
            "RecognizedSpeech",
            accuracy=70.0,
            words=[_word("咖啡", 70.0, [_syl("咖", 60.0), _syl("啡", 80.0)])],
        ),
    )

    score = await pa.assess(b"FAKEWAV", "我想要咖啡")

    assert [s.hanzi for s in score.syllables] == ["我", "想要", "咖", "啡"]


async def test_overall_weighs_each_segment_by_its_length(patched):
    """One number for the whole utterance, not the last segment's number.

    Azure scores each segment on its own. Averaging them flat would let a
    one-syllable afterthought count as much as the sentence before it.
    """
    patched(
        _result(
            "RecognizedSpeech",
            accuracy=90.0,
            words=[_word("我想要", 90.0, [_syl("我", 90.0), _syl("想", 90.0), _syl("要", 90.0)])],
        ),
        _result(
            "RecognizedSpeech",
            accuracy=50.0,
            words=[_word("啊", 50.0, [_syl("啊", 50.0)])],
        ),
    )

    score = await pa.assess(b"FAKEWAV", "我想要啊")

    # (90*3 + 50*1) / 4 = 80, not the flat mean of 70.
    assert score.overall == 80.0


async def test_no_match_returns_none(patched):
    patched(_result("NoMatch"))

    assert await pa.assess(b"FAKEWAV", "你好") is None


async def test_no_segments_at_all_returns_none(patched):
    """Silence: the session opens, ends, and never recognizes anything."""
    patched()

    assert await pa.assess(b"FAKEWAV", "你好") is None


async def test_canceled_raises_pa_error(patched):
    patched(canceled=types.SimpleNamespace(error_details="bad key"))

    with pytest.raises(pa.PaError, match="bad key"):
        await pa.assess(b"FAKEWAV", "你好")


@pytest.mark.live
async def test_live_assess_returns_structural_scores():
    """Real Azure round-trip: synthesize a zh-CN sample, then assess it.

    Asserts structure only (scores in range, a hanzi per syllable) — never exact
    numbers. Needs Azure creds; excluded from the default run. `pytest -m live`.
    """
    import tempfile

    import azure.cognitiveservices.speech as speechsdk

    from backend import config

    if not (config.AZURE_SPEECH_KEY and config.AZURE_SPEECH_REGION):
        pytest.skip("Azure Speech credentials not configured")

    ref = "你好老师"
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        synth_cfg = speechsdk.SpeechConfig(
            subscription=config.AZURE_SPEECH_KEY, region=config.AZURE_SPEECH_REGION
        )
        synth_cfg.speech_synthesis_voice_name = "zh-CN-XiaoxiaoNeural"
        synth = speechsdk.SpeechSynthesizer(
            speech_config=synth_cfg,
            audio_config=speechsdk.audio.AudioOutputConfig(filename=tmp.name),
        )
        synth.speak_text_async(ref).get()
        tmp.seek(0)
        wav = tmp.read()

    score = await pa.assess(wav, ref)

    assert score is not None
    assert 0.0 <= score.overall <= 100.0
    assert score.syllables
    for syl in score.syllables:
        assert syl.hanzi
        assert 0.0 <= syl.accuracy <= 100.0


async def test_a_stalled_assessment_times_out_as_a_pa_error(monkeypatch):
    """A wedged PA call is the one most likely to go unnoticed.

    PA is the *faster* branch, so nothing else waits on it — without a deadline
    it would hold the streamed response open past a reply the learner already
    has on screen. `PaError` is what `_assess_or_degrade` already knows how to
    turn into `pronunciation: null`, so the turn completes rather than stalling.
    """
    import time

    def never_returns(audio_wav, reference_text, language):
        time.sleep(30)
        raise AssertionError("the timeout should have fired long before this")

    monkeypatch.setattr(pa, "_assess_sync", never_returns)
    monkeypatch.setattr(pa.config, "PA_TIMEOUT_S", 0.05)

    with pytest.raises(pa.PaError, match="timed out"):
        await pa.assess(b"FAKEWAV", "你好")
