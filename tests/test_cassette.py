"""The eval cassette layer: record once, replay for free, miss loudly.

Three tiers, no tokens spent:
- **The key (pure).** `sha256(model + system + tools + messages + params)`. What
  belongs in it, what must not, and that it is stable across dict ordering.
- **The store (local I/O).** A cassette round-trips through a temp directory and
  lands on disk as a diffable file.
- **The client (contract).** A drop-in for `AsyncAnthropic` at the one seam the
  workers use: `client.messages.parse(**request, timeout=...)`. A hit replays a
  sample; a miss without `--record` raises and never reaches the network.

The last tier runs the *real* conversation worker through the fake client, not
a stubbed `build_request` — a cassette that keys on a request nobody sends is a
cassette that misses forever.
"""
import argparse
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from backend import config, kb
from backend.models import ConversationResult, GraderResult, SpokenConversationResult
from backend.workers import conversation, grader
from evals import cassette


class _Other(BaseModel):
    """A second output schema, to prove the schema is part of the key."""

    value: str


def _request(**overrides):
    """The kwargs a worker hands to `messages.parse`, in miniature."""
    request = {
        "model": "claude-sonnet-5",
        "system": [{"type": "text", "text": "be a partner"}],
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": 1024,
        "output_format": _Other,
    }
    request.update(overrides)
    return request


def _sample(value="one", **overrides):
    sample = {
        "stop_reason": "end_turn",
        "parsed_output": {"value": value},
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }
    sample.update(overrides)
    return sample


# --- The key -------------------------------------------------------------


def test_key_is_a_sha256_hex_digest():
    key = cassette.request_key(_request())
    assert len(key) == 64
    assert set(key) <= set("0123456789abcdef")


def test_same_request_same_key_whatever_the_dict_order():
    a = cassette.request_key(_request())
    b = cassette.request_key({k: v for k, v in reversed(list(_request().items()))})
    assert a == b


@pytest.mark.parametrize(
    "overrides",
    [
        {"model": "claude-haiku-4-5-20251001"},
        {"system": [{"type": "text", "text": "be someone else"}]},
        {"messages": [{"role": "user", "content": "再见"}]},
        {"max_tokens": 2048},
        {"output_config": {"effort": "low"}},
        {"thinking": {"type": "disabled"}},
        {"tools": [{"name": "lookup"}]},
        {"output_format": ConversationResult},
        {"extra_headers": {"anthropic-beta": "some-beta-2026-01-01"}},
        {"extra_body": {"top_k": 5}},
        {"temperature": 0.7},
    ],
    ids=[
        "model",
        "system",
        "messages",
        "max_tokens",
        "effort",
        "thinking",
        "tools",
        "schema",
        "beta-header",
        "extra_body",
        "a-param-no-worker-sends-yet",
    ],
)
def test_every_input_that_changes_the_answer_changes_the_key(overrides):
    """Sampling dials, `thinking`, betas — anything that shapes the output.

    `params` is a **deny-list**: everything that is not the request's spine and
    not `timeout` is hashed, including fields no worker sends today. That is the
    property under test. An allow-list would silently drop the next dial someone
    adds, and the eval would answer a question nobody asked.
    """
    assert cassette.request_key(_request()) != cassette.request_key(
        _request(**overrides)
    )


def test_timeout_is_the_only_thing_left_out_of_the_key():
    # The worker passes its deadline alongside the request. A deadline aborts a
    # call; it cannot change what the model says. Tuning one must not invalidate
    # every cassette recorded under it — and it is the *only* exemption, which
    # is what this asserts.
    assert cassette.request_key(_request(timeout=30.0)) == cassette.request_key(
        _request()
    )
    assert cassette.NOT_IN_KEY == frozenset({"timeout"})


def test_the_output_schema_travels_as_its_json_schema():
    # Two schemas that differ only in a docstring are two different prompts
    # (`messages.parse` renders the docstring into the tool description), so
    # they must not share a key.
    class A(BaseModel):
        """One rubric."""

        value: str

    class B(BaseModel):
        """A different rubric."""

        value: str

    assert cassette.request_key(_request(output_format=A)) != cassette.request_key(
        _request(output_format=B)
    )


