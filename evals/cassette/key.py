"""The cassette key: `sha256(model + system + tools + messages + params)`.

The key is taken over the request **we assemble**, not over the HTTP bytes the
SDK ends up sending. That is the whole reason this layer is hand-rolled rather
than VCR-shaped: an eval asks "given this prompt, what does the model say?", so
the identity of a recording has to be the prompt, in the form the workers build
it. A layer keyed on a serialized HTTP body would also key on transport detail
the workers do not choose and cannot see.

Two consequences worth stating out loud:

- **Change a prompt, change the key.** Only the affected cases go stale. That is
  the property that makes a re-record wave cheap enough to do on every prompt
  edit, which is what A2's cuts need.
- **A param we cannot serialize is an error, never a skip.** Hashing around an
  unknown object would file two different requests under one cassette, and the
  eval would then report an answer to a question nobody asked.

**Everything in the payload is in the key.** `params` is a deny-list, not an
allow-list: `max_tokens`, `thinking`, `output_config.effort`, a beta header, a
sampling dial no worker sends yet — all of it shapes the output, so all of it is
hashed, including fields added after this file was written. The single exemption
is `timeout`, which aborts a call rather than changing what it says.

An allow-list here would be the quiet failure mode this whole layer exists to
prevent: the next dial someone adds would not change the key, the eval would
replay a recording made at a different setting, and nothing would say so.
"""
import hashlib
import json
from typing import Any, Dict

from pydantic import BaseModel


class CassetteError(Exception):
    """A request this layer cannot key, store, or replay."""


# The one exemption, and it earns it: a deadline aborts a call, it cannot change
# what the call says. Every worker passes its own from config, so hashing it
# would invalidate every cassette in the repo the day one is tuned.
#
# Nothing else belongs here. `extra_headers` carries beta flags and `extra_body`
# merges into the JSON payload — both change the answer, so both are hashed.
NOT_IN_KEY = frozenset({"timeout"})

# The request's spine, named because the key's shape mirrors the spec
# (`model + system + tools + messages + params`) and a reader should be able to
# check that. This is NOT an allow-list: every kwarg that is not here and not
# `timeout` still lands in `params` and is hashed. See the module docstring.
_SPINE = ("model", "system", "messages", "tools", "output_format")


def canonical_request(request: Dict[str, Any]) -> Dict[str, Any]:
    """The request reduced to what could change the model's answer.

    Structured output rides under `tools` because that is what it is on the
    wire: `messages.parse` renders the Pydantic model into a tool schema, and
    the schema carries the field descriptions and docstrings that are, in this
    codebase, a real part of the prompt (`backend/models.py`).

    `params` is everything else the caller sent, `timeout` excepted — sampling
    dials, `thinking`, `output_config.effort`, betas, and whatever a worker
    starts sending next week.
    """
    params = {
        name: value
        for name, value in request.items()
        if name not in _SPINE and name not in NOT_IN_KEY
    }
    return {
        "model": request.get("model"),
        "system": request.get("system"),
        "messages": request.get("messages"),
        "tools": {
            "tools": request.get("tools"),
            "output_format": _schema_of(request.get("output_format")),
        },
        "params": params,
    }


def request_key(request: Dict[str, Any]) -> str:
    """Hex sha256 over the canonical request. Stable across dict ordering."""
    return hashlib.sha256(_canonical_bytes(canonical_request(request))).hexdigest()


def _schema_of(output_format):
    """A Pydantic output schema as JSON, or `None` when there is no schema."""
    if output_format is None:
        return None
    if isinstance(output_format, type) and issubclass(output_format, BaseModel):
        return output_format.model_json_schema()
    raise CassetteError(
        f"cannot key an output_format of type {type(output_format).__name__}"
    )


def _canonical_bytes(canonical: Dict[str, Any]) -> bytes:
    try:
        text = json.dumps(
            canonical,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=_refuse,
        )
    except TypeError as exc:  # pragma: no cover - `default` raises first
        raise CassetteError(f"request is not serializable: {exc}") from exc
    return text.encode("utf-8")


def _refuse(value):
    raise CassetteError(
        f"cannot key a request containing {type(value).__name__}; "
        "the cassette layer covers plain JSON request fields and a Pydantic "
        "output_format"
    )
