---
name: kb-topic
description: >-
  Author, extend, or validate a Mandarin knowledge-base topic for convo-agent
  (kb/zh/<id>/ markdown: topic, vocab, grammar, dialogues). Use when adding a new
  topic, updating a topic's vocab/grammar/dialogues, adjusting the HSK band
  ceiling, or regenerating the HSK wordlist. Dev-time only — not part of the app.
---

# kb-topic — knowledge-base authoring

A **developer-invoked, dev-time** workflow for the convo-agent KB. It is *not*
part of the FastAPI service: nothing in `backend/` imports it and it never runs in
the request path. It produces/edits version-controlled markdown under `kb/zh/` and
ships it via a PR.

## The non-negotiable invariants

1. **Select vocab from the committed list; never invent in-band words.** Candidate
   vocab comes from `kb/zh/_hsk/hsk-3.0.json` (`word → band`), filtered to
   `band ≤ ceiling`. If a word you want isn't in the list, it isn't in scope.
2. **The list is authoritative for *membership only*.** Its pinyin/gloss are
   unreliable (吗→má, 也→surname). Curate pinyin/gloss yourself in `vocab.md`.
3. **The band ceiling is universal**, in `kb/zh/_hsk/ceiling.json` — never a
   per-topic frontmatter field. A topic's highest band is *derived* from its vocab.
4. **Dialogues stay in-scope:** only `target_vocab`, the compositional phrases in
   `vocab.md`, and declared `proper_names`. Dialogue pinyin is *generated*, not
   hand-typed (see step 5), so it always agrees with `vocab.md`.
5. **Nothing ships until `validate.py` is clean**, and it ships as a PR.

## Files a topic has

```
kb/zh/<id>/
  topic.md      # frontmatter (id, display_name, target_vocab, proper_names, related) + overview
  vocab.md      # curated tables: core words, compositional phrases, question words
  grammar.md    # minimal patterns, each using only vocab.md words
  dialogues.md  # 汉字 + generated pinyin seed exchanges
```
Model the shape on `kb/zh/greetings/` — it is the reference example.

## Add a new topic

1. Pick `id`, `display_name`, and the semantic field. Branch from `main`.
2. **Select vocab:** read `hsk-3.0.json`, take words in the field with
   `band ≤ ceiling` (`ceiling.json`). Note compositional phrases (你好 = 你+好) —
   they live in a separate `vocab.md` table, not in `target_vocab`.
3. Write `vocab.md` with **curated** pinyin/gloss + the phrase table.
4. Write `grammar.md` — only patterns expressible with the chosen vocab.
5. Write `dialogues.md` 汉字 lines (only in-scope words + `proper_names`). Then
   generate the pinyin line under each:
   `python kb/zh/_tools/annotate_pinyin.py kb/zh/<id>/vocab.md "<汉字 line>"`
6. Write `topic.md` (frontmatter + overview). Declare any `proper_names` used in
   dialogues (names are often out-of-HSK, e.g. 李/明, so they must be whitelisted).
7. Add the topic row to `kb/zh/index.md`.
8. **Validate:** `python kb/zh/_tools/validate.py kb/zh/<id>` — fix every ERROR.
9. Deliver via PR (see CLAUDE.md "Delivery").

## Extend / update a topic

Add the new vocab (still `band ≤ ceiling`), update grammar/dialogues, regenerate
affected pinyin, re-run `validate.py`. Additive — the frontmatter declares no band
to bump.

## Adjust the band ceiling (learner advanced a level)

Edit `band_ceiling` in `kb/zh/_hsk/ceiling.json`, then
`python kb/zh/_tools/validate.py --all`. Raising it never breaks anything;
*lowering* it will flag any topic that now exceeds scope — surface those.

## Regenerate the HSK wordlist

`python kb/zh/_hsk/build.py` (pinned to an upstream commit). Bump the pin in
`build.py` only deliberately, when you want upstream corrections.

## Tooling deps

`pip install -r kb/zh/_tools/requirements.txt` (pypinyin; `build.py` and
`validate.py` are stdlib-only).
