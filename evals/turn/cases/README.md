# Red-team probes for the partner

These are **constructed**, not recorded. `tests/fixtures/sessions/` holds real
turns from real sessions and stays that way; a fabricated turn filed among
recordings is a lie a later reader cannot detect.

Each probe sets up a turn where the partner has an obvious opportunity to hand
over a `request` slot the learner has not asked for. The learner's line is
deliberately empty of asks, so any slot the reply establishes was volunteered.

They carry no gold labels. Gold answers "what did this turn deserve", which is a
question about the learner; a probe is a question about the partner.

    python -m evals.turn.replay --cases-dir evals/turn/cases
    python -m evals.turn.replay --record --samples 3 --cases-dir evals/turn/cases
