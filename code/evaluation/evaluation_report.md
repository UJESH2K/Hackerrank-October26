# Evaluation report

Ghost Data Still Exists — deletion-audit agent.

## Method

Two scored sets, both run through the real pipeline in-process, never against a
stub:

1. **The 32 public samples** in `dataset/sample_deletion_requests.csv`, scored
   against `dataset/sample_output.csv`. These run with the model layer enabled
   and its cache warm, so they measure the submitted configuration.
2. **Five hand-built cases** in `code/evaluation/custom_cases/`, six cases in
   total. Each is a self-contained dataset directory using the real
   participant-facing file formats, with an `expected_output.csv` derived by hand
   from `deletion_rules.csv` and the problem statement's operation table — not
   by running the pipeline and recording what it said. They run with `--no-llm`,
   so the suite is free, offline and reproducible.

`sample_output.csv` is read only by `code/evaluation/`. The audit pipeline never
opens it (decision D6), so no gold answer can leak into a prediction.

### What each metric means

A step's **identity** is `(phase, operation, system_id, resource_id)` — what the
step claims about which resource. `state`, `evidence_id` and `step_no` are scored
separately as **fields**, on the steps whose identity already matched, so a
citation error is never mistaken for a missing finding.

- **Status accuracy** — the three-class `deletion_status`.
- **Step precision / recall / F1** — on step identity.
- **Field accuracy** — per field, over matched steps.
- **Sequence accuracy** — the step array in order, exact; plus the mean
  normalised Levenshtein distance over step identities.
- **Resource discovery recall** — of the resources gold names, how many were
  attributed.
- **Identity accuracy** — how often the request email resolved to a subject who
  owns at least one of the case's gold resources.
- **Cause** — each failing case is attributed to the first applicable of
  `unrecoverable`, `attribution`, `status`, `state`, `policy`, `ordering`,
  `citation`, so a regression points at one module. `unrecoverable` is derived
  from the data, not from a list: the harness asks whether the gold resource
  appears in any parsed observation or activity row at all.

## Sample results

```
status accuracy        32/32 = 100.0%

gold \ predicted          COMPLETE      INCOMPLETE  NOT_APPLICABLE
COMPLETE                        10               0               0
INCOMPLETE                       0              22               0
NOT_APPLICABLE                   0               0               0
```

| metric | value |
|---|---|
| step precision | **99.5%** |
| step recall | **97.9%** |
| step F1 | **98.7%** |
| gold / predicted steps | 193 / 190 |
| field accuracy — `state` | **100.0%** |
| field accuracy — `evidence_id` | **100.0%** |
| field accuracy — `step_no` | 97.9% |
| matched steps scored for fields | 189 |
| resource discovery recall | **96.4%** |
| resource discovery precision | 99.1% |
| sequence exact | **29/32 = 90.6%** |
| mean normalised edit distance | **0.0374** |
| identity resolved | 32/32 |
| identity led to a gold resource | 31/32 = 96.9% |
| exact cases (status + every step) | **29/32 = 90.6%** |

`step_no` sits below `state` and `evidence_id` for one mechanical reason: a
missing unrecoverable finding shortens the array, so every later step in
`case_18` and `case_20` is numbered one lower. Those steps are otherwise
correct, which is why their identity matched at all.

## Before and after

Each row is the whole system at one point, measured on the same 32 samples.

| configuration | status | step F1 | exact | 518 distribution |
|---|---|---|---|---|
| deterministic only (`--no-llm`) | 31/32 (96.9%) | 97.4% | 87.5% | 166 / 352 / 0 |
| live v1 — model layer added | 29/32 (90.6%) | 95.4% | 81.2% | 157 / 351 / 10 |
| live v2 — classifier prompt v2 + backstop | 31/32 (96.9%) | 97.4% | 87.5% | 150 / 358 / 10 |
| **live v3 — document timing and presence** | **32/32 (100%)** | **98.7%** | **90.6%** | **149 / 359 / 10** |
| final submitted run | 32/32 (100%) | 98.7% | 90.6% | 149 / 359 / 10 |

