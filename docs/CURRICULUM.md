# Curriculum — keeping practice in step with a syllabus

Where topics come from, what bounds them, and how that stays ergonomic as the KB
grows past one learner, one level, and one language.

Companion to [`DESIGN.md`](DESIGN.md) (architecture) and
[`SCENARIOS.md`](SCENARIOS.md) (the authored goal format). This doc owns one
question those two leave open: **how does the practice surface stay matched to
what the learner has been taught?**

---

## North star

Not urgent. Written down so the near-term work bends toward it rather than away.

### The learner opens the app and it already knows what to drill

The home screen is a **topic list**, not a hardcoded session. Each row carries
its state: covered or locked, how fresh (which unit taught it, when), how strong
(from the proficiency profile), how long since it was last practised, and a pin
to force it into the next draw.

What surfaces is a *mix* — this week's units, and the older topic the learner
keeps getting wrong. Neither because anything was configured.

And any row is tappable. The list is a recommendation with a reason attached,
never a gate.

This is `GET /api/topics` (#29) plus the profile layer, plus one action — *mark a
unit covered* — which is the only input the learner ever gives about their
syllabus.

### The KB is live-editable

Today a topic edit needs a process restart: `load_kb_block` is
`lru_cache(maxsize=None)` for the life of the process (`backend/kb.py:331`).

The hook for fixing this already exists. `DESIGN.md`'s DB row is
`topic_id → kb_path + content_hash`, and the hash is there precisely to detect
that the committed KB changed. Live editing is then: compare the hash, invalidate
that one cache entry.

The subtlety is not invalidation — it is the prompt cache. The KB block must be
**byte-identical across every turn of a session**. So the rule has to be: **a
session pins a `content_hash` at start and rides it to the end.** New content
applies to the *next* session. A learner mid-conversation never sees the ground
move, and an author never waits for a restart.

### An admin describes what they want and gets a reviewed PR

> "Add Japanese at JLPT N5, travel domain, eight topics."

The agent scaffolds `kb/ja/lang.json`, builds the lexicon from a pinned upstream
list, authors the topics with scenarios, runs the gate, and opens a PR with a
validation report attached. A human reads a diff, not a spec.

The agent never writes to a running server — see "Why the agent stays out-of-band".

### Practice is aimed at a purpose

The learner picks a purpose — family, travel, work — and the draw reweights.
Topics carry domain facets; a purpose is a saved filter plus a weighting profile.

---

## The problem

The learner studies elsewhere — a topic-structured app, roughly 50 units of 3–4
lessons each. This app is where they *apply* a unit. So the practice surface has
to move when the syllabus moves.

Today it doesn't. There is one topic, `greetings`, and the only scope signal is a
universal HSK band ceiling.

### Units and topics are not the same object

| | source of truth | changes when |
|---|---|---|
| **Unit** | the external app | the learner finishes a lesson |
| **Topic** | this repo, authored markdown | someone authors it |

`DESIGN.md` says the learner marks a *topic* covered. That is inverted. Learners
cover units, and units are not topics:

- Some units make no scenario — "Numbers 1–100", "Measure words". There is no
  goal to bound, and `validate.py` would rightly reject a one-slot drill.
- A good scenario usually needs vocab from several units. "Order food" wants
  numbers, money and politeness, taught weeks apart.

The mapping is **many-to-many and curated**. Topics stay authored around a
conversational goal. Units are a separate, lighter record.

---

## The scope rule

**The band ceiling stays the only hard bound.** Words outside the current unit
are fair game — the learner is trying to *communicate*, and a partner that
refuses every word not in this week's lesson is a worse partner. Recall also lags
exposure: a word met two units ago is still being learned.

**What the learner recently covered is a soft signal.** Two jobs, neither a filter:

1. **Selection.** Recently covered units raise the weight of topics using their
   vocab — the `freshness(covered_at)` term in `DESIGN.md`, finally with a data
   source. One term of four. See "Accumulation, not a queue" below.
2. **Preference.** The system prompt already separates a hard rule from a soft one
   (`backend/prompts.py:23`): *"Use **ONLY** vocabulary and grammar at or below
   HSK 3.0 band 2, and **prefer** words that appear in the topic knowledge base."*
   Covered vocab joins the **prefer** clause. Never the **ONLY** clause.

If the syllabus record is empty or stale, behaviour degrades to exactly today's.
That property is what lets the record stay hand-maintained and rough.

---

## Accumulation, not a queue

The syllabus framing above has a failure mode, and it is the worst one available:
building a system that only ever drills the newest unit. A learner who finishes
unit 14 does not stop needing unit 3. Weakness is the *reason to practise*;
recency is only a reason to practise **sooner**.

So, stated as invariants rather than left implicit in a formula:

1. **Covered is monotonic.** A topic that has ever been covered stays fair game
   forever. Nothing ages out of the pool, ever. (`DESIGN.md` says this; it is
   restated here because the syllabus record makes recency newly tempting.)
2. **Weakness outranks freshness in the long run.** `freshness(covered_at)` decays
   — roughly two weeks. `w_weak · (1 − derived_strength)` does not decay; it
   persists until the learner actually improves. A topic the learner is bad at
   should keep resurfacing after the new-unit excitement has faded, and should be
   able to *outweigh* a freshly covered topic the learner is already good at.
3. **Staleness is a third, independent pull.** `w_stale · staleness(last_practiced)`
   catches the topic that is neither new nor known-weak but simply hasn't come up.
   Without it, a topic that was strong six months ago never returns and the
   strength score silently goes stale with it.
4. **Manual choice always wins.** The learner can pick any covered topic directly,
   and it runs. The weighted draw is the *default*, not a gate. Deliberate review —
   "I want to redo greetings" — must never require beating an algorithm.

### What this means for the four terms

```
weight(topic) =
    w_weak  · (1 − derived_strength)      # persistent — the main long-run driver
  + w_fresh · freshness(covered_at)        # decays ~2wks — "drill what I just learned"
  + w_stale · staleness(last_practiced)    # grows — nothing gets forgotten
  + w_focus · pinned_this_session          # the learner's explicit override
```

The syllabus record (C1) feeds exactly one of these — `w_fresh`. It is the term
with a decay on it, deliberately. The other three are what make the app a
practice tool rather than a homework queue.

**A weak topic covered months ago must be able to beat a strong topic covered
yesterday.** That is a testable property, not a vibe, and C3 should test it.

---

## Level — the abstraction that has to survive

This is the part most at risk of being painted into a corner, so it gets stated
in full.

### "Beginner" is currently pinned in eight places, and only one is a number

The intent in `DESIGN.md` is that difficulty is one universal dial: raise
`band_ceiling` and higher-band vocab unlocks everywhere. That is not what the code
does. An audit:

| # | Where | What it pins | Moves with `band_ceiling`? |
|---|---|---|---|
| 1 | `kb/zh/_hsk/ceiling.json` | `band_ceiling: 2` | it *is* the number |
| 2 | `kb/zh/_tools/validate.py:267` | rejects out-of-band vocab at author time | ✅ yes |
| 3 | `backend/config.py:148` | `HSK_BAND_CEILING` | loaded — **and never read** |
| 4 | `backend/prompts.py:19,23` | "beginner learner (HSK 3.0, bands 1–2)", "at or below HSK 3.0 band 2" | ❌ string literal |
| 5 | `backend/prompts.py:25` | "ONE short sentence. The learner answers in 2–4 words" | ❌ |
| 6 | `backend/prompts.py:29–35` | the whole "may not be able to type 汉字" pinyin-tolerance paragraph | ❌ |
| 7 | `kb/zh/pacing.json` | "the band-1-2 partner elicits about one fact per turn" | ❌ |
| 8 | `backend/config.py:103` | `TTS_RATE_PCT = -10` — "a band-1–2 learner cannot segment it" | ❌ |

Row 3 is the sharp one. **`config.HSK_BAND_CEILING` is defined and read by
nothing.** `grep` finds one assignment and zero uses. The prompt states "band 2"
as its own literal. So raising the ceiling today changes what `validate.py`
*accepts* and not one word of what the partner is *told*. Filed as its own issue.

The broader point stands regardless: raising a band number from 2 to 4 would not
produce an intermediate app. The partner would still speak one short sentence,
still expect 2–4 word answers, still assume the learner can't type 汉字, still
talk at −10% rate, and the turn cap would still be paced for one fact per turn.

### So level is two axes, not one

| axis | question | what it controls |
|---|---|---|
| **Band** | *which words* are fair game | vocab scope, at author time and in the prompt's hard rule |
| **Stage** | *how hard the conversation* is | reply length, expected learner turn length, pacing coefficients, speech rate, whether romanized input is expected |

They move together in practice and are authored separately. Conflating them is
what makes "raise one number" a lie. Rows 5–8 above are all **stage**, and none of
them belongs in a band.

A **stage** should be a small named bundle — `beginner`, `intermediate`,
`advanced` — carrying exactly those settings. Then "the learner levelled up" is
two edits, band and stage, and both are data.

### Band generalizes as a level index into an ordered lexicon

The service does not need to know what "HSK" means. It needs two things: *is this
word at or below the learner's level*, and *how do I name the level in a prompt*.

That makes HSK 3.0 one instance of a general shape:

| language | level system | lexicon |
|---|---|---|
| zh | HSK 3.0, bands 1–9 | `word → band` |
| ja | JLPT, N5–N1 | `word → level` |
| es / fr / de | CEFR, A1–C2 | `word → level` |

Every language ships a **lexicon** (`word → rank`) and an **ordered level list**.
A ceiling is a value in that ordering. `validate.py` compares ranks; the prompt
renders the level's display name. Neither cares which system it is.

```jsonc
// kb/<lang>/lang.json
{
  "language": "zh",
  "display_name": "Mandarin Chinese",
  "levels": { "system": "HSK 3.0", "ordered": ["1","2","3","4","5","6","7-9"] },
  "lexicon": "_lexicon/hsk-3.0.json",   // word -> one of `ordered`
  "romanization": "pinyin",              // or null — not every language wants one
  "speech": { "stt_locale": "zh-CN", "tts_voice": "zh-CN-XiaoxiaoNeural" }
}
```

And the learner carries `{ language, level: {band, stage} }` — universal today
(single user), per-user when the profile lands, which `DESIGN.md` already
anticipates with first-class `user_id` / `language` columns.

### What is genuinely language-specific

| piece | today | generalizes to |
|---|---|---|
| leveled wordlist | `kb/zh/_hsk/hsk-3.0.json` | `kb/<lang>/_lexicon/` |
| ceiling semantics | "HSK 3.0 band" | a rank in `levels.ordered` |
| romanization | `backend/pinyin.py` (pypinyin) | per-language, or none |
| prompt literals | "HSK 3.0, bands 1–2" | rendered from the manifest |
| speech | `TTS_VOICE`, STT locale | manifest fields |
| KB root | `KB_ROOT = kb/zh` (`backend/kb.py:24`) | `kb/<lang>` |
| tone scoring | `tones.py`, `typed_pinyin.py` | tonal languages only — a capability flag, not a given |

The last row is worth flagging: tone assessment is not universal. For a
non-tonal language that whole path is inert, so it has to be a declared
capability rather than an assumed stage of the turn.

---

## The syllabus record

One file, one row per unit, added by hand or by an agent in a session.

```
kb/zh/_syllabus.md
```

A row carries: the unit's name, the date finished, and a handful of words worth
drilling. **Not** an exhaustive word list — extracting one per unit is a chore
with a poor payoff, given the words only ever feed a preference.

Deliberately not built now:

- **No in-app "mark covered" screen** — needs the DB layer (Phase 7). It arrives
  with the topic list in the north star, not before.
- **No import from the external app** — there is no public machine-readable lesson
  index, so any importer would be scraping or transcription.
- **No per-unit exact vocabulary** — see above.

The front door is a Claude Code session: say which unit was finished, the
`kb-topic` skill appends the row and authors or extends the affected topics, and
it ships as a PR like every other change.

---

## Why the agent stays out-of-band

The agent is the easy half of automated authoring. The half that makes it safe
already exists: `validate.py` is a machine-checkable acceptance test for KB
content — vocab membership, band, scenario achievability, dialogue scope, the two
guardrail rules.

**The gate is the product, not the agent.** So it gets hardened before any
automation, not after.

The agent **opens a PR; it does not write to a running server**:

- It preserves the one-way coupling — authoring tools may import `backend`, never
  the reverse (`CLAUDE.md`).
- It keeps the KB git-versioned, which is what the
  `topic_id → kb_path + content_hash` pointer assumes.
- Generated content wants review anyway.

Note this is compatible with the live-editable KB in the north star. Live editing
invalidates a cache when the *committed* content hash changes. It does not make
the server an author.

---

## Purpose

Family / travel / work is a **facet on a topic**, not a second directory tree.
"Order food" belongs to travel *and* daily life; a tree forces a false pick and
duplicates the KB.

Use the CEFR's four domains of language use — **personal, public, occupational,
educational** — rather than inventing a taxonomy. They are the standard vocabulary
and carry descriptor sets already.

One optional frontmatter field. A purpose is then a saved filter plus a weighting
profile, reusing the `w_focus` pin already in `DESIGN.md`'s selection weight.

---

## The first slate, and where it came from

Stage 0 needed an actual list of topics. Picking them by taste would have been
faster and worse — the point of a topic is that it matches something the learner
was taught, so the slate has to answer to a syllabus, not to an author's sense of
what makes a fun scene.

**The rule: take the intersection of the two courses a beginner is most likely to
be using.** This learner uses HelloChinese. Most people use Duolingo. A topic
both courses teach early is a topic almost any beginner arrives already holding
vocabulary for — and one neither teaches is a topic where the app would be
teaching rather than giving practice, which is not what it is for.

The two courses map differently, and that difference is the useful part:

| | HelloChinese | Duolingo Chinese |
|---|---|---|
| structure | HSK-aligned, situational units | thematic, loosely ordered |
| maps onto our band ceiling | directly — it *is* HSK | approximately |
| use it for | which topic, and roughly when | a sanity check that the topic is common |

So HelloChinese decides the slate and Duolingo vetoes anything idiosyncratic.

| topic | theme in both courses | authored |
|---|---|---|
| `greetings` | greetings, names | ✅ |
| `self-intro` | nationality, language, occupation | ✅ |
| `family` | family members, ages | ✅ |
| `numbers-money` | numbers, prices, shopping | ✅ |
| `food-ordering` | food, restaurant | ✅ |
| `time-date` | days, dates, making plans | — |
| `weather` | weather, seasons | — |
| `directions` | places, transport, asking the way | — |

The unit *names* above are by theme, not by unit number. Neither course
publishes a stable machine-readable index, and a wrong number is worse than no
number — so this table is deliberately not precise about ordering. `_syllabus.md`
(C1) is where real unit records go, entered by hand as they are finished.

### Themes are more portable than the courses that teach them

Being imprecise about ordering turns out to be the more general choice, not just
the safer one. Greet, introduce yourself, count, buy something, order food, ask
the way — beginner courses converge on roughly that sequence regardless of the
language, because it is ordered by *what a beginner can do with it*, not by
anything specific to Mandarin.

That is worth stating because it changes what a topic slate *is*. If the ordering
were an artifact of HelloChinese, then adding a language (C5) would mean
researching a new course and deriving a new slate. If it is a property of
beginners, the slate largely ports: a JLPT N5 or CEFR A1 course wants the same
eight scenes, with different vocabulary underneath them and different scenario
details on top.

So the split holds at every level of this doc. **The scene is portable; the
lexicon is not.** `lang.json` and the ordered level list carry the part that
differs; the topic slate is closer to a constant. Which means the admin request
in the north star — *"add Japanese at JLPT N5, travel domain, eight topics"* — is
more plausible than it sounds, because "eight topics" is not eight unsolved
authoring problems. It is eight known scenes needing a new lexicon.

Unverified against an actual N5 or A1 syllabus. Recorded as a claim to check when
a second language is real, not as a finding.

### The easy topics are the hard ones

The last three are unauthored on purpose: they are the ones where the guardrail
bites. `validate.py` requires more than one slot and at least one `request` slot,
and "say it is cold" satisfies neither — it is a vocabulary drill with weather
words in it.

The fix that works for all three is the same shape: **two things to find out,
plus one to tell.** "Find out tomorrow's weather and whether it will be cold,
then say what you will wear." That is a scenario. "Describe the weather" is a
flashcard.

This is the rules working. Budget authoring time for it — the topics that look
easiest need the most thought to have a shape at all, which is the opposite of
the intuition.

### What authoring five topics actually taught

Findings from the first batch, kept because they will recur:

- **The obvious word is often out of band.** 年纪 (3), 苹果 (3), 咖啡 (3), 附近
  (4), 前面/后面 (3), 伞 (4) — every one of them the first word an author reaches
  for. Check the index before building a slot around a noun, not after.

  **The fix is not to raise the ceiling.** That is the tempting move and it does
  not work yet: `config.HSK_BAND_CEILING` is read by nothing, and `prompts.py`
  states the level as a string literal in four places, so raising the number
  changes what `validate.py` *accepts* and not one word of what the partner is
  *told*. You would get a KB that legally contains 苹果 and a partner instructed
  never to say it — strictly worse than today, where the two agree. That is
  stage **C0**, and it has to land before a bump means anything.

  The deeper reason is that the ceiling is a claim about *the learner*, not an
  authoring convenience. Band 3 adds 953 words; raising it to dodge one noun
  asserts the learner knows all of them, in every topic at once. The evidence
  from this batch is that band 2 was not the real constraint — five topics
  validated clean with 53–69 target words each, and nothing was blocked. Words
  were substituted: 多大 for 年纪, 水果 for 苹果.
- **A phrase row has two independent ways to fail**, and telling them apart is
  what tells you how to fix it. `in_band` tries the whole word first, and falls
  back to every character only if the phrase is not a list entry at all:
  - **The phrase is itself a high-band entry.** 有没有 is a band-6 word, so it
    fails on the whole-word lookup — even though 有 and 没有 are both band 1. No
    amount of decomposition helps. Patterns like A-not-A belong in `grammar.md`
    prose instead, where the dialogue tokenizer splits them anyway.
  - **The phrase is not listed, and a character is out of band.** 便宜一点儿 is
    not an entry, so each character is checked, and 便 is band 6. Note that
    便宜 *alone* is band 2 and perfectly fine — the phrase failed, not the word.
    Shorten the row rather than abandoning the vocabulary.
- **`annotate_pinyin.py` gets 不/一 sandhi wrong** when the word is in the
  curated vocab table: the lexicon entry wins over pypinyin's context-aware
  reading, so it emits `bù` before a fourth tone and `yī gè` for 一个. Rephrase
  the line rather than hand-correcting generated pinyin — the generated line is
  the invariant.
- **Tests that assert the KB's inventory break on the second topic.** Two
  `test_orchestrator.py` tests hardcoded "greetings is the only topic". Assert
  the *shape* of a scan, never its contents.

## Staging

Each stage is usable alone. Nothing here blocks the MVP.

| Stage | What | Depends on |
|---|---|---|
| 0 | Author topics by hand; find out what actually hurts | — (this is #29) |
| C0 | Fix the dead `HSK_BAND_CEILING`: prompt reads the ceiling | — |
| C1 | `_syllabus.md` + `kb-topic` records a finished unit | 0 |
| C2 | Covered vocab as a soft preference in the prompt | C1 |
| C3 | Selection weighting reads the syllabus | C1, profile/DB |
| C4 | Harden `validate.py` into the generation gate + eval set | 0 |
| C5 | Split band from stage; `lang.json` + lexicon contract | C0, C4 |
| C6 | Domain facet + purpose filters | C3 |
| C7 | Live-editable KB — session pins a `content_hash` | C1 |
| C8 | In-app topic list — covered state, weights, pin, mark-covered | C3, C7 |
| C9 | Admin front door — skill behind an endpoint, opens PRs | C4, C5 |

C1–C3 serve the single learner. C4–C5 make extension safe. C7–C9 are the north
star, and they are cheap *only if* C5 lands first.

### The ordering argument

**Stage 0 first, because the format is the risk.** Six hand-authored topics will
teach more about what a topic *is* than any tooling built in advance — the same
reasoning that moved #29 to last within M2.

**C4 before any automation.** An authoring agent without a machine gate produces
content nobody can trust and everybody must read.

**C5 before the KB gets large.** It is a refactor with no user-visible change
until a second language or level exists. Its value is that it stops `zh` and
`band 2` leaking further with every topic added. Cheap now, expensive at 20
topics.

---

## Open threads

- **Freshness decay.** `DESIGN.md` guesses ~2 weeks. Untested. It is the decay
  that stops the app becoming a recency queue, so it matters more than its
  offhand tone suggests — too slow and new units crowd out weak old ones for
  months.
- **The four coefficients are unset.** `w_weak`, `w_fresh`, `w_stale`, `w_focus`
  have no values yet. Their *relative* sizes encode the whole policy — in
  particular `w_weak` vs `w_fresh` decides whether weakness or recency wins after
  the decay. They want one home and a comment explaining the trade, the way
  `pacing.json` handles its coefficients.
- **Ceiling and syllabus can disagree.** A unit may teach a band-3 word before the
  ceiling rises. Current answer: the ceiling wins, the word drops from the
  preference set. Revisit if it bites.
- **How many stages?** Three (`beginner` / `intermediate` / `advanced`) is a
  guess. The honest version may be that stage is per-setting and the named bundles
  are just presets.
- **Topic count.** Selection weighting is invisible below ~5 topics and
  load-bearing above ~15. Where C3 stops being optional is unknown.
