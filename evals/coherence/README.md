# V0 — can `coherence` carry a gate?

`docs/VALIDITY.md`, chunk V0. **Ships no gate.** Its output is a measurement and
a recommendation, and *"no threshold is safe"* is a valid one.

The question: `WorkerAnnotation.coherence` (`backend/models.py:301`) has been
computed on every turn since the conversation worker shipped and read by no code
path. V1 proposed gating A2's credit floor on it. Before gating anything on an
unmeasured signal, measure it.

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

The whole tracker error on this corpus is the one failure `docs/VALIDITY.md`
describes, and it is not occasional — it is **every single run**. That is the
number V2's goal-blind grader has to move, and it is recorded here *before* the
grader exists, so the grader cannot be marked against a standard written after
it.

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
| `matrix.py` | Tag-vs-gold confusion, candidate gates, the recommendation |
| `report.py` | Render the above as markdown |
| `replay.py` | Cassette runner — grades each case, writes `observations.json` |
| `RESULTS.md` | The rendered measurement |
| `../../tests/fixtures/sessions/` | The cases, and the labels held apart from them |

Pure logic is tested in `tests/test_coherence_eval.py`. Replay runs off
cassettes. A1's dense-turn cases assert slot credit against those recordings.

## A1 / the V2 re-run

[`RESULTS.md`](RESULTS.md) is now the **grader** on `claude-opus-5`, 30 runs
over 10 cases, off committed cassettes. Replay calls `grader.grade`, not the
converser. Turn-1 fixtures carry `opening_line`.

The slot misses A3 has to clear:

| case | missed |
| --- | --- |
| `milk-and-biscuits` | `order` ×3 |
| `computer-work-ni-ne` | `partner_origin` ×3 |

`clip-and-tea` already credits `order` on this grader. The V0 gaming turn
(`nonsequitur-slot-fill`) is now tagged `drifting` and still credits
`recommendation` — a different signal from the converser, and A4's problem.

## Re-running it

```bash
source .venv/bin/activate
python -m evals.coherence.replay --repeat 3            # free, off cassettes
python -m evals.coherence.replay --record --samples 3  # live; costs money
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
