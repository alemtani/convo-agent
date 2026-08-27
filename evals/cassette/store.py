"""Cassettes on disk: one file per key, committed to the repo.

A cassette is a recording of what the model said, not of what we asked. The
request is not stored: it is rebuilt from the code on every run, and a copy of
a 10KB KB block in every file would make the directory unreadable in review for
no gain. What is stored instead is a one-line `summary`, so a reviewer looking
at a diff can tell which turn changed its mind.

The file is JSON with sorted keys, real 汉字 (`ensure_ascii=False`), and a
trailing newline, because it is read by people in a pull request.
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from evals.cassette.key import CassetteError

FILENAME_SUFFIX = ".json"

# A cassette is named for its key, which is a sha256 hex digest. Anything else
# in the directory — a README, a manifest someone parked there — is not a
# recording, and `prune` must never mistake one for a stale key.
_KEY_CHARS = set("0123456789abcdef")


def _is_key(stem: str) -> bool:
    return len(stem) == 64 and set(stem) <= _KEY_CHARS


@dataclass
class Cassette:
    """Every sample recorded under one key.

    `samples` is a list because a single recording can be a lucky draw. N draws
    of the same request are the distribution the eval asserts against; see
    `client.CassetteClient` for how a replay walks them.
    """

    key: str
    model: str
    summary: str = ""
    recorded_at: str = ""
    samples: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "model": self.model,
            "summary": self.summary,
            "recorded_at": self.recorded_at,
            "samples": self.samples,
        }


class CassetteStore:
    """A directory of cassettes, addressed by key."""

    def __init__(self, root):
        self.root = Path(root)

    @staticmethod
    def default_root() -> Path:
        """`evals/cassettes/`, resolved from this file rather than the CWD.

        The eval runner, pytest and the scheduled re-record job all start from
        different places; a store that moved with the CWD would silently record
        a second copy of everything.
        """
        return Path(__file__).resolve().parent.parent / "cassettes"

    def path_for(self, key: str) -> Path:
        return self.root / f"{key}{FILENAME_SUFFIX}"

    def load(self, key: str) -> Optional[Cassette]:
        """The cassette under `key`, or `None` if nothing is recorded."""
        path = self.path_for(key)
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        if raw.get("key") != key:
            raise ValueError(f"{path} holds key {raw.get('key')}, not {key}")
        return Cassette(
            key=raw["key"],
            model=raw.get("model", ""),
            summary=raw.get("summary", ""),
            recorded_at=raw.get("recorded_at", ""),
            samples=list(raw.get("samples", [])),
        )

    def append(
        self,
        key: str,
        sample: Dict[str, Any],
        *,
        model: str,
        summary: str = "",
        replace: bool = False,
    ) -> Cassette:
        """Add one sample under `key`; `replace` starts the recording over."""
        cassette = None if replace else self.load(key)
        if cassette is None:
            cassette = Cassette(key=key, model=model, summary=summary)
        cassette.model = model
        cassette.summary = summary or cassette.summary
        cassette.recorded_at = _now()
        cassette.samples.append(sample)
        self.write(cassette)
        return cassette

    def write(self, cassette: Cassette) -> None:
        os.makedirs(self.root, exist_ok=True)
        with open(self.path_for(cassette.key), "w", encoding="utf-8") as handle:
            json.dump(
                cassette.to_json(),
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")

    def prune(self, *, keep) -> List[str]:
        """Delete every recorded key not in `keep`; return what went, sorted.

        A prompt edit changes the key, so the recording made under the old one
        becomes unreachable: nothing can produce that key again, and the
        filename is a hash, so no one can read it either. It stops being
        evidence and becomes a file the directory cannot be reasoned about
        around.

        Only ever safe after a run that swept **every** key — the scheduled
        re-record job. A partial run (`--case`) touches a handful and would
        delete the rest, which is why `--prune` is refused without `--record`
        and refused alongside `--case`.
        """
        keep = set(keep)
        if not keep:
            # A run that reached no key at all did not sweep anything: it
            # crashed, or `--case` matched nothing, or the API was down. The
            # corpus costs real money to rebuild, so this is the one place the
            # layer refuses rather than doing as it is told.
            raise CassetteError("refusing to prune a store down to keep nothing")
        removed = [key for key in self.keys() if key not in keep]
        for key in removed:
            self.path_for(key).unlink()
        return removed

    def keys(self) -> Iterator[str]:
        """Every recorded key, sorted. What the re-record job iterates."""
        if not self.root.exists():
            return iter(())
        return iter(
            sorted(
                p.stem
                for p in self.root.glob(f"*{FILENAME_SUFFIX}")
                if _is_key(p.stem)
            )
        )


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
