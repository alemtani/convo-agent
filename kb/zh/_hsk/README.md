# HSK 3.0 reference wordlist (source of truth for band membership)

`band1-2.json` is the **authoritative band-membership list** that every
`vocab.md` is checked against (see `CLAUDE.md` / `DESIGN.md`: vocab is *verified*
against a public HSK list, not recalled from memory). The topic-generator agent
*selects* vocab from this file; it never invents in-band words.

## Provenance

- Source: [`drkameleon/complete-hsk-vocabulary`](https://github.com/drkameleon/complete-hsk-vocabulary),
  `wordlists/exclusive/new/{1,2}.json` (HSK 3.0 / "new" standard).
- Distilled to `{word: {pinyin, gloss, pos, band}}` for the 1,256 words in
  bands 1–2 (band 1 = 506, band 2 = 750 new).

## What it is and isn't authoritative for

- **Authoritative:** *membership* — "is this word in HSK 3.0 band 1–2?" This is
  the band-drift guard.
- **NOT authoritative:** pinyin/gloss for some function words and polysemes. The
  source picks one dictionary sense, which is occasionally the wrong one for a
  beginner context. Known examples found while authoring `greetings/`:
  - 吗 → listed as `má` "(coll.) what?"; the question particle is neutral-tone `ma`.
  - 也 → `Yě` "surname Ye"; the adverb "also" is `yě`.
  - 累 → `lěi` "to accumulate"; the "tired" sense is `lèi`.
  So `vocab.md` carries **curated** pinyin/gloss; the validator uses this file only
  for the membership check.

## Compounds

Common greetings like 你好, 您好, 早上好, 美国 are **not single entries** — they
compose from in-band single words (你+好, etc.). The validator must accept a
compound when every component morpheme is in-band, not require the whole string
to be a list entry.