def test_an_unserializable_param_fails_loudly():
    # Silently hashing an object by identity (or skipping it) would key two
    # different requests to one cassette. Better to stop.
    with pytest.raises(cassette.CassetteError):
        cassette.request_key(_request(mystery=object()))


# --- The store -----------------------------------------------------------


def test_a_recorded_sample_round_trips(tmp_path):
    store = cassette.CassetteStore(tmp_path)
    key = cassette.request_key(_request())
    store.append(key, _sample(), model="claude-sonnet-5", summary="a turn")
    loaded = store.load(key)
    assert loaded.key == key
    assert loaded.model == "claude-sonnet-5"
    assert [s["parsed_output"]["value"] for s in loaded.samples] == ["one"]


def test_samples_accumulate_under_one_key(tmp_path):
    store = cassette.CassetteStore(tmp_path)
    key = cassette.request_key(_request())
    store.append(key, _sample("one"), model="m", summary="s")
    store.append(key, _sample("two"), model="m", summary="s")
    assert len(store.load(key).samples) == 2


def test_an_unrecorded_key_loads_as_none(tmp_path):
    assert cassette.CassetteStore(tmp_path).load("0" * 64) is None


def test_a_cassette_is_a_diffable_file(tmp_path):
    # Cassettes are committed, so they are read in review. Sorted keys, real
    # 汉字, and a trailing newline keep the diff about the answer.
    store = cassette.CassetteStore(tmp_path)
    key = cassette.request_key(_request())
    store.append(key, _sample("你好"), model="m", summary="s")
    raw = store.path_for(key).read_text(encoding="utf-8")
    assert "你好" in raw
    assert raw.endswith("\n")
    assert json.loads(raw)["key"] == key


def test_the_store_lists_what_it_holds(tmp_path):
    store = cassette.CassetteStore(tmp_path)
    key = cassette.request_key(_request())
    store.append(key, _sample(), model="m", summary="s")
    assert list(store.keys()) == [key]


# --- The client ----------------------------------------------------------


class _Live:
    """A stand-in for the real SDK client, counting what it is asked to do."""

    def __init__(self, *results):
        self.calls = []
        self._results = list(results)
        self.messages = self

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self._results.pop(0)


def _response(value="one", stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        parsed_output=_Other(value=value),
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=2,
            cache_read_input_tokens=8,
            cache_creation_input_tokens=0,
        ),
    )


async def test_replay_returns_the_recorded_response(tmp_path):
    store = cassette.CassetteStore(tmp_path)
    request = _request()
    store.append(cassette.request_key(request), _sample(), model="m", summary="s")
    client = cassette.CassetteClient(store)

    response = await client.messages.parse(**request, timeout=30.0)

    assert isinstance(response.parsed_output, _Other)
    assert response.parsed_output.value == "one"
    assert response.stop_reason == "end_turn"
    assert response.usage.input_tokens == 10


async def test_a_miss_fails_loudly_and_never_calls_live(tmp_path):
    live = _Live(_response())
    client = cassette.CassetteClient(cassette.CassetteStore(tmp_path), live=live)

    with pytest.raises(cassette.CassetteMiss) as excinfo:
        await client.messages.parse(**_request())

    assert "--record" in str(excinfo.value)
    assert live.calls == []


async def test_repeat_walks_the_distribution_rather_than_one_draw(tmp_path):
    # Three samples under one key, replayed four times: the layer cycles them,
    # so `--repeat` measures what the model actually did across the recording
    # instead of the first draw three times.
    store = cassette.CassetteStore(tmp_path)
    key = cassette.request_key(_request())
    for value in ("one", "two", "three"):
        store.append(key, _sample(value), model="m", summary="s")
    client = cassette.CassetteClient(store)

    seen = [
        (await client.messages.parse(**_request())).parsed_output.value
        for _ in range(4)
    ]
    assert seen == ["one", "two", "three", "one"]


