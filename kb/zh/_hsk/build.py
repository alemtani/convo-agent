#!/usr/bin/env python3
"""Regenerate the HSK 3.0 membership list from the upstream wordlists.

This is the re-runnable source of `hsk-3.0.json` (replaces the one-shot curl).
The output is the *universe* of HSK 3.0 words with a `band` per word; consumers
(the validator, the topic-generator agent) filter to a learner's current band
ceiling. Expanding the learner's scope is therefore raising a number at use
time, not regenerating this file.

Usage:
    python build.py                 # all bands (1-7); default
    python build.py --max-band 2    # materialize only bands 1-2

Source: drkameleon/complete-hsk-vocabulary, wordlists/exclusive/new/<level>.json
(HSK 3.0 / "new" standard). Level 7 is the combined HSK 7-9 advanced band.
"""
import argparse
import json
import urllib.request

RAW = ("https://raw.githubusercontent.com/drkameleon/"
       "complete-hsk-vocabulary/main/wordlists/exclusive/new/{level}.json")
LEVELS = [1, 2, 3, 4, 5, 6, 7]  # 7 == the combined 7-9 band


def fetch(level):
    with urllib.request.urlopen(RAW.format(level=level)) as r:
        return json.load(r)


def distill(max_band):
    out = {}
    for band in LEVELS:
        if band > max_band:
            break
        for e in fetch(band):
            form = e["forms"][0]
            out[e["simplified"]] = {
                "pinyin": form["transcriptions"]["pinyin"],
                "gloss": (form["meanings"][0] if form["meanings"] else "").strip(),
                "pos": e.get("pos", []),
                "band": band,
            }
    # stable order: band, then pinyin — keeps diffs small across rebuilds
    return dict(sorted(out.items(), key=lambda kv: (kv[1]["band"], kv[1]["pinyin"])))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-band", type=int, default=7,
                    help="highest HSK band to include (default 7 = everything)")
    ap.add_argument("--out", default="hsk-3.0.json")
    args = ap.parse_args()

    words = distill(args.max_band)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False, indent=1)
    by_band = {}
    for w in words.values():
        by_band[w["band"]] = by_band.get(w["band"], 0) + 1
    print(f"wrote {args.out}: {len(words)} words; per band {dict(sorted(by_band.items()))}")


if __name__ == "__main__":
    main()
