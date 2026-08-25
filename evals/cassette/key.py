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
"""
import hashlib
import json
from typing import Any, Dict

from pydantic import BaseModel


class CassetteError(Exception):
    """A request this layer cannot key, store, or replay."""


# Settings that govern how the call travels, not what it asks. `timeout` is the
# live one: every worker passes its own deadline, and tuning a deadline must not
# invalidate every cassette in the repo.
TRANSPORT_KWARGS = frozenset(
    {"timeout", "extra_headers", "extra_query", "extra_body"}
)

# Named separately from the rest of `params` because they are the request's
# spine, and a key whose shape mirrors the spec (`model + system + tools +
# messages + params`) is a key a reader can check.
_SPINE = ("model", "system", "messages", "tools", "output_format")


def canonical_request(request: Dict[str, Any]) -> Dict[str, Any]:
    """The request reduced to what could change the model's answer.

    Structured output rides under `tools` because that is what it is on the
    wire: `messages.parse` renders the Pydantic model into a tool schema, and
    the schema carries the field descriptions and docstrings that are, in this
    codebase, a real part of the prompt (`backend/models.py`).
    """
    params = {
        name: value
        for name, value in request.items()
        if name not in _SPINE and name not in TRANSPORT_KWARGS
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
