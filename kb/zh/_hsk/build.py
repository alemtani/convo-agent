#!/usr/bin/env python3
"""Regenerate the HSK 3.0 membership index from the upstream wordlists.

The output `hsk-3.0.json` is the *band-drift guard* and nothing more: a lean
`{word: band}` map for every HSK 3.0 word (all bands). It deliberately drops
pinyin/gloss/pos — the list is not trusted for those (see README), the validator
only needs membership + band, and the topic-generator agent fetches richer data
on demand for the handful of words a topic actually uses.

It is authoring-time only (never shipped or read at runtime), so it stays small
and doubles as the offline fixture for validation tests.

Pinned to an upstream commit (not `main`) so regeneration is reproducible and a
word can't silently drift between bands under us.

Usage:
    python build.py                 # all bands (1-7); default
    python build.py --max-band 2    # materialize only bands 1-2
"""
import argparse
import json
import urllib.request

# Pinned: drkameleon/complete-hsk-vocabulary @ this commit. Bump deliberately.
UPSTREAM_SHA = "7ac65bf1a6387d35f1ade478906172a19311c7f9"
RAW = ("https://raw.githubusercontent.com/drkameleon/complete-hsk-vocabulary/"
       f"{UPSTREAM_SHA}/wordlists/exclusive/new/{{level}}.json")
LEVELS = [1, 2, 3, 4, 5, 6, 7]  # 7 == the combined 7-9 advanced band


def fetch(level):
    with urllib.request.urlopen(RAW.format(level=level)) as r:
        return json.load(r)


def distill(max_band):
    out = {}
    for band in LEVELS:
        if band > max_band:
            break
        for e in fetch(band):
            out[e["simplified"]] = band
    # stable order: band, then word — keeps diffs small across rebuilds
    return dict(sorted(out.items(), key=lambda kv: (kv[1], kv[0])))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-band", type=int, default=7,
                    help="highest HSK band to include (default 7 = everything)")
    ap.add_argument("--out", default="hsk-3.0.json")
    args = ap.parse_args()

    words = distill(args.max_band)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    by_band = {}
    for b in words.values():
        by_band[b] = by_band.get(b, 0) + 1
    print(f"wrote {args.out}: {len(words)} words; per band {dict(sorted(by_band.items()))}")


if __name__ == "__main__":
    main()
