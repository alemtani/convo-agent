"""Passcode gate for the deployed app — one shared secret, one signed cookie.

This is deliberately the smallest thing that closes the hole a public URL opens:
every `/api/*` call spends real Anthropic and Azure money, so the deployed app
cannot be open to whoever finds the hostname. It is *not* an account system —
DESIGN.md's "Hosting & User State" calls this out as the only seam that changes
to go multi-user, and nothing in the data model depends on it.

Two properties are worth the few lines they cost:

- **The signing key is derived from the passcode**, so rotating the passcode
  invalidates every outstanding session. With one shared credential and no user
  table there is nowhere else to revoke from — without this, a cookie copied off
  a phone outlives the only lever we have.
- **The expiry is inside the signed payload**, so a client can't extend its own
  session by editing the cookie.

Signing is stdlib `hmac`; the service has no need for a session framework and
`itsdangerous` isn't already a dependency.

The gate is active iff `config.APP_PASSCODE` is non-empty — unset means local
development, where a login screen is pure friction and there is no public URL to
protect. Because "I forgot to set the secret" is precisely the failure this
module exists to prevent, that state is not silent: `main` logs a warning at
startup and `/health` reports `auth: enabled|disabled` so one curl against the
deployed host answers the question from outside.

`config.APP_PASSCODE` is read at call time rather than captured at import, so
tests can set it with `monkeypatch` and so a rotation needs no import-order care.
"""
import hashlib
import hmac
import logging
import time
from typing import Optional

from backend import config

logger = logging.getLogger(__name__)

COOKIE_NAME = "convo_session"

# Domain separation: the derived key is only ever valid as a session-cookie key,
# so a passcode reused elsewhere can't produce a colliding signature.
_KEY_CONTEXT = b"convo-agent/session-cookie/v1"


def is_enabled() -> bool:
    """True when a passcode is configured, i.e. the gate should be enforced."""
    return bool(config.APP_PASSCODE)


def _signing_key() -> bytes:
    """Derive the HMAC key from the passcode.

    Deriving rather than configuring a second secret keeps the deploy to one
    env var *and* makes passcode rotation a revocation lever (see module
    docstring). The passcode is a human-typed string, so it is hashed into a
    full-width key rather than used as key material directly.
    """
    return hashlib.sha256(_KEY_CONTEXT + config.APP_PASSCODE.encode("utf-8")).digest()


def _sign(payload: str) -> str:
    return hmac.new(_signing_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def check_passcode(candidate: str) -> bool:
    """Compare a submitted passcode against the configured one, in constant time."""
    if not is_enabled() or not candidate:
        return False
    return hmac.compare_digest(candidate, config.APP_PASSCODE)


def issue_token(now: Optional[float] = None) -> str:
    """Mint a session token of the form `<expiry_unix>.<hmac>`.

    The expiry is the signed payload, so it is both the thing that limits the
    session and the thing a forger would have to rewrite.
    """
    now = time.time() if now is None else now
    expiry = int(now + config.SESSION_TTL_DAYS * 86400)
    return f"{expiry}.{_sign(str(expiry))}"


def verify_token(token: Optional[str]) -> bool:
    """True iff `token` carries our signature and has not expired.

    Every failure mode — absent, malformed, unsigned, re-signed under an old
    passcode, or simply stale — returns False rather than raising: this runs on
    the request path and the only question it answers is pass/fail.
    """
    if not token:
        return False
    expiry_raw, _, signature = token.partition(".")
    if not signature:
        return False
    try:
        expiry = int(expiry_raw)
    except ValueError:
        return False
    if not hmac.compare_digest(signature, _sign(expiry_raw)):
        return False
    return time.time() < expiry
