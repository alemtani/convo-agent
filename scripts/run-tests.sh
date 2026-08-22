#!/usr/bin/env bash
# Default pytest gate. Shared by every agent harness.
#
# Claude's Stop hook treats exit 2 as "block the turn". OpenCode's idle
# plugin cannot block; it toasts on the same non-zero exit.
#
# Bootstrap-tolerant:
#   - pytest not installed yet        -> no-op (exit 0)
#   - no tests collected (exit 5)     -> no-op (exit 0)
#   - tests pass (exit 0)             -> success
#   - tests fail (any other exit)     -> exit 2

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 0

if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  PY=python3
fi

"$PY" -c 'import pytest' 2>/dev/null || exit 0

out=$("$PY" -m pytest -q 2>&1)
code=$?

if [ "$code" -eq 0 ] || [ "$code" -eq 5 ]; then
  exit 0
fi

echo "$out" >&2
echo "" >&2
echo "Tests failed: pytest exited $code. Fix them before ending the turn." >&2
exit 2
