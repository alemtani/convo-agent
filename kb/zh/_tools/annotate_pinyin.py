#!/usr/bin/env python3
"""Annotate Chinese dialogue lines with pinyin, consistent with a topic's vocab.

The learner reads pinyin as much as the characters (it's the partner-reply
format too: 汉字 + pinyin), so every dialogue line carries a pinyin line. Pinyin
is *not* re-derived freely — it is composed from the topic's curated `vocab.md`
readings (greedy longest-match), so dialogues never contradict the vocabulary
table (e.g. neutral-tone 谢谢 xièxie, 朋友 péngyou). pypinyin fills only the gaps
(proper names, glue chars) and, crucially, applies 不/一 tone sandhi.

Usage:
    python annotate_pinyin.py <vocab.md> "<chinese line>"
    python annotate_pinyin.py <vocab.md> --stdin   # one line per stdin row

So it is a reusable authoring tool (the topic-generator agent calls it), not a
one-off.
"""
import argparse
import re
import sys

from pypinyin import Style, pinyin as _pp

# Extra readings the curated vocab table won't contain (proper names used in
# seed dialogues). Kept tiny and explicit; everything else falls back to pypinyin.
PROPER_NAMES = {"小明": "xiǎomíng", "小王": "xiǎo wáng", "小李": "xiǎo lǐ"}

PUNCT = "，。！？、：；…“”‘’（）"


def load_lexicon(vocab_md_path):
    """word -> curated pinyin, from the markdown tables in a topic's vocab.md."""
    lex = dict(PROPER_NAMES)
    row = re.compile(r"^\|\s*([一-鿿]+)\s*\|\s*([A-Za-zǖǘǚǜüāáǎàēéěèīíǐìōóǒòūúǔùǘǚ\s]+?)\s*\|")
    with open(vocab_md_path, encoding="utf-8") as f:
        for line in f:
            m = row.match(line)
            if m:
                lex[m.group(1)] = m.group(2).strip()
    return lex


def _fallback(ch):
    return _pp(ch, style=Style.TONE)[0][0]


def annotate(text, lex):
    """Greedy longest-match over the lexicon; pypinyin fallback per char.

    pypinyin is run on maximal Han runs so its 不/一 sandhi has context."""
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch in PUNCT or ch.isspace():
            i += 1
            continue
        if not ("一" <= ch <= "鿿"):
            out.append(ch)
            i += 1
            continue
        # try longest lexicon entry starting at i
        matched = None
        for j in range(min(n, i + 6), i, -1):
            if text[i:j] in lex:
                matched = (text[i:j], lex[text[i:j]])
                break
        if matched:
            out.append(matched[1])
            i += len(matched[0])
        else:
            out.append(_fallback(ch))
            i += 1
    return " ".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("vocab_md")
    ap.add_argument("line", nargs="?")
    ap.add_argument("--stdin", action="store_true")
    args = ap.parse_args()
    lex = load_lexicon(args.vocab_md)
    lines = (l.rstrip("\n") for l in sys.stdin) if args.stdin else [args.line]
    for line in lines:
        if line:
            print(annotate(line, lex))


if __name__ == "__main__":
    main()
