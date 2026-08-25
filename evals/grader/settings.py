"""One point in the grader's settings space, and how to stand the app on it.

`workers/grader.build_request` reads `config.GRADER_MODEL`, `GRADER_EFFORT` and
`GRADER_MAX_TOKENS` **at call time**. That is what lets a sweep vary them
without adding an eval-only argument to a production worker: setting the module
attribute is exactly what setting the environment variable does, and the request
the sweep measures is the request the app builds.

The alternative — threading `model=`/`effort=` through `grade()` for the
sweep's benefit — would put a parameter on the hot path that only an eval ever
passes, and leave the thing being measured one argument away from the thing
being shipped.
"""
import contextlib
from dataclasses import dataclass
from typing import Tuple

from backend import config

# The dials this sweep turns. `GRADER_TIMEOUT_S` is not among them: it aborts a
# call, it does not change what the call says or how long a successful one takes.
_DIALS = {
    "model": "GRADER_MODEL",
    "effort": "GRADER_EFFORT",
    "max_tokens": "GRADER_MAX_TOKENS",
}


@dataclass(frozen=True)
class Setting:
    """A model, an effort level, and a token budget."""

    model: str
    effort: str
    max_tokens: int

    @property
    def id(self) -> str:
        """`model/effort` — the budget is held constant, so it is not in the name."""
        return f"{self.model}/{self.effort}"


# The budget every arm of the sweep runs at: today's shipped value. It is held
# constant on purpose. `max_tokens` is not a variable of this experiment, it is
# an *output* of it — the sweep measures what real grades consume so the cap can
# be read off that, and a run whose budget moved with its model could not say
# which of the two lost a grade.
SWEEP_MAX_TOKENS = 4096

# Opus 5 against Sonnet 5, `medium` against `low`. Four arms, because the
# question is not "is Sonnet fast?" but whether the cheap model at low effort
# gives up any accuracy the expensive one at medium was buying.
DEFAULT_MATRIX: Tuple[Setting, ...] = tuple(
    Setting(model=model, effort=effort, max_tokens=SWEEP_MAX_TOKENS)
    for model in ("claude-opus-5", "claude-sonnet-5")
    for effort in ("medium", "low")
)


@contextlib.contextmanager
def applied(setting: Setting):
    """Run the body with `config` standing on `setting`, then put it back.

    Restores on the way out however the body left — a raised call in the middle
    of a sweep must not leave the next arm measuring the wrong model.
    """
    before = {name: getattr(config, name) for name in _DIALS.values()}
    for field, name in _DIALS.items():
        setattr(config, name, getattr(setting, field))
    try:
        yield setting
    finally:
        for name, value in before.items():
            setattr(config, name, value)
