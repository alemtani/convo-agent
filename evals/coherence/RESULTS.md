# The grade against gold labels

Model: `claude-opus-5`. 55 runs over 11 cases.

## Slot accuracy

Does the tracker credit the facts the learner actually established? **Spurious** is credit they did not earn — the failure this track exists to remove. **Missed** is credit they earned and did not get — the failure A2's floor exists to rescue. A3's numbers are the standing baseline: 0 missed over 55 runs.

| case | exact | spurious | missed |
|---|---|---|---|
| clean-slot-fill | 5/5 | — | — |
| clip-and-tea | 5/5 | — | — |
| computer-work-ni-ne | 5/5 | — | — |
| derailed-input | 5/5 | — | — |
| earned-under-annotated | 5/5 | — | — |
| elliptical-ni-ne | 3/5 | — | `wellbeing` ×2 |
| milk-and-biscuits | 5/5 | — | — |
| nonsequitur-slot-fill | 5/5 | — | — |
| order-after-answers | 5/5 | — | — |
| owed-drinks-then-order | 5/5 | — | — |
| wandering-no-slot | 5/5 | — | — |
| **total** | **53/55** | **0** | **2** |

## Per-case runs

| case | gold slots | credited | owed turns |
|---|---|---|---|
| clean-slot-fill | recommendation | recommendation | — |
| clip-and-tea | order | order | — |
| computer-work-ni-ne | self_job, partner_origin, partner_job | self_job, partner_origin, partner_job | — |
| derailed-input | — | — | — |
| earned-under-annotated | self_name, partner_name | self_name, partner_name | — |
| elliptical-ni-ne | wellbeing | wellbeing | — |
| milk-and-biscuits | recommendation, drinks, order | recommendation, drinks, order | — |
| nonsequitur-slot-fill | recommendation | recommendation | — |
| order-after-answers | order | order | — |
| owed-drinks-then-order | order | order | drinks |
| wandering-no-slot | — | — | — |
| clean-slot-fill | recommendation | recommendation | — |
| clip-and-tea | order | order | — |
| computer-work-ni-ne | self_job, partner_origin, partner_job | self_job, partner_origin, partner_job | — |
| derailed-input | — | — | — |
| earned-under-annotated | self_name, partner_name | self_name, partner_name | — |
| elliptical-ni-ne | wellbeing | — ⚠️ | — |
| milk-and-biscuits | recommendation, drinks, order | recommendation, drinks, order | — |
| nonsequitur-slot-fill | recommendation | recommendation | — |
| order-after-answers | order | order | — |
| owed-drinks-then-order | order | order | drinks |
| wandering-no-slot | — | — | — |
| clean-slot-fill | recommendation | recommendation | — |
| clip-and-tea | order | order | — |
| computer-work-ni-ne | self_job, partner_origin, partner_job | self_job, partner_origin, partner_job | — |
| derailed-input | — | — | — |
| earned-under-annotated | self_name, partner_name | self_name, partner_name | — |
| elliptical-ni-ne | wellbeing | wellbeing | — |
| milk-and-biscuits | recommendation, drinks, order | recommendation, drinks, order | — |
| nonsequitur-slot-fill | recommendation | recommendation | — |
| order-after-answers | order | order | — |
| owed-drinks-then-order | order | order | drinks |
| wandering-no-slot | — | — | — |
| clean-slot-fill | recommendation | recommendation | — |
| clip-and-tea | order | order | — |
| computer-work-ni-ne | self_job, partner_origin, partner_job | self_job, partner_origin, partner_job | — |
| derailed-input | — | — | — |
| earned-under-annotated | self_name, partner_name | self_name, partner_name | — |
| elliptical-ni-ne | wellbeing | wellbeing | — |
| milk-and-biscuits | recommendation, drinks, order | recommendation, drinks, order | — |
| nonsequitur-slot-fill | recommendation | recommendation | — |
| order-after-answers | order | order | — |
| owed-drinks-then-order | order | order | drinks |
| wandering-no-slot | — | — | — |
| clean-slot-fill | recommendation | recommendation | — |
| clip-and-tea | order | order | — |
| computer-work-ni-ne | self_job, partner_origin, partner_job | self_job, partner_origin, partner_job | — |
| derailed-input | — | — | — |
| earned-under-annotated | self_name, partner_name | self_name, partner_name | — |
| elliptical-ni-ne | wellbeing | — ⚠️ | — |
| milk-and-biscuits | recommendation, drinks, order | recommendation, drinks, order | — |
| nonsequitur-slot-fill | recommendation | recommendation | — |
| order-after-answers | order | order | — |
| owed-drinks-then-order | order | order | drinks |
| wandering-no-slot | — | — | — |
