# HSK 3.0 reference wordlist (source of truth for band membership)

`hsk-3.0.json` is the **authoritative band-membership list** that every
`vocab.md` is checked against (see `CLAUDE.md` / `DESIGN.md`: vocab is *verified*
against a public HSK list, not recalled from memory). The topic-generator agent
*selects* vocab from this file; it never invents in-band words.

## It holds the whole universe; consumers filter by band ceiling

The file contains **all HSK 3.0 bands (1–7)**, each word tagged with its `band`.
It is deliberately not scoped to bands 1–2 — the learner advances, and the scope
should not be welded into a filename. Expanding a learner (or a topic) to a
higher band is therefore **raising a number at use time**, not regenerating this
file:

- a topic declares its ceiling in `topic.md` frontmatter (`hsk_band: [1, 2]`);
- the validator/agent reads that ceiling and accepts words with `band ≤ ceiling`.

So extending `greetings` to band 3 later = change the frontmatter range and
re-validate; the data is already present. (Band 7 is the combined HSK 7–9
advanced band.)

## Re-runnable, not a one-shot

`build.py` regenerates `hsk-3.0.json` from the upstream wordlists:

```bash
python build.py               # all bands (default)
python build.py --max-band 2  # materialize only bands 1–2, if you ever want a lean file
```

- Source: [`drkameleon/complete-hsk-vocabulary`](https://github.com/drkameleon/complete-hsk-vocabulary),
  `wordlists/exclusive/new/{1..7}.json` (HSK 3.0 / "new" standard).
- Distilled to `{word: {pinyin, gloss, pos, band}}` (10,969 words;
  band 1 = 506, band 2 = 750, …).
- Requires no extra deps (stdlib `urllib`).

## What it is and isn't authoritative for

- **Authoritative:** *membership* — "is this word in HSK 3.0, and at which band?"
  This is the band-drift guard.
- **NOT authoritative:** pinyin/gloss for some function words and polysemes. The
  source picks one dictionary sense, occasionally the wrong one for a beginner
  context. Examples found while authoring `greetings/`:
  - 吗 → listed as `má` "(coll.) what?"; the question particle is neutral-tone `ma`.
  - 也 → `Yě` "surname Ye"; the adverb "also" is `yě`.
  - 累 → `lěi` "to accumulate"; the "tired" sense is `lèi`.
  So `vocab.md` carries **curated** pinyin/gloss; the validator uses this file
  only for the membership check, and `_tools/annotate_pinyin.py` derives dialogue
  pinyin from the curated `vocab.md`, not from this list.

## Compounds

Common greetings like 你好, 您好, 早上好, 美国 are **not single entries** — they
compose from in-band single words (你+好, etc.). The validator must accept a
compound when every component morpheme is in-band, not require the whole string
to be a list entry.
