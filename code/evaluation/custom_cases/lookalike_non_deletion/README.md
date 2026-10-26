# lookalike_non_deletion

Two different people hold near-identical addresses, `alice.monroe@example.com`
and `a.monroe@example.com`, and each owns an account and a search document.
`case_401` is a marketing-only opt-out, so no deletion is authorised; `case_402`
is a genuine deletion from the *other* person and must touch only her two
records, never the look-alike's.
The intent verdicts come from `recorded_intents.csv`, a recorded classifier
answer, so the suite runs offline and free with `--no-llm`.
