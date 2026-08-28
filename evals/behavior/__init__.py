"""Behavioral evals: structural invariants over worker output, off cassettes.

These are the tests A0.6 took off the `live` marker. They assert *structure* —
a valid schema, a slot credited, a slot withheld — which is exactly what a
recording reproduces, so they run in CI for free and are a merge gate.

What stayed live is what a recording cannot stand in for: a real
`cache_read_input_tokens`, and everything Azure.
"""