Live v1 is the project's only regression, and it is the most instructive row:
handing the model the classification job without telling it that a *follow-up on
a deletion is still a deletion request* cost two whole cases. v2 and v3 recovered
it and went past the deterministic baseline.

## Custom cases

| case | cases | what it tests | result |
|---|---|---|---|
| `email_chain` | 1 | request filed with the oldest of three emails; three records each carrying a different address | **pass** |
| `replica_drift_resync` | 1 | `SUCCESS` claimed, failover replica still holds the profile, connector re-synced a search doc | **pass** |
| `tombstone_retention` | 1 | tombstone redaction, audit record retained, backup already restore-blocked | **pass** |
| `lookalike_non_deletion` | 2 | two users with near-identical emails; marketing-only opt-out vs a real deletion | **pass** |
| `post_audit_evidence` | 1 | later evidence shows deletion, admissible evidence shows presence | **pass** |
| **total** | **6** | | **6/6, 100% on every metric** |

All six matched their hand-derived expected output on the first run, including
step order, `state`, `evidence_id` and `step_no`. Each directory holds a
five-line `README.md` explaining what the case is for.

`lookalike_non_deletion` needs an intent verdict, which cannot come from a model
in an offline suite, so the fixture ships `recorded_intents.csv` and the harness
passes it through the pipeline's `decisions=` parameter. The recorded verdict
sits next to the expected output where a reader can see it; the pipeline's own
classifier is untouched.

## Failures found and improvements made

The full decision log is `decisions.md` (D1–D45). The ones that moved a metric:

| # | problem found | fix | effect |
|---|---|---|---|
| **D1** | `code/io/` would have shadowed the stdlib `io` module for the whole process | one `audit` package, no stdlib-colliding names | avoided breaking `open()`, `csv`, `json` |
| **D8** | activity rows route `support_*` through `search_service`; the answer format never does | derive `system_id` from the resource prefix, never from `target_system` | 232 rows' worth of wrong `system_id` avoided |
| **D15** | 228 observations had unknown presence — exactly the PII-scan and heartbeat lines | per-event-suffix read-back rule, after enumerating all 31 log events | unknown presence 228 → 1 |
| **D19** | a wrong account candidate's follow-ups landed on learned bands yet sat 28 hours out | learn a maximum follow-up delay (3.38 h) alongside the bands | accounts 30/32 → 31/32 |
| **D20** | two cases tied for one orphan analytics record | use each run's *own* cadence, measurable from its linked resources | orphans 5/5 → 6/6 |
| **D23** | `created_at` won the observation-time race, dating a link five weeks early | `created_at`/`expires_at`/`last_write` are not observation times | sample recall 94.6% → 95.5% |
| **D27** | two `COMPLETE` verifications had empty `evidence_id` | cite the `activity_id` of the catalog row, which the contract permits | validator errors → 0 |
| **D30** | a backslash in an f-string is Python 3.12-only syntax | fixed, plus an `ast.parse(feature_version=(3,11))` check over every file | avoided a `SyntaxError` on a 3.11 grader |
| **D32** | the cue `"correct"` matched *"apply the correct treatment"* | match real rectification phrasings only | model calls 46 → 10, a 78% cut |
| **D38** | a rejected API key was silently swallowed into a deterministic-looking run | record failures, treat auth errors as terminal, print a banner | one bad key costs 1 request, not 518 |
| **D39** | `--no-llm` was reading the model's cached verdicts | gate both caches on the gateway being enabled | made the before/after comparison valid at all |
| **D40** | v1 discarded two whole cases on 0.60 and 0.85 confidence | accept a non-deletion verdict only at ≥ 0.9 | backstop; 0 overrides needed after v2 |
| **D41** | editing one prompt re-paid every other prompt's calls | per-prompt cache fingerprints | a classifier change cost $0.0007 instead of $0.58 |
| **D42** | 24 document references dropped as non-resource-ids | resolve aliases the data catalog declares | 2 recovered, both changed a case |
| **D43** | the model dated a record to its *submitted* date, 12 weeks early | for documents, the observation time is a capture time only | `case_07` became exact |
| **D44** | `present: null` suppressed a finding the artefact supported | a listed record is present unless the artefact says otherwise | sample status 31/32 → 32/32 |

