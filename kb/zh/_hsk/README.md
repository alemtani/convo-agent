# HSK 3.0 reference index (source of truth for band membership)

`hsk-3.0.json` is the **authoritative band-membership index** that every
`vocab.md` is checked against (see `CLAUDE.md` / `DESIGN.md`: vocab is *verified*
against a public HSK list, not recalled from memory). The topic-generator agent
*selects* vocab from this file; it never invents in-band words.

## Lean by design: `word → band`, nothing else

The file is a flat `{word: band}` map for **all HSK 3.0 bands (1–7)** — no pinyin,
gloss, or POS. Reasons:

- The list is **not trusted** for pinyin/gloss anyway (one dictionary sense, often
  the wrong one for a beginner — see quirks below), so `vocab.md` curates those.
- Validation only needs membership + band, so the rich fields were dead weight
  (~1.2 MB → ~120 KB).
- The agent fetches *richer* data (gloss for drafting) **on demand for the ~30
  words a topic actually uses**, never the whole corpus locally.

It is **authoring-time only** — never shipped to the client or read at runtime —
so it stays small and doubles as the offline fixture for validation tests.

## The ceiling is universal, not per-band-in-the-file

The file holds every band so the data is never the bottleneck. *Which* bands are
fair game is `config.HSK_BAND_CEILING` (the learner's current level), applied
uniformly across all topics — raise it once and every topic may use higher-band
words. A topic's own highest band is *derived* from its vocab, not authored.

## Re-runnable and pinned

`build.py` regenerates `hsk-3.0.json` from the upstream wordlists, pinned to a
specific commit so a word can't silently drift between bands under us:

```bash
python build.py               # all bands (default)
python build.py --max-band 2  # materialize only bands 1–2, for a leaner file
```

- Source: [`drkameleon/complete-hsk-vocabulary`](https://github.com/drkameleon/complete-hsk-vocabulary)
  @ `7ac65bf` (pinned in `build.py`), `wordlists/exclusive/new/{1..7}.json`
  (HSK 3.0 / "new" standard; band 7 = the combined 7–9 advanced band).
- 10,969 words (band 1 = 506, band 2 = 750, …). Stdlib only (`urllib`).
- Bump the pin deliberately when you want to pick up upstream corrections.

## What it is and isn't authoritative for

- **Authoritative:** *membership* — "is this word in HSK 3.0, and at which band?"
  This is the band-drift guard.
- **NOT authoritative:** pinyin/gloss (dropped from the file). Examples found while
  authoring `greetings/` of why the source's readings can't be trusted:
  - 吗 → `má` "(coll.) what?"; the question particle is neutral-tone `ma`.
  - 也 → `Yě` "surname Ye"; the adverb "also" is `yě`.
  - 累 → `lěi` "to accumulate"; the "tired" sense is `lèi`.
  So `vocab.md` carries **curated** pinyin/gloss, and `_tools/annotate_pinyin.py`
  derives dialogue pinyin from the curated `vocab.md`, not from this list.

## Compounds

Common greetings like 你好, 您好, 早上好, 美国 are **not single entries** — they
compose from in-band single words (你+好, etc.). The validator must accept a
compound when every component morpheme is in-band, not require the whole string
to be a list entry.
