# The grader's measurement — is it crediting the right facts?

Started as `docs/VALIDITY.md` chunk V0, *can `coherence` carry a gate?*, and the
V0 history below is kept because the answer it gave shaped everything after it.

**What this directory measures today is the grade**: which slots the grader
credits, against gold labels held apart from the transcripts. Coherence is the
partner's judgment since A4 (`docs/streams/grading.md`), and this runner holds
the partner still — so the tag is observed by `evals/turn/`, the runner that
runs a whole turn, and `matrix.confusion` scores it there.

## History: V0's question, and A4's answer

V1 proposed gating A2's credit floor on `coherence`. Before gating anything on
an unmeasured signal, V0 measured it — and reported that no threshold was safe,
so no gate shipped. A4 ships one anyway, and V0 is why it can: what V0 measured
was a **goal-aware** partner's tag. That partner could see what was scoreable,
so it called the gaming turn relevant. V2 made the partner goal-blind, which
retired the objection, and A4 asks the same question of that partner — binary,
`coherent` or not — and gates on the answer in
`orchestrator._advance_or_echo`.

The gate-selection code went with the question it answered. `matrix.py` no
longer searches candidate thresholds: the gate is chosen, and a recommender
with one candidate is not a measurement.

## The answer

**No** — on the converser. Across 21 runs over 7 cases, the partner tagged the
gaming turn `on_track` **every time** and credited `recommendation` every time.
No threshold over `coherence` separates that turn from the ones the learner
earned, because the tag does not notice it at all. Those numbers lived in
`RESULTS.md` until A1 re-ran the corpus through the grader; the file now holds
the V2 measurement instead.

The corpus was rebuilt once mid-measurement, after review found the fixtures
carried the session's opening line inside `dialogue` — a shape the live client
never sends, which shifts both the `messages` array and the pressure hint. The
headline survived the rebuild unchanged; the numbers here are from the corrected
corpus, and `test_every_case_carries_the_dialogue_shape_the_client_actually_sends`
now holds the wire shape in place.

That is the second of the two failures V0 could have found, and the milder one:
the signal is not *wrong*, it is silent. No gate suppressed an earned turn
either. So V1's gate would not have broken A2 — it simply would never have
fired, which is risk bought with no benefit.

The motivating turn stays exactly where `docs/VALIDITY.md` puts it: **V2's to
fix.** A partner that holds the rubric plays along, and no amount of reading its
own tag afterwards changes that.

## The baseline V2 has to beat

Scoring `slots_filled` against `gold.slots_established` — not the gate question,
the one underneath it: **is the tracker right?**

| | runs | result |
|---|---|---|
| Exactly right | 18/21 | every case except one |
| **Spurious** — credit not earned | **3** | `recommendation` on the gaming turn, all 3 runs |
| **Missed** — credit earned, not given | **0** | none on this corpus |

At V0 the whole tracker error on this corpus is that one turn, on every run —
the failure `docs/VALIDITY.md` describes, recorded before the grader existed so
it could not be marked against a standard written after it.

**A4 reframed what that number means.** The goal-blind grader still credits the
gaming turn, and under A4 that is the *right* answer: the grader is a pure
extractor, so it credits the fact the words state and leaves coherence to the
partner. Gold credits it now too (`slots_established: [recommendation]`), so slot
accuracy reads it as exact, not spurious. The gaming turn is caught by the gate
instead, and whether the partner tags it incoherent is scored in `evals/turn`.
V2 did not beat the V0 number by making the grader stricter; it dissolved it by
splitting the question in two.

**One thing this says to the accessibility track.** `missed` is zero, and
`earned-under-annotated` — the messy-pinyin turn built specifically to provoke
under-annotation — scored 3/3. That does **not** argue against A2's floor: the
under-annotation A2 exists to fix was observed in a real session, and a corpus
of seven authored cases failing to reproduce it is weak evidence about a rare
event. It does mean this corpus cannot currently show A2's floor earning its
keep, and a case drawn from the session where it actually happened would be
worth more than any case written from imagination.

## Layout

| File | What |
|---|---|
| `cases.py` | Load recorded cases and gold labels; refuse an unpaired set |
| `matrix.py` | Slot accuracy per case; the tag-vs-gold 2×2 the turn runner fills |
| `report.py` | Render the above as markdown |
| `replay.py` | Cassette runner — grades each case, writes `observations.json` |
| `RESULTS.md` | The rendered measurement |
| `../../tests/fixtures/sessions/` | The cases, and the labels held apart from them |

Pure logic is tested in `tests/test_coherence_eval.py`. Replay runs off
cassettes. A1's dense-turn cases assert slot credit against those recordings.

## A1 / A3 / A4

[`RESULTS.md`](RESULTS.md) is the **grader** on `claude-opus-5`, 55 runs over 11
cases, off committed cassettes. Replay calls `grader.grade`, not the converser.
Turn-1 fixtures carry `opening_line`.

A1 recorded three dense turns the grader under-credited; A3 rewrote the
`slots_filled` instruction and all three went green. They are asserted as a
**rate**, not a run: `DENSE_MIN_EXACT` of `DENSE_SAMPLES` draws must be exact
(`tests/test_coherence_eval.py`).

A4 took `coherence` out of this prompt. The corpus was re-recorded on the
slots-only grader and the dense cases held at 5/5 each. One case moved the wrong
way — `elliptical-ni-ne` misses `wellbeing` on 2 of 5 draws, where A3 measured
0 of 5 — and three separate 55-run waves showed it, so it is a shift and not one
unlucky draw. It is the smallest case in the corpus: 我很好，你呢？, where the
whole slot rides on 你呢 bouncing one question back.

The V0 gaming turn (`nonsequitur-slot-fill`) still credits `recommendation` on
every run — the right answer for a goal-blind extractor, so gold credits it too
and slot accuracy reads it as exact. Coherence is the separate question: gold
labels the turn incoherent, the gate blocks the credit before the learner sees
it, and the partner's tag is scored in `evals/turn`.

## Re-running it

```bash
source .venv/bin/activate
python -m evals.coherence.replay --repeat 5                      # free, off cassettes
python -m evals.coherence.replay --record --samples 5 --repeat 5  # live; costs money
python - <<'PY'
from evals.coherence.cases import load_cases, load_gold, paired
from evals.coherence.matrix import load_observations
from evals.coherence.report import render
gold = load_gold("tests/fixtures/sessions/gold.json")
paired(load_cases("tests/fixtures/sessions"), gold)
run = load_observations("evals/coherence/observations.json")
open("evals/coherence/RESULTS.md", "w").write(render(run.observations, gold, model=run.model))
PY
```

The model id travels with the observations so a stale matrix cannot be mistaken
for a current one.

## The labels, and who wrote them

`gold.json` is the label set. It lives apart from the transcripts so the party
who writes the cases need not be the party who judges them — the same separation
this whole track is about.

`gold.second-opinion.json` is a second labeller's pass (grok) over the original
seven transcripts. It agrees on all seven, on every field. The three A1 cases
were labelled with the cases, not independently.

**Read that agreement carefully.** The second labeller had repo access and said
it read `docs/VALIDITY.md` and the existing labels before answering, so this is
**corroboration, not independence**. It rules out a careless slip in the first
pass. It does not rule out a shared assumption, and a genuinely blind relabel —
transcripts only, no repo — would be worth more than this one is.
