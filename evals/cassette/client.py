"""A drop-in for `AsyncAnthropic` at the one seam every worker uses.

`conversation.respond`, `grader.grade`, `sketch.write` and `feedback.card` all
take an optional `client` and call exactly one method on it:

    await client.messages.parse(**request, timeout=...)

So this class needs to be that shape and nothing more. Every Anthropic call in
the repo today is a non-streaming, structured `messages.parse`, which is what
keeps a hand-rolled layer this small. When one is not — B2's streamed verdict is
the known case — the layer must refuse rather than guess, and it does.

**Replay is the default and a miss is an error.** A layer that quietly fell back
to a live call on a miss would be indistinguishable from no layer at all on the
day someone edits a prompt, and the failure mode is a bill rather than a red
build. `record=True` (the runner's `--record`) is the only way to spend money.

**A replay walks the samples, it does not repeat the first one.** N recorded
draws of one request are a distribution; `replay.py --repeat N` exists to sample
it. Cycling is deterministic, so CI stays deterministic: same order in, same
answers out.
"""
from types import SimpleNamespace
from typing import Any, Dict, Optional

from evals.cassette.key import CassetteError, request_key
from evals.cassette.store import CassetteStore

# The usage fields the app reads (`orchestrator._report`, the live cache test).
# Recorded so a replayed turn reports the same token story a live one did.
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


class CassetteMiss(CassetteError):
    """A request with no recording, in a run that is not allowed to spend."""


class CassetteClient:
    """Replay recorded Anthropic responses; record them only when asked."""

    def __init__(
        self,
        store: Optional[CassetteStore] = None,
        *,
        record: bool = False,
        samples: int = 1,
        refresh: bool = False,
        live=None,
    ):
        self.store = store if store is not None else CassetteStore(
            CassetteStore.default_root()
        )
        self.record = record
        self.samples = samples
        self.refresh = refresh
        self.messages = _Messages(self)
        self._live = live
        self._cursor: Dict[str, int] = {}
        self._refreshed = set()
        self.hits = 0
        self.recorded = 0

    async def parse(self, **request) -> Any:
        output_format = request.get("output_format")
        if output_format is None:
            raise CassetteError(
                "the cassette layer covers structured `messages.parse` calls "
                "only; this request carries no output_format"
            )
        key = request_key(request)
        cassette = self.store.load(key)
        recorded = 0 if cassette is None else len(cassette.samples)
        if self.refresh and key not in self._refreshed:
            recorded = 0

        if recorded < self._wanted():
            if not self.record:
                raise CassetteMiss(
                    f"no cassette for {key[:12]}… ({_summarize(request)}).\n"
                    "Re-run with --record to make the call and record it, or "
                    "check whether a prompt change invalidated this key."
                )
            return await self._record(key, request)

        self.hits += 1
        sample = cassette.samples[self._cursor.get(key, 0) % len(cassette.samples)]
        self._cursor[key] = self._cursor.get(key, 0) + 1
        return _replayed(sample, output_format)

    def _wanted(self) -> int:
        """How many samples a key must hold. One, unless recording more."""
        return max(1, self.samples) if self.record else 1

    async def _record(self, key: str, request: Dict[str, Any]):
        response = await self._live_client().messages.parse(**request)
        replace = self.refresh and key not in self._refreshed
        self.store.append(
            key,
            _sample_of(response),
            model=request.get("model", ""),
            summary=_summarize(request),
            replace=replace,
        )
        self._refreshed.add(key)
        self.recorded += 1
        return response

    def _live_client(self):
        if self._live is None:
            from anthropic import AsyncAnthropic

            from backend import config

            self._live = AsyncAnthropic(
                api_key=config.ANTHROPIC_API_KEY,
                max_retries=config.CLAUDE_MAX_RETRIES,
            )
        return self._live


class _Messages:
    """`client.messages.parse` — the only surface the workers touch."""

    def __init__(self, client: CassetteClient):
        self._client = client

    async def parse(self, **request):
        return await self._client.parse(**request)


def _replayed(sample: Dict[str, Any], output_format):
    """A recorded sample, in the shape the workers read a live response in."""
    parsed = sample.get("parsed_output")
    return SimpleNamespace(
        stop_reason=sample.get("stop_reason"),
        parsed_output=(
            None if parsed is None else output_format.model_validate(parsed)
        ),
        usage=SimpleNamespace(
            **{
                name: sample.get("usage", {}).get(name)
                for name in USAGE_FIELDS
            }
        ),
    )


def _sample_of(response) -> Dict[str, Any]:
    """One live response, reduced to what a replay has to reproduce."""
    parsed = getattr(response, "parsed_output", None)
    usage = getattr(response, "usage", None)
    return {
        "stop_reason": getattr(response, "stop_reason", None),
        "parsed_output": None if parsed is None else parsed.model_dump(mode="json"),
        "usage": {
            name: getattr(usage, name, None) for name in USAGE_FIELDS
        },
    }


def _summarize(request: Dict[str, Any]) -> str:
    """A line a reviewer can read in a diff: model, schema, last thing said."""
    output_format = request.get("output_format")
    schema = getattr(output_format, "__name__", "?")
    messages = request.get("messages") or []
    last = _text_of(messages[-1]["content"]) if messages else ""
    if len(last) > 60:
        last = last[:59] + "…"
    return f"{request.get('model')} → {schema}: {last}"


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )
