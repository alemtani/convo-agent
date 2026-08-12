# Curriculum — keeping practice in step with a syllabus

How topics enter the app, what bounds them, and how that stays ergonomic as the
KB grows past one learner and one language.

Companion to [`DESIGN.md`](DESIGN.md) (architecture) and
[`SCENARIOS.md`](SCENARIOS.md) (the authored goal format). This doc owns one
question those two leave open: **where do topics come from, and what keeps them
matched to what the learner has actually been taught?**

---

## The problem

The learner studies elsewhere — a topic-structured app (HelloChinese), roughly
50 units of 3–4 lessons each. This app is where they *apply* a unit. So the
practice surface has to move when the syllabus moves.

Today it doesn't. There is one topic, `greetings`. The only scope rule is a
universal HSK band ceiling (`kb/zh/_hsk/ceiling.json` → `config.HSK_BAND_CEILING`).
Band 2 is about 600 words, most of which the learner has not met. So "practice
what I just learned" is approximate at best.

### Two objects, currently conflated

| | source of truth | changes when |
|---|---|---|
| **Unit** | the external app | the learner finishes a lesson |
| **Topic** | this repo, authored markdown | someone authors it |

`DESIGN.md` says the learner marks a *topic* covered. That is inverted. Learners
cover units. Units are not topics:

- Some units make no scenario at all — "Numbers 1–100", "Measure words". There is
  no goal to bound, and `validate.py` would rightly reject a one-slot drill.
- A good scenario usually needs vocab from several units. "Order food" wants
  numbers, money, and politeness, taught weeks apart.

So the mapping is **many-to-many and curated**, not one-to-one. Topics stay
authored around a conversational goal. Units are a separate, lighter record.

---

## The scope rule

**The HSK band ceiling stays the only hard bound.** Unchanged. Words outside the
current unit are fair game — the learner is trying to *communicate*, and a partner
that refuses every word not in this week's lesson is a worse partner. Recall also
lags exposure: a word met two units ago is still being learned.

**What the learner recently covered is a soft signal.** It does two jobs, neither
of them a filter:

1. **Selection.** Recently covered units raise the weight of the topics that use
   their vocab. This is the `freshness(covered_at)` term already specified in
   `DESIGN.md` — it just gains a real data source.
2. **Preference.** The system prompt already separates a hard rule from a soft
   one (`backend/prompts.py`): *"Use ONLY vocabulary and grammar at or below HSK
   3.0 band 2, and prefer words that appear in the topic knowledge base."* The
   covered set joins the **prefer** clause. It never joins the **ONLY** clause.

The consequence worth stating: if the syllabus record is empty or stale, behaviour
degrades exactly to today's — ceiling only. Nothing breaks. That property is what
lets the record stay hand-maintained and rough.

---

## The syllabus record

One file, one line per unit, added by hand or by an agent in a session.

```
kb/zh/_syllabus.md
```

A row carries: the unit's name, the date it was finished, and a handful of words
worth drilling. Not an exhaustive word list — extracting one per unit is a chore
with a poor payoff, given the words only ever feed a *preference*.

Deliberately not built now:

- **No in-app "mark covered" screen.** It needs the DB layer (Phase 7) and adds
  a surface to maintain. A file edit is cheaper and auditable.
- **No import from the external app.** There is no public machine-readable lesson
  index for HelloChinese, so any importer would be scraping or transcription.
- **No per-unit exact vocabulary.** See above.

The front door is a Claude Code session: say which unit was finished, the
`kb-topic` skill appends the row and authors or extends the affected topics, and
it ships as a PR like every other change.

---

## Why the agent stays out-of-band

The long-term vision is an admin who prompts an agent to add a language, topic, or
scenario. The agent is the easy half. The half that makes it safe already exists:
`kb/zh/_tools/validate.py` is a machine-checkable acceptance test for KB content —
vocab membership, band, scenario achievability, dialogue scope, the two guardrail
rules.

