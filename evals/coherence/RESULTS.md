# `coherence` against gold labels

Model: `claude-sonnet-5`. 21 runs over 7 cases.

## Tag vs gold

| gold \ observed | on_track | drifting | off_track |
|---|---|---|---|
| **on_track** | 12 | 0 | 0 |
| **drifting** | 3 | 1 | 2 |
| **off_track** | 0 | 0 | 3 |

## Candidate gates

| gate | gaming blocked | rescues suppressed | safe | useful |
|---|---|---|---|---|
| `on_track` | 0/3 | 0/12 | yes | no |
| `on_track | drifting` | 0/3 | 0/12 | yes | no |
| `on_track | drifting | off_track` | 0/3 | 0/12 | yes | no |

## Recommendation

**No safe gate: every candidate never fires.** `coherence` tagged the wrongly credited runs the same way it tagged the earned ones, so no threshold over it separates them. A gate that never fires is risk bought with no benefit. V1 does not ship one on this evidence; the fix stays with V2.

## Per-case runs

| case | gold | observed | slots filled |
|---|---|---|---|
| clean-slot-fill | on_track | on_track | recommendation |
| derailed-input | off_track | off_track | — |
| earned-under-annotated | on_track | on_track | self_name, partner_name |
| elliptical-ni-ne | on_track | on_track | wellbeing |
| nonsequitur-slot-fill | drifting | on_track ⚠️ | recommendation |
| order-after-answers | on_track | on_track | order |
| wandering-no-slot | drifting | off_track ⚠️ | — |
| clean-slot-fill | on_track | on_track | recommendation |
| derailed-input | off_track | off_track | — |
| earned-under-annotated | on_track | on_track | self_name, partner_name |
| elliptical-ni-ne | on_track | on_track | wellbeing |
| nonsequitur-slot-fill | drifting | on_track ⚠️ | recommendation |
| order-after-answers | on_track | on_track | order |
| wandering-no-slot | drifting | off_track ⚠️ | — |
| clean-slot-fill | on_track | on_track | recommendation |
| derailed-input | off_track | off_track | — |
| earned-under-annotated | on_track | on_track | self_name, partner_name |
| elliptical-ni-ne | on_track | on_track | wellbeing |
| nonsequitur-slot-fill | drifting | on_track ⚠️ | recommendation |
| order-after-answers | on_track | on_track | order |
| wandering-no-slot | drifting | drifting | — |
