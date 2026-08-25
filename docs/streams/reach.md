# Stream C — Reach

**The question:** can somebody who is not the author use this, in a language that
is not Mandarin?

Three things stand between here and that: a second language pack, real accounts,
and an app a person installs.

## Order inside the stream

Spanish first, auth second, shell third. Spanish is the one that finds the bugs —
every place the code assumes Mandarin is a place a second learner would have hit
anyway. Auth and the shell are known work with known shapes.

C is gated on Stream A. A grader that under-credits in Mandarin will under-credit
in Spanish, and the fix would then land twice.

## C1 — Spanish

The claim in `CLAUDE.md` is "build for one, design for many": `language` is a
first-class column. This is the test of that claim.

### The seams to find

- `kb/zh/` → the tree is language-scoped by directory already. `kb/es/` is the
  shape.
- `kb/zh/_hsk/ceiling.json` — HSK is a Mandarin standard. Spanish needs CEFR.
  Issue #48 (`C5: split band from stage; lang.json manifest + lexicon contract`)
  is exactly this seam and it is now on the critical path.
- `pinyin.py` / `typed_pinyin.py` — romanization is a Mandarin problem. Spanish
  is already written in the alphabet the learner types. The pipeline must allow a
  language with no romanization step rather than special-case it.
- `tones.py` — tone errors are Mandarin. Spanish PA returns phoneme accuracy and
  no tones. `tone_errors` becomes one member of a general "pronunciation errors"
  idea, or stays Mandarin-only behind a capability flag.
- Azure locale — `es-ES` or `es-MX`, and the choice matters for PA scoring.
- Prompts — the partner's band rule reads "HSK 3.0 band 2". That is a per-language
  string, not a constant.

### The five topics

Same five scenarios: greetings, self-introduction, family, numbers and money,
food and ordering. Same slot structure. Different vocabulary, grammar, and scene.

**Authored by the `kb-topic` skill, not by hand.** This is the point. The skill
was built for Mandarin; a second language is the only honest test of whether it
generalises. If the skill cannot author Spanish, fix the skill — do not write the
markdown yourself and call the stream done.

`validate.py` must pass on `kb/es/**` with the same scenario guardrails.

### Evals

The cassette suite from A0 gets a Spanish case set. Grading accuracy in Spanish
is reported separately from Mandarin. A single blended number would hide a broken
language.

## C2 — Auth

Today: a passcode (`backend/auth.py`, 104 lines).

Needed: accounts, so `user_id` stops being a default and the covered-set and
proficiency state in `db.py` (Phases 7–8, still unwritten) have somebody to
belong to.

Decide early, because it shapes the shell:

- Hosted identity (Auth0, Clerk, Supabase) versus rolling sessions here.
- Anonymous-first — a learner practises immediately and claims the account later
  — versus a sign-up wall.

Recommendation: hosted identity, anonymous-first. A language app that demands a
password before the first sentence loses the learner at the door, and the
practice loop already works with no account at all.

Keys stay server-side. That rule does not bend.

## C3 — The mobile shell

The frontend is already a mobile-first PWA and the target device is mobile
Safari.

Two routes:

- **Stay a PWA.** Add-to-home-screen, a manifest, an offline shell. Cheapest.
  No store review. Weakest on iOS: background audio and push are limited, and
  installation is a thing users must be told how to do.
- **Wrap it** (Capacitor). A real install, a store listing, real mic permissions.
  Costs a build pipeline, Apple Developer enrolment, and review time.

Recommendation: PWA first, wrap only if the mic or install flow proves it
necessary. Decide from a real phone session, not from a table.

Either way the safe-area, autoplay and `AudioContext` unlock behaviour on mobile
Safari needs checking on a real device. The smoke suite is desktop Chromium and
does not see any of it.

## Done when

- Five Spanish topics, authored by the skill, passing `validate.py`.
- Grading accuracy reported per language.
- A learner signs in, and their progress is theirs.
- The app installs on a phone and a full session runs on it.

## Kickoff prompt

```
Read docs/streams/reach.md. Start Stream C at C1: Spanish.

Do not author markdown by hand — the kb-topic skill is the only writer, and a
second language is the test of whether that skill generalises. If it cannot
author Spanish, fix the skill.

Start by finding the seams, before writing any content: the HSK ceiling
(kb/zh/_hsk/ceiling.json) is a Mandarin standard and Spanish needs CEFR — issue
#48 covers that split; pinyin.py has no Spanish equivalent and the pipeline must
allow a language without a romanization step; tones.py is Mandarin-only and
tone_errors needs a general shape; the partner prompt hardcodes "HSK 3.0 band 2".

Report the seams and a proposed lang.json contract first. Do not start writing
kb/es/ until that lands. Branch from main, conventional commits.
```
