"""A fake Azure `SpeechRecognizer` that does continuous recognition.

The three speech boundary tests (`test_azure`, `test_stt`, `test_pronunciation`)
each build their own `speechsdk` stand-in, but they all need the same recognizer
shape, so it lives here once.

The fake mirrors the real SDK's *asynchronous* contract, which is the part the
fix has to get right: `start_continuous_recognition` returns immediately and the
events arrive later on another thread. Firing them inline would let a broken
implementation — one that reads its results before the session has stopped —
pass. It stays deterministic all the same: nothing waits on a clock, the fake
only hands the events to a thread and the code under test blocks on the session
event until they have all been delivered.
"""
import threading
import types


class FakeSignal:
    """Stand-in for an SDK event signal: `connect` a handler, `fire` an event."""

    def __init__(self):
        self.handlers = []

    def connect(self, handler):
        self.handlers.append(handler)

    def fire(self, event):
        for handler in list(self.handlers):
            handler(event)


def recognized_event(result):
    return types.SimpleNamespace(result=result)


def canceled_event(reason, result=None):
    return types.SimpleNamespace(
        reason=reason,
        result=result,
        cancellation_details=types.SimpleNamespace(reason=reason),
    )


def make_recognizer_class(events, recorder, *, silent=False, observer=None):
    """Build a fake recognizer class that replays ``events`` when started.

    ``events`` is a list of ``(signal_name, event)`` pairs delivered in order on
    a worker thread — the SDK's own callback thread, in effect. ``silent=True``
    starts a session that never emits anything, which is how a hang is tested.
    ``observer`` is called once, on the event thread, before the events go out;
    tests use it to check what is still true *while* recognition is running.
    """

    class FakeRecognizer:
        def __init__(self, speech_config, audio_config):
            recorder["speech_config"] = speech_config
            recorder["audio_config"] = audio_config
            recorder["stopped"] = False
            self.recognized = FakeSignal()
            self.canceled = FakeSignal()
            self.session_stopped = FakeSignal()
            self._thread = None

        def recognize_once(self):
            raise AssertionError(
                "recognize_once stops at the first pause — use continuous "
                "recognition so a learner who thinks mid-sentence is still heard"
            )

        def start_continuous_recognition(self):
            recorder["started"] = True
            if silent:
                return
            self._thread = threading.Thread(target=self._replay, daemon=True)
            self._thread.start()

        def _replay(self):
            if observer is not None:
                observer(recorder)
            for name, event in events:
                getattr(self, name).fire(event)

        def stop_continuous_recognition(self):
            recorder["stopped"] = True

    return FakeRecognizer
