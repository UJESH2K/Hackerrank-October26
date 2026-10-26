# Interview notes

## Architecture in five lines

1. Every artefact under `dataset/evidence/` becomes an `Observation` — one fact
   about one *copy* of one resource at one time — plus `CoverageFact`s that are
   what make absence provable.
2. The request email resolves to a subject by exact match on current then
   historical identifiers; resources are attributed to that subject through four
   layers (evidence link, catalog link, deletion-workflow run, orphan timing),
   scoped to one deletion episode.
3. State is reconstructed per copy at `audit_at` using only admissible evidence,
   so replica drift and recreation paths are visible rather than averaged away.
4. `deletion_rules.csv` maps a resource's data category to a required treatment;
   a treatment already satisfied yields a verification step, one not satisfied
   yields a finding, a remediation and a verification.
5. The status follows from the steps — any finding means `INCOMPLETE` — and the
   output is validated against the submission contract on every run.

## The hardest problems

**Identifier-free deleted records (D18–D21).** About half the primary accounts
have been deleted, so their evidence carries no email at all. They are matched
through the deletion worker's own run: candidates are scored on result agreement,
presence in the case evidence, proximity to `reported_at`, and a **learned
timing signature**. From 348 anchor events the `(follow-up − account deletion)
mod 60` histogram puts all its mass in 12 of 60 bins. Band membership alone was
not enough: one wrong candidate's follow-ups landed on learned bands yet sat
**28 hours** out, so D19 learns a maximum delay (3.38 h) as well. D20 then uses
each run's *own* cadence to break ties between runs. Accounts: 31/32.

**Replica drift and recreation (D10, D12).** `copy_label` is on every
observation, so a record gone from `customer_profiles_primary` and still live in
`customer_profiles_failover_replica` is a remaining record, not a clean
deletion. Absence needs a coverage fact — a snapshot that enumerated the system
and did not contain the resource. `evidence_90` is the purest case: one `INSERT`,
into `snapshot_scope`. Its entire content is a claim of absence.

**Episodes and time admissibility (D17, D23, D43).** State may only come from
evidence captured at or before `audit_at`; identity linkage may come from
anywhere, because ownership does not change. The same person can file twice, so
a link counts only inside `[requested_at − 7d, audit_at + 7d]`. The same trap
appeared three times: `created_at` dated a link five weeks early (D23), then the
model dated a support record to its *submitted* date, 12 weeks early (D43). A
date printed inside a record is never an observation time.

**Multilingual intent with negation traps (D7, D32, D40).** Three non-deletion
requests state a deletion keyword in order to refuse it — *"I am not asking for
deletion"*. A cue list decides those backwards, which is why intent goes to a
model. v1 was too strict in the other direction: it marked two cases
`NOT_APPLICABLE` at 0.60 and 0.85 confidence, costing them entirely, because the
prompt did not say that a *follow-up* on a deletion is still a deletion request.
v2 fixed the definition; D40 added an asymmetric backstop — a non-deletion
verdict is accepted only at ≥ 0.9, because a wrong `NOT_APPLICABLE` discards a
whole case. After v2 the backstop fired zero times.

**Document timing and aliases (D42–D44).** Documents show business references,
not internal ids. The catalog *declares* the mapping — `object_19
(delivery_confirmation_19.pdf)`, `tombstone_17 (user_17)` — so D42 resolves
declared aliases instead of dropping them, recovering 2 of 24 drops and changing
two cases. D44 was the subtler one: a record a document *lists* is present, even
when the model reports no status, because a rendered row is itself the evidence.

**An invalid API key, swallowed (D38).** The first key returned 401. The blanket
exception handler recorded a 0-token call and fell through to the deterministic
path, so a bad key produced a run that looked exactly like a clean deterministic
one — and the usage record said `cold, 0 tokens, $0.00`. Failures are now
recorded, auth errors are terminal (one bad key costs 1 request, not 518), and
the run prints a banner.

**Two stdlib and syntax traps (D1, D30).** A package at `code/io/` would have
satisfied `import io` for the whole process, breaking `open()`, `csv` and
`json` — caught before it shipped. And `f"{'gold \\ pred':<16}"` is Python
3.12-only syntax that would have been a `SyntaxError` on a 3.11 grader; there is
now an `ast.parse(feature_version=(3,11))` check over every file.

## Final metrics

| | samples (32) | custom (6) |
|---|---|---|
| status accuracy | **32/32 = 100%** | **6/6 = 100%** |
| step precision / recall / F1 | 99.5% / 97.9% / **98.7%** | 100% / 100% / **100%** |
| field accuracy (state / evidence_id) | **100% / 100%** | 100% / 100% |
| sequence exact | 29/32 = 90.6% | 6/6 = 100% |
| exact cases | 29/32 = 90.6% | 6/6 = 100% |

518-case run: **COMPLETE 149 / INCOMPLETE 359 / NOT_APPLICABLE 10**, 3,252
steps, validator 0 errors, 0 warnings. The three non-exact sample cases are the
D22 unrecoverable items — resources that appear nowhere in participant data.

## Real cost

**$1.1872** total, across 34 API calls and 180,740 tokens — about **$0.0023 per
request**. The submitted `output.csv` came from cached answers and cost nothing
to produce. Three choices did that work: rules answer 508 of 518
classifications; the 15 document extractions are case-independent and cached by
content hash; and each prompt has its own cache namespace, so moving the
classifier prompt from v1 to v2 cost $0.0007 instead of $0.58.

## How AI was used

Spec-first. Each phase started from a written contract — the output format, the
decision rules, the acceptance criteria — and ended with measurement against the
32 public samples before anything was called done. Every non-obvious rule went
into `decisions.md` with the data that justified it, which is why regressions
were attributable: live v1's drop from 31/32 to 29/32 was traced to two specific
cases and two confidence values within minutes. Gates were mechanical (pytest,
ruff, the contract validator, the 3.11 syntax check) so "it works" never rested
on an impression. Several of the most valuable findings came from writing the
check before trusting the result: the validator caught two uncited verifications
the sample set never exercises, and a one-call smoke test caught the dead API
key before a 15-call batch.

## Next steps

- Extend extraction to capture document reference numbers, so
  `export_1842`'s declared alias `NST-2026-0718-1842` can be resolved —
  currently the model never transcribes it.
- Give the retention PDFs a join key, or accept that they cannot contribute.
- Replace the low-confidence account fallback (83 of 518 cases) with an
  abstention that still emits a verification, if the grader rewards that.
- Cross-validate the learned timing bands by holding out a slice of cases, to
  measure how much the `workflow_run` layer depends on this dataset's cadence.
