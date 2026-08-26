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
  - the scenario block, if present (see docs/SCENARIOS.md):
      · every `expressible_with` word ∈ target_vocab, at/below the ceiling   ERROR
      · more than one slot — substance                                      ERROR
      · at least one `request` slot — an obstacle                           ERROR
      · no duplicate slot ids                                                ERROR
      · a `max_turns` override is ≥ the derived cap and states a reason      ERROR
      · `situation` / `goal` are non-empty and ASCII                         ERROR
      · a `request` slot requires ASCII `withholding` prose — the scene must
        not answer its own question, because the partner is goal-blind         ERROR
    A topic with no scenario at all is a WARN — topics may land first.

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

# Parse topic.md with the *loader's* parser, so the guardrail checks exactly
# what the service will read — one parser, no drift. This is the only coupling
# and it runs one way: authoring tools may import `backend`, never the reverse
# (CLAUDE.md). `backend.kb` is stdlib-only, so this file stays stdlib-only too.
sys.path.insert(0, os.path.abspath(os.path.join(ZH, os.pardir, os.pardir)))
from backend import kb  # noqa: E402
HSK = os.path.join(ZH, "_hsk", "hsk-3.0.json")
CEILING = os.path.join(ZH, "_hsk", "ceiling.json")
INDEX = os.path.join(ZH, "index.md")

HAN = r"一-鿿"
HAN_RUN = re.compile(f"[{HAN}]+")
TABLE_WORD = re.compile(rf"^\|\s*([{HAN}]+)\s*\|")


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_frontmatter(topic_md):
    """Parse topic.md via the loader. Returns (topic, errors); topic is None if
    the frontmatter is malformed — a bad topic gets an ERROR, not a traceback."""
    try:
        with open(topic_md, encoding="utf-8") as f:
            return kb.parse_topic_frontmatter(f.read()), []
    except (OSError, kb.KbError) as exc:
        return None, [f"topic.md: {exc}"]


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


def scenario_errors(scenario, target_vocab, hsk, ceiling):
    """The six authoring rules for a scenario block (docs/SCENARIOS.md)."""
    errors = []
    tv = set(target_vocab)

    # 1. every expressible_with word is achievable: taught, and in band.
    for slot in scenario.slots:
        for w in slot.expressible_with:
            band = hsk.get(w)
            if band is not None and band > ceiling:
                errors.append(f"slot '{slot.id}' expressible_with {w} is HSK band "
                              f"{band}, above ceiling {ceiling}")
            elif not in_band(w, hsk, ceiling):
                errors.append(f"slot '{slot.id}' expressible_with {w} is not in the "
                              f"HSK index (ceiling={ceiling})")
            if w not in tv:
                errors.append(f"slot '{slot.id}' expressible_with {w} not in target_vocab")

    # 2. substance. A single slot is a one-exchange conversation by construction.
    if scenario.n_slots <= 1:
        errors.append(f"{scenario.n_slots} slot — a one-exchange scenario. "
                      "Needs more than one.")

    # 3. an obstacle. Informs only is a vocabulary drill, however many there are.
    if scenario.n_request_slots < 1:
        errors.append("no request slots — the learner never has to extract "
                      "anything. This is a vocabulary drill, not a scenario.")

    # 4. unique slot ids. `depends_on` used to live here as a graph check;
    #    A2 removed the field, so leftover YAML fails at parse as an unknown
    #    key rather than as a cycle.
    seen = set()
    for slot in scenario.slots:
        if slot.id in seen:
            errors.append(f"duplicate slot id '{slot.id}'")
        seen.add(slot.id)

    # 5. a pacing override may buy room; it may never starve the goal, and it
    #    must say why (the derivation is the default, not an opinion to ignore).
    if scenario.authored_max_turns is not None:
        derived = kb.derive_max_turns(scenario.n_slots, scenario.n_request_slots)
        if scenario.authored_max_turns < derived:
            errors.append(f"max_turns {scenario.authored_max_turns} is below the "
                          f"derived {derived} — the override starves the goal")
        if not scenario.max_turns_reason:
            errors.append("max_turns override needs a `max_turns_reason`")

    # 6. the learner has to be able to read the task.
    for field in ("situation", "goal"):
        value = getattr(scenario, field)
        if not value.strip():
            errors.append(f"scenario `{field}` is empty")
        elif not value.isascii():
            errors.append(f"scenario `{field}` is not ASCII — a band-1 learner "
                          "cannot read a Chinese task description")

    # 7. a request slot needs a scene that does not already answer it.
    #
    #    The converser is goal-blind (docs/VALIDITY.md): it cannot be told to
    #    withhold a slot's answer, because it does not know the slot is there.
    #    An ordinary, helpful partner then volunteers the fact — in `greetings`
    #    it introduces itself, and `partner_name` is gone before the learner
    #    speaks. Only the situation can stop that.
    #
    #    Prose is not checkable: no rule here can tell whether a scene really
    #    leaves the gap open. What is checkable is whether the author answered
    #    the question at all, which is the same bargain `max_turns_reason`
    #    strikes. Scoped to request slots — an inform-only scenario withholds
    #    nothing, and is already rejected by rule 3 for that.
    if scenario.n_request_slots >= 1 and not (scenario.withholding or "").strip():
        errors.append(
            f"{scenario.n_request_slots} request slot(s) but no `withholding` — "
            "say what the scene does not hand over unasked. A goal-blind "
            "partner cannot withhold a fact it does not know is scored."
        )
    elif scenario.withholding and not scenario.withholding.isascii():
        errors.append("scenario `withholding` is not ASCII — it is stage "
                      "direction for the partner, not learner-facing Chinese")
    return errors


def validate_topic(topic_dir, hsk, ceiling, index_text):
    errors, warns = [], []
    fm, fm_errors = parse_frontmatter(os.path.join(topic_dir, "topic.md"))
    if fm is None:
        return fm_errors, warns

    if not fm.target_vocab:
        errors.append("frontmatter missing/empty `target_vocab`")

    vocab_tbl = table_words(os.path.join(topic_dir, "vocab.md"))
    vocab_set = set(vocab_tbl)

    # membership: target_vocab + every vocab-table word
    for w in set(fm.target_vocab) | vocab_set:
        if not in_band(w, hsk, ceiling):
            b = hsk.get(w)
            why = f"band {b}" if b is not None else "not in HSK index"
            errors.append(f"`{w}` is out of scope ({why}, ceiling={ceiling})")

    # every target word should be documented in vocab.md
    for w in fm.target_vocab:
        if w not in vocab_set:
            warns.append(f"`{w}` in target_vocab but not documented in vocab.md")

    # index.md lists the topic
    if fm.id and fm.id not in index_text:
        errors.append(f"topic id `{fm.id}` not listed in kb/zh/index.md")

    # the scenario block — absent is advisory, malformed is fatal
    if fm.scenario is None:
        warns.append("no `scenario:` block — the topic cannot be practised as a "
                     "goal-oriented session (docs/SCENARIOS.md)")
    else:
        errors.extend(scenario_errors(fm.scenario, fm.target_vocab, hsk, ceiling))

    # dialogue scope
    lexicon = vocab_set | set(fm.target_vocab) | set(fm.proper_names)
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