So the gate is the product, not the agent. Every step toward automated authoring
is an extension of the validator.

The agent should **open a PR, not write to a running server**:

- It preserves the repo's one-way coupling — authoring tools may import
  `backend`, never the reverse (`CLAUDE.md`).
- It keeps the KB git-versioned, which is what `DESIGN.md`'s
  `topic_id → kb_path + content_hash` pointer assumes. A runtime writer would
  break `load_kb_block`'s process-lifetime `lru_cache` and the "never markdown
  blobs in the DB" rule at once.
- Generated content wants review anyway.

A live-editable KB and an in-app topic list are real improvements. They are not
urgent, and neither is on this path's critical line.

---

## Two axes the design should not foreclose

### Language

Adding a second language is smaller than it looks, but not free. What is actually
language-specific:

| piece | today | generalizes to |
|---|---|---|
| leveled wordlist | `kb/zh/_hsk/hsk-3.0.json` | `kb/<lang>/_lexicon/` — a `word → level` map |
| ceiling semantics | "HSK 3.0 band" | a level index into that map |
| romanization | `backend/pinyin.py` (pypinyin) | a per-language transliterator, or none |
| prompt literals | "HSK 3.0, bands 1–2" in `prompts.py` | rendered from the manifest |
| speech | `TTS_VOICE=zh-CN-XiaoxiaoNeural` | per-language voice + STT locale |
| KB root | `KB_ROOT = kb/zh` | `kb/<lang>` |

The generalization is a `kb/<lang>/lang.json` manifest plus a generic
`word → level` lexicon contract, of which HSK 3.0 becomes one instance. `user_id`
and `language` are already first-class columns in the schema (`DESIGN.md`), so the
DB side is additive.

### Purpose

"Learning for family / travel / work" is a **facet on a topic**, not a second
directory tree. Use the CEFR's four domains of language use — *personal, public,
occupational, educational* — rather than inventing a taxonomy; they are the
standard vocabulary and they already have descriptor sets attached.

A purpose is then a saved filter over topics plus a weighting profile. It reuses
the `w_focus` pin already in `DESIGN.md`'s selection weight. One optional
frontmatter field, no schema change.

---

## Staging

Each stage is usable alone. Nothing here blocks the MVP.

| Stage | What | Depends on |
|---|---|---|
| 0 | Author topics by hand; find out what actually hurts | — (this is #29) |
| 1 | `_syllabus.md` + `kb-topic` records a finished unit | 0 |
| 2 | Covered-vocab preference in the prompt | 1 |
| 3 | Selection weighting reads the syllabus | 1, profile/DB (Phase 7) |
| 4 | Harden `validate.py` into the generation gate + eval set | 0 |
| 5 | Parameterize language (`lang.json`, lexicon contract) | 4 |
| 6 | Domain facet + purpose filters | 3 |
| 7 | Admin front door — skill behind an endpoint, opens PRs | 4, 5 |

Stages 1–3 serve the single learner. Stages 4–7 serve extension, and 4 is the one
that makes the rest safe.

### The honest ordering argument

Stage 0 comes first because the format is the risk. Six hand-authored topics will
teach us more about what a topic *is* than any amount of tooling built in advance —
the same reasoning that moved #29 from second to last within M2.

Stage 4 comes before any automation, not after. An authoring agent without a
machine gate produces content nobody can trust and everybody must read.

---

## Open threads

- **Freshness decay.** `DESIGN.md` says ~2 weeks. Untested; needs real use.
- **Ceiling and syllabus can disagree.** A unit may teach a band-3 word before the
  ceiling rises. Current answer: the ceiling wins, the word is dropped from the
  preference set. Revisit if it bites.
- **Topic count.** Selection weighting is invisible below ~5 topics and load-bearing
  above ~15. The point at which Stage 3 stops being optional is unknown.
