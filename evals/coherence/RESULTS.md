# `coherence` against gold labels

Model: `claude-opus-5`. 30 runs over 10 cases.

## Tag vs gold

| gold \ observed | on_track | drifting | off_track |
|---|---|---|---|
| **on_track** | 21 | 0 | 0 |
| **drifting** | 0 | 5 | 1 |
| **off_track** | 3 | 0 | 0 |

## Candidate gates

| gate | gaming blocked | rescues suppressed | safe | useful |
|---|---|---|---|---|
| `on_track` | 3/3 | 0/21 | yes | yes |
| `on_track | drifting` | 0/3 | 0/21 | yes | no |
| `on_track | drifting | off_track` | 0/3 | 0/21 | yes | no |

## Recommendation

**Recommended gate: `on_track`.** It blocks 3/3 wrongly credited runs and suppresses none of 21 earned ones.

## Slot accuracy

Does the tracker credit the facts the learner actually established? **Spurious** is credit they did not earn — the failure this track exists to remove. **Missed** is credit they earned and did not get — the failure A2's floor exists to rescue. This is the metric V2's grader has to beat, and these numbers are its baseline.

| case | exact | spurious | missed |
|---|---|---|---|
| clean-slot-fill | 3/3 | — | — |
| clip-and-tea | 3/3 | — | — |
| computer-work-ni-ne | 0/3 | — | `partner_origin` ×3 |
| derailed-input | 3/3 | — | — |
| earned-under-annotated | 3/3 | — | — |
| elliptical-ni-ne | 2/3 | `self_name` ×1 | — |
| milk-and-biscuits | 0/3 | — | `order` ×3 |
| nonsequitur-slot-fill | 0/3 | `recommendation` ×3 | — |
| order-after-answers | 3/3 | — | — |
| wandering-no-slot | 3/3 | — | — |
| **total** | **20/30** | **4** | **6** |

## Per-case runs

| case | gold | observed | slots filled |
|---|---|---|---|
| clean-slot-fill | on_track | on_track | recommendation |
| clip-and-tea | on_track | on_track | order |
| computer-work-ni-ne | on_track | on_track | self_job, partner_job |
| derailed-input | off_track | on_track ⚠️ | — |
| earned-under-annotated | on_track | on_track | self_name, partner_name |
| elliptical-ni-ne | on_track | on_track | self_name, wellbeing |
| milk-and-biscuits | on_track | on_track | recommendation, drinks |
| nonsequitur-slot-fill | drifting | drifting | recommendation |
| order-after-answers | on_track | on_track | order |
| wandering-no-slot | drifting | off_track ⚠️ | — |
| clean-slot-fill | on_track | on_track | recommendation |
| clip-and-tea | on_track | on_track | order |
| computer-work-ni-ne | on_track | on_track | self_job, partner_job |
| derailed-input | off_track | on_track ⚠️ | — |
| earned-under-annotated | on_track | on_track | self_name, partner_name |
| elliptical-ni-ne | on_track | on_track | wellbeing |
| milk-and-biscuits | on_track | on_track | recommendation, drinks |
| nonsequitur-slot-fill | drifting | drifting | recommendation |
| order-after-answers | on_track | on_track | order |
| wandering-no-slot | drifting | drifting | — |
| clean-slot-fill | on_track | on_track | recommendation |
| clip-and-tea | on_track | on_track | order |
| computer-work-ni-ne | on_track | on_track | self_job, partner_job |
| derailed-input | off_track | on_track ⚠️ | — |
| earned-under-annotated | on_track | on_track | self_name, partner_name |
| elliptical-ni-ne | on_track | on_track | wellbeing |
| milk-and-biscuits | on_track | on_track | recommendation, drinks |
| nonsequitur-slot-fill | drifting | drifting | recommendation |
| order-after-answers | on_track | on_track | order |
| wandering-no-slot | drifting | drifting | — |