async def test_record_mode_calls_live_on_a_miss_and_writes_the_cassette(tmp_path):
    store = cassette.CassetteStore(tmp_path)
    live = _Live(_response("fresh"))
    client = cassette.CassetteClient(store, record=True, live=live)

    response = await client.messages.parse(**_request(), timeout=30.0)

    assert response.parsed_output.value == "fresh"
    assert len(live.calls) == 1
    # The deadline travels to the network but not into the cassette's identity.
    assert live.calls[0]["timeout"] == 30.0
    recorded = store.load(cassette.request_key(_request()))
    assert recorded.samples[0]["parsed_output"] == {"value": "fresh"}
    assert recorded.samples[0]["usage"]["cache_read_input_tokens"] == 8


async def test_record_mode_tops_up_to_the_sample_count(tmp_path):
    store = cassette.CassetteStore(tmp_path)
    key = cassette.request_key(_request())
    store.append(key, _sample("one"), model="m", summary="s")
    live = _Live(_response("two"), _response("three"))
    client = cassette.CassetteClient(store, record=True, samples=3, live=live)

    await client.messages.parse(**_request())
    await client.messages.parse(**_request())
    await client.messages.parse(**_request())

    # Two calls bought the two missing samples; the third replayed.
    assert len(live.calls) == 2
    assert len(store.load(key).samples) == 3


async def test_record_mode_spends_nothing_once_the_key_is_full(tmp_path):
    store = cassette.CassetteStore(tmp_path)
    key = cassette.request_key(_request())
    store.append(key, _sample(), model="m", summary="s")
    live = _Live()
    client = cassette.CassetteClient(store, record=True, samples=1, live=live)

    await client.messages.parse(**_request())

    assert live.calls == []


async def test_refresh_replaces_the_recording_instead_of_appending(tmp_path):
    # What the scheduled re-record job runs: the point is a diff against live,
    # and appending to a stale recording would bury it.
    store = cassette.CassetteStore(tmp_path)
    key = cassette.request_key(_request())
    store.append(key, _sample("stale"), model="m", summary="s")
    client = cassette.CassetteClient(
        store, record=True, samples=1, refresh=True, live=_Live(_response("fresh"))
    )

    await client.messages.parse(**_request())

    assert [s["parsed_output"]["value"] for s in store.load(key).samples] == ["fresh"]


async def test_a_request_without_a_schema_is_out_of_scope(tmp_path):
    # Every Anthropic call in the repo today is a structured `messages.parse`.
    # A streaming or free-text call is a real gap in the layer (Stream B's
    # verdict streaming is the known case), so it must say so, not guess.
    client = cassette.CassetteClient(cassette.CassetteStore(tmp_path))
    with pytest.raises(cassette.CassetteError):
        await client.messages.parse(**_request(output_format=None))


# --- The seam the evals actually use --------------------------------------


async def test_the_real_conversation_worker_replays_off_a_cassette(tmp_path):
    """One real worker through the real client, keyed off the real request.

    Stubbing both sides would prove the layer agrees with itself. This proves
    it agrees with `conversation.build_request`.
    """
    store = cassette.CassetteStore(tmp_path)
    request = conversation.build_request(
        kb_block="VOCAB block bytes",
        sketch="SKETCH bytes",
        dialogue=[],
        user_text="你好",
        forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
    )
    parsed = ConversationResult.model_validate(
        {
            "partner_response": {"zh": "你好！", "pinyin": "nǐ hǎo!"},
            "user_reading": {"zh": "你好", "pinyin": "nǐ hǎo"},
            "turn_annotation": {"learner_said_goodbye": False},
        }
    )
    store.append(
        cassette.request_key(request),
        {
            "stop_reason": "end_turn",
            "parsed_output": parsed.model_dump(mode="json"),
            "usage": {"input_tokens": 900, "cache_read_input_tokens": 850},
        },
        model=config.CONVERSATION_MODEL,
        summary="greetings opening",
    )

    reply, annotation, reading, usage = await conversation.respond(
        kb_block="VOCAB block bytes",
        sketch="SKETCH bytes",
        dialogue=[],
        user_text="你好",
        forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
        client=cassette.CassetteClient(store),
    )

    assert reply.zh == "你好！"
    assert reading.zh == "你好"
    assert annotation.learner_said_goodbye is False
    assert usage.cache_read_input_tokens == 850


