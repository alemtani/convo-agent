#!/usr/bin/env bash
# Stop hook: run the test suite and block the turn from ending if it fails.
#
# Bootstrap-tolerant by design:
#   - pytest not installed yet        -> no-op (exit 0)
#   - no tests collected (exit 5)     -> no-op (exit 0)
#   - tests pass (exit 0)             -> allow stop (exit 0)
#   - tests fail (any other exit)     -> block stop (exit 2), feed output to Claude
#
# Exit 2 is the Stop-hook signal that blocks completion; stderr is shown to Claude.

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

# Prefer the project venv if present, else fall back to python3.
if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  PY=python3
fi

# If pytest isn't available, do nothing (pre-`pip install` bootstrap).
"$PY" -c 'import pytest' 2>/dev/null || exit 0

out=$("$PY" -m pytest -q 2>&1)
code=$?

# 0 = passed, 5 = no tests collected yet. Both are fine.
if [ "$code" -eq 0 ] || [ "$code" -eq 5 ]; then
  exit 0
fi

echo "$out" >&2
echo "" >&2
echo "Stop hook blocked: pytest exited $code. Fix the failing tests before ending the turn." >&2
exit 2