## Unrecoverable gold, with proof

Three gold resources cannot be produced from participant-facing data. The
harness detects this from the data rather than from a list: it parses every
artefact and the activity stream and asks whether the resource appears anywhere.

| case | resource | appears in |
|---|---|---|
| `case_18` | `audit_record_6645` | nothing but `sample_output.csv` |
| `case_20` | `audit_record_5569` | nothing but `sample_output.csv` |
| `case_04` | `account_004` | nothing but `sample_output.csv` |

Verified independently by grep over the whole dataset:

```
$ grep -rl "audit_record_6645" dataset/
dataset/sample_output.csv
```

`case_04` also wants `analytics_901`, which *is* in `evidence_56` — but it
carries no identifier (it is an anonymised profile) and `case_04` has no account
deletion time to date it against, so there is nothing to attribute it by. Its
account appears nowhere, so the low-confidence fallback (D21) guesses
`account_631`, which is the single false positive in the sample set.

Chasing these three would mean a rule that fits two or three specific rows —
the case-family shortcut AGENTS.md §6.7 forbids. The error-analysis round was
therefore closed with no code change (D45).

## Limitations

- **Document extraction depends on a model.** Without `ANTHROPIC_API_KEY` the 15
  PDFs and screenshots yield nothing and the pipeline falls back to the
  deterministic path, which scores 31/32 instead of 32/32. The shipped cache
  makes the submitted result reproducible without a key, but a new artefact
  would need one.
- **`NOT_APPLICABLE` has no public ground truth.** `sample_output.csv` contains
  none, so the ten non-deletion cases are validated against the ten
  `CLASSIFY_REQUEST` activity rows as an independent cross-check, plus the
  `lookalike_non_deletion` fixture. A non-deletion request that intake never
  classified would be invisible to that check.
- **The retention PDFs contribute nothing.** `legal_retention_register` ties
  retention to order references and user ids, with no `legal_hold_*` resource id
  anywhere, so post-validation correctly drops every row. The facts are there;
  the join key is not.
- **The timing signature is learned from this dataset.** The bands and the
  3.38-hour maximum delay are unsupervised, re-learned on every run from
  whichever requests are loaded, but a hidden set whose worker cadence differs
  would weaken the `workflow_run` layer.
- **83 of 518 accounts rest on the low-confidence fallback** (D21). Each is a
  plausible id rather than a proven one; the alternative was an empty
  verification.
- **Identity is exact-match only** (D16), which is correct for this dataset — zero
  identifier collisions were measured — but would miss a genuine typo in a
  request email.

## Reproducing

```bash
python -m pip install -r code/requirements.txt

# the submitted output, from the shipped cache: no key needed, 0 tokens
python code/main.py

# the deterministic pipeline alone
python code/main.py --no-llm

# scoring
python code/evaluation/main.py                # the 32 public samples
python code/evaluation/main.py --suite all    # samples + the 5 custom cases
python code/evaluation/main.py --dataset-dir code/evaluation/custom_cases/email_chain

# diagnostics
python code/evaluation/check_attribution.py   # attribution precision and recall
python code/scripts/ingest_report.py          # per-artefact ingestion
python code/scripts/profile_dataset.py        # measured dataset profile
python code/scripts/usage_report.py           # regenerate the usage report

# quality gates
python -m pytest code/tests -q
python -m ruff check code
```

Results are written to `code/evaluation/results/`: a timestamped JSON per run,
`latest.json`, and `latest_summary.md`.