async def test_a_miss_escapes_the_worker_instead_of_degrading(tmp_path):
    # `respond` wraps every SDK failure into `ConversationError`, which the
    # runner catches and reports as one lost run. A miss must not arrive
    # dressed as a flaky API — it is a stale cassette, and it stops the run.
    with pytest.raises(cassette.CassetteMiss):
        await conversation.respond(
            kb_block="KB",
            sketch="S",
            dialogue=[],
            user_text="你好",
            forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
            client=cassette.CassetteClient(cassette.CassetteStore(tmp_path)),
        )


async def test_the_real_grader_replays_off_a_cassette(tmp_path):
    """The turn's other worker, through the same client.

    `run_text_turn` hands one `client` to both the converser and the grader, so
    a layer that only fits one of them fits neither in practice.
    """
    scenario = kb.load_scenario("food-ordering")
    store = cassette.CassetteStore(tmp_path)
    request = grader.build_request(
        scenario=scenario, dialogue=[], user_text="我要一杯茶", opening_line="您好"
    )
    result = GraderResult(coherence="on_track", slots_filled=[])
    store.append(
        cassette.request_key(request),
        {
            "stop_reason": "end_turn",
            "parsed_output": result.model_dump(mode="json"),
            "usage": {"input_tokens": 400, "output_tokens": 20},
        },
        model=config.GRADER_MODEL,
        summary="food-ordering, a drink",
    )

    grade, usage = await grader.grade(
        scenario=scenario,
        dialogue=[],
        user_text="我要一杯茶",
        opening_line="您好",
        client=cassette.CassetteClient(store),
    )

    assert grade.coherence == "on_track"
    assert usage.output_tokens == 20


def test_the_spoken_schema_and_the_text_schema_are_different_cassettes():
    # `want_reading` picks the schema and nothing else. Two different answers
    # are being asked for, so they must not share one recording.
    common = dict(
        kb_block="KB",
        sketch="S",
        dialogue=[],
        user_text="你好",
        forgiveness_level=config.FORGIVENESS_LEVEL_DEFAULT,
    )
    text = conversation.build_request(**common, want_reading=True)
    spoken = conversation.build_request(**common, want_reading=False)
    assert text["output_format"] is ConversationResult
    assert spoken["output_format"] is SpokenConversationResult
    assert cassette.request_key(text) != cassette.request_key(spoken)


def test_the_default_store_is_the_committed_cassette_directory():
    root = cassette.CassetteStore.default_root()
    assert root.parts[-2:] == ("evals", "cassettes")


# --- The runner's flags ---------------------------------------------------


def _parse(argv):
    parser = argparse.ArgumentParser()
    cassette.cli.add_arguments(parser)
    return parser.parse_args(argv)


def test_a_runner_replays_by_default(tmp_path):
    client = cassette.cli.client_from_args(_parse(["--cassettes", str(tmp_path)]))
    assert client.record is False
    assert client.store.root == tmp_path


def test_record_and_samples_reach_the_client(tmp_path):
    client = cassette.cli.client_from_args(
        _parse(["--record", "--samples", "3", "--cassettes", str(tmp_path)])
    )
    assert (client.record, client.samples) == (True, 3)


def test_refresh_without_record_is_refused():
    # It would silently do nothing, and the scheduled job would report that
    # yesterday's recording still agrees with yesterday's recording.
    with pytest.raises(SystemExit):
        cassette.cli.client_from_args(_parse(["--refresh"]))
