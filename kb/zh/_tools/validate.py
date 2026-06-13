#!/usr/bin/env python3
"""Validate a KB topic against the HSK membership index and scope rules.

This is the **guardrail the topic-authoring skill runs** (and you can run by
hand) — not a unit test. It fails loudly so a malformed topic never ships.
Stdlib only; reads the lean `word → band` index and the universal band ceiling.

Checks (ERROR fails the run; WARN is advisory):
  - frontmatter parses and has id / display_name / target_vocab            ERROR
  - every target_vocab + vocab-table word is ∈ HSK at/below the ceiling,
    or compositional (a compound whose component chars are all in-band)     ERROR
  - vocab-table phrases compose from in-band components                     ERROR
  - every target_vocab word is documented in vocab.md                       WARN
  - the topic is listed in kb/zh/index.md                                   ERROR
  - every word used in dialogues is in the topic's taught set
    (vocab tables + declared proper_names); an in-band-but-undocumented
    word is a WARN, an out-of-HSK / undeclared token is an ERROR

Usage:
    python validate.py <topic_dir> [<topic_dir> ...]
    python validate.py --all          # every topic under kb/zh/
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ZH = os.path.join(HERE, os.pardir)            # kb/zh
HSK = os.path.join(ZH, "_hsk", "hsk-3.0.json")
CEILING = os.path.join(ZH, "_hsk", "ceiling.json")
INDEX = os.path.join(ZH, "index.md")

HAN = r"一-鿿"
HAN_RUN = re.compile(f"[{HAN}]+")
TABLE_WORD = re.compile(rf"^\|\s*([{HAN}]+)\s*\|")
FLOW_LIST = lambda key, text: (
    re.search(rf"^{key}:\s*\[(.*?)\]", text, re.M))


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_frontmatter(topic_md):
    with open(topic_md, encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    fm = m.group(1) if m else ""
    def scalar(key):
        mm = re.search(rf"^{key}:\s*(.+)$", fm, re.M)
        return mm.group(1).strip().strip('"') if mm else None
    def flow(key):
        mm = FLOW_LIST(key, fm)
        return [w.strip() for w in mm.group(1).split(",") if w.strip()] if mm else []
    return {
        "id": scalar("id"),
        "display_name": scalar("display_name"),
        "target_vocab": flow("target_vocab"),
        "proper_names": flow("proper_names"),
    }


def table_words(md_path):
    """First column of every markdown table row (the 汉字)."""
    words = []
    if not os.path.exists(md_path):
        return words
    with open(md_path, encoding="utf-8") as f:
        for line in f:
            m = TABLE_WORD.match(line)
            if m:
                words.append(m.group(1))
    return words


def dialogue_words(md_path):
    """Greedy-tokenize the 汉字 dialogue lines (skip the italic pinyin lines)."""
    runs = []
    if not os.path.exists(md_path):
        return runs
    with open(md_path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s.startswith(">") and not re.match(r"^>\s*_", s):
                runs.extend(HAN_RUN.findall(s))
    return runs


def in_band(word, hsk, ceiling):
    """Word is fair game if it's a list entry ≤ ceiling, or a compound whose
    every component character is a list entry ≤ ceiling."""
    b = hsk.get(word)
    if b is not None:
        return b <= ceiling
    if len(word) > 1:
        return all((hsk.get(c) is not None and hsk[c] <= ceiling) for c in word)
    return False


def tokenize(run, lexicon):
    """Greedy longest-match over a lexicon; returns (matched, leftovers)."""
    matched, leftover, i, n = [], [], 0, len(run)
    while i < n:
        hit = None
        for j in range(min(n, i + 6), i, -1):
            if run[i:j] in lexicon:
                hit = run[i:j]
                break
        if hit:
            matched.append(hit)
            i += len(hit)
        else:
            leftover.append(run[i])
            i += 1
    return matched, leftover


def validate_topic(topic_dir, hsk, ceiling, index_text):
    errors, warns = [], []
    fm = parse_frontmatter(os.path.join(topic_dir, "topic.md"))

    for field in ("id", "display_name"):
        if not fm[field]:
            errors.append(f"frontmatter missing `{field}`")
    if not fm["target_vocab"]:
        errors.append("frontmatter missing/empty `target_vocab`")

    vocab_tbl = table_words(os.path.join(topic_dir, "vocab.md"))
    vocab_set = set(vocab_tbl)

    # membership: target_vocab + every vocab-table word
    for w in set(fm["target_vocab"]) | vocab_set:
        if not in_band(w, hsk, ceiling):
            b = hsk.get(w)
            why = f"band {b}" if b is not None else "not in HSK index"
            errors.append(f"`{w}` is out of scope ({why}, ceiling={ceiling})")

    # every target word should be documented in vocab.md
    for w in fm["target_vocab"]:
        if w not in vocab_set:
            warns.append(f"`{w}` in target_vocab but not documented in vocab.md")

    # index.md lists the topic
    if fm["id"] and fm["id"] not in index_text:
        errors.append(f"topic id `{fm['id']}` not listed in kb/zh/index.md")

    # dialogue scope
    lexicon = vocab_set | set(fm["target_vocab"]) | set(fm["proper_names"])
    for run in dialogue_words(os.path.join(topic_dir, "dialogues.md")):
        matched, leftover = tokenize(run, lexicon)
        for tok in leftover:
            if in_band(tok, hsk, ceiling):
                warns.append(f"dialogue uses in-band but undocumented `{tok}`")
            else:
                errors.append(f"dialogue uses out-of-scope `{tok}` "
                              f"(not in taught vocab or proper_names)")
    return errors, warns


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("topics", nargs="*", help="topic dir(s), e.g. kb/zh/greetings")
    ap.add_argument("--all", action="store_true", help="validate every topic under kb/zh/")
    args = ap.parse_args()

    hsk = load_json(HSK)
    ceiling = int(load_json(CEILING)["band_ceiling"])
    with open(INDEX, encoding="utf-8") as f:
        index_text = f.read()

    dirs = args.topics
    if args.all:
        dirs = [os.path.join(ZH, d) for d in sorted(os.listdir(ZH))
                if os.path.isfile(os.path.join(ZH, d, "topic.md"))]
    if not dirs:
        ap.error("pass a topic dir or --all")

    total_err = 0
    for d in dirs:
        name = os.path.basename(os.path.normpath(d))
        errors, warns = validate_topic(d, hsk, ceiling, index_text)
        total_err += len(errors)
        status = "FAIL" if errors else "ok"
        print(f"[{status}] {name} (ceiling={ceiling})")
        for e in errors:
            print(f"  ERROR: {e}")
        for w in warns:
            print(f"  warn:  {w}")
    sys.exit(1 if total_err else 0)


if __name__ == "__main__":
    main()
