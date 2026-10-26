# Ghost Data Still Exists — deletion-audit agent

## The problem

A deletion workflow reports **Complete** while personal data is still sitting in
replicas, logs, support attachments, exports, search indexes, analytics records,
archives and backups — and active sync or restore paths can put back what was
deleted. For each of 518 requests the agent must decide whether the deletion is
`COMPLETE`, `INCOMPLETE`, or `NOT_APPLICABLE` because no deletion was requested,
and emit the ordered findings, remediation and verification steps that prove it.

Reported statuses and operation results are **claims**. The agent has to
reconstruct what was actually true at each case's `audit_at` from the supplied
evidence, resolve current *and* historical identifiers, apply
`deletion_rules.csv` to every copy, and keep the records policy says to retain.

## Architecture

```
                         dataset/*.csv  +  dataset/evidence/**
                                      |
        +-----------------------------v------------------------------+
        |  loaders.py        CSV -> frozen models, times -> aware UTC |
        +-----------------------------+------------------------------+
                                      |
        +-----------------------------v------------------------------+
        |  evidence/         json | sql | log | text | document      |
        |                    -> Observation(evidence_id, observed_at,|
        |                       system_id, resource_id, copy_label,  |
        |                       present, attributes, link_keys)      |
        |                    -> CoverageFact (what makes absence     |
        |                       provable)                            |
        |                                                            |
        |   [LLM 1] document_parser.py: PDFs and screenshots ------->|
        |           claude-opus-5-5, strict post-validation           |
        +-----------------------------+------------------------------+
                                      |
        +-----------------------------v------------------------------+
        |  identity.py       request email -> subject (exact match,  |
        |                    current then historical identifiers)    |
        +-----------------------------+------------------------------+
                                      |
        +-----------------------------v------------------------------+
        |  attribution.py    which resources belong to THIS episode  |
        |                    evidence_link | catalog_link |          |
        |                    workflow_run  | orphan_timing           |
        +-----------------------------+------------------------------+
                                      |
        +-----------------------------v------------------------------+
        |  state.py          state at audit_at, per copy             |
        |                    admissible evidence only; drift visible |
        +-----------------------------+------------------------------+
                                      |
        +-----------------------------v------------------------------+
        |  policy.py         resource -> data_category -> treatment  |
        |                    action read from deletion_rules.csv     |
        +-----------------------------+------------------------------+
                                      |
        +-----------------------------v------------------------------+
        |  planner.py        state + policy -> ordered steps         |
        |                    status follows from the steps           |
        |                                                            |
        |   [LLM 2] classifier.py: does this text authorise deletion?|
        |           stage 1 rules (508/518), stage 2 claude-haiku-5-5 |
        +-----------------------------+------------------------------+
                                      |
        +-----------------------------v------------------------------+
        |  validate.py       the submission contract, every run      |
        +-----------------------------+------------------------------+
                                      |
                                 output.csv
```

**The two model touchpoints, and nothing else.** Everything that can be decided
from structured data is plain, tested Python. A model is used only where no rule
can answer:

1. **Request-intent classification** (`claude-haiku-5-5`). Stage-1 rules settle
   508 of 518 requests with no call. The 10 that carry a negation, a scoping
   word or no recognised deletion phrasing go to the model in one batch — the
   dataset contains requests that state a deletion keyword in order to *refuse*
   it, which a cue list decides backwards.
2. **Document and screenshot extraction** (`claude-opus-5-5`). 4 PDFs and 11
   PNGs have no machine-readable structure. The model transcribes; code then
   validates every resource id against the dataset's own vocabulary and drops
   anything it cannot verify.

## Module map

```text
code/
  main.py                  CLI: audit, write output.csv, validate it
  config.yaml              paths, model ids, prices (with source + date), flags
  requirements.txt         pinned
  ruff.toml                lint and format configuration
  audit/
    models.py              frozen dataclasses + the contract's vocabularies
    loaders.py             dataset/*.csv -> models; aware UTC timestamps
    aliases.py             aliases the data catalog declares
    identity.py            request email -> subject; exact matching only
    attribution.py         episode scoping, four attribution layers
    state.py               resource state at audit_at, per copy
    policy.py              data category and required treatment
    planner.py             state + policy -> ordered steps
    pipeline.py            the stages, wired together
    validate.py            submission-contract validator
    llm.py                 the single model gateway: cache, usage, retry
    classifier.py          request intent, rules then model
    evidence/
      systems.py           resource prefix -> system; copy labels
      fields.py            flattening, link keys, presence, times
      context.py           the parser contract
      json_parser.py       recursive record walker
      sql_parser.py        SQLite snapshots, replica drift, coverage
      log_parser.py        seven log line styles, trace merging
      text_parser.py       shift-handover records
      document_parser.py   PDF/PNG via the vision model, post-validated
      registry.py          dispatch by file extension
      index.py             queryable index + the audit_at admissibility rule
  prompts/
    request_intent_classifier.md   version 2
    evidence_extraction.md         version 2
  scripts/
    profile_dataset.py     read-only dataset profile
    ingest_report.py       read-only ingestion diagnostics
    usage_report.py        generates evaluation/usage_report.md
    package.py             builds code.zip
  evaluation/
    main.py                scoring harness (samples + custom cases)
    metrics.py             the measures, separately testable
    check_attribution.py   attribution precision/recall
    check_llm_layer.py     what the model layer changes
    custom_cases/          5 hand-built fixtures, 6 cases
    results/               per-run JSON + latest_summary.md
    decisions.md           every non-obvious rule, with its evidence (D1-D45)
    data_facts.md          measured dataset profile
    ingestion_facts.md     measured ingestion output
    evaluation_report.md   required report
    usage_report.md        required report
    interview_notes.md     one-page summary
  tests/                   517 tests
```

## Design principles

**Deterministic-first.** Identity resolution, attribution, state
reconstruction, policy mapping, step ordering and serialisation are plain Python
with tests. The model answers two questions rules cannot. `--no-llm` runs the
whole system end to end and still scores 31/32 on the samples.

**Claims versus evidence.** `reported_status` and `reported_result` are claims.
Measured: `DELETE_RECORD` activity is 302 `SUCCESS` against 368 `FAILED` and 220
`PARTIAL` — a claimed success is barely a third of attempts. State comes from
evidence; a `SUCCESS` never proves absence.

**Time-admissible evidence.** State may only come from evidence captured at or
before a case's `audit_at`, and from records observed at or before it. Identity
linkage is exempt: who a resource belongs to does not change over time. A date
*printed inside* a record — submitted, ordered, created — is never an
observation time (D23, D43).

**Findings need positive evidence.** A finding is emitted only when evidence
establishes the thing policy objects to. Absence is provable — an explicit
negative, or a coverage fact saying a snapshot enumerated the system without the
resource — but presence is never inferred from silence. The one exception is
document evidence, where a rendered row *is* the positive evidence (D24, D44).

**Deletion episodes.** The same person can file twice, months apart. A link
counts for a case only if it was observed within
`[requested_at − 7d, audit_at + 7d]`, so one episode's findings cannot leak into
another's.

**Copies are first-class.** Every observation carries a `copy_label`, so a
record deleted from the primary and still served by a failover replica is
visible as exactly that — the drift the challenge is built on.

## Setup

Python **3.11** or newer (the code is checked against 3.11 syntax; developed on
3.12).

```bash
python -m pip install -r code/requirements.txt
```

For the two model touchpoints, put an Anthropic key in a `.env` file at the
repository root:

```
ANTHROPIC_API_KEY=<your Anthropic API key>
```

`.env` is gitignored, excluded from the package, and read only by
`code/audit/llm.py`. A real environment variable takes precedence over the file.
**No key is needed to reproduce the submitted `output.csv`** — the shipped
response cache covers it.

## Running

```bash
# audit all 518 cases, write output.csv at the repo root, then validate it
python code/main.py

# deterministic only: no model calls at all
python code/main.py --no-llm

# a subset, or a different requests file
python code/main.py --cases case_33,case_34
python code/main.py --input dataset/sample_deletion_requests.csv --output /tmp/s.csv
```

Every run validates its own output against the submission contract and exits
non-zero on a violation.

### Evaluation

```bash
python code/evaluation/main.py                # the 32 public samples
python code/evaluation/main.py --suite all    # samples + 5 custom cases
python code/evaluation/main.py --dataset-dir code/evaluation/custom_cases/email_chain
python code/evaluation/check_attribution.py
python code/scripts/usage_report.py
```

### Tests and lint

```bash
python -m pytest code/tests -q      # 517 tests
python -m ruff check code
```

## Configuration

`code/config.yaml` holds dataset and output paths, the cache directory, the two
model ids with their effort levels, prices per 1M tokens with the source URL and
the date they were verified, and feature flags. No secret is ever stored there.

Sampling parameters are deliberately absent: a non-default `temperature` is
rejected by both configured models. Repeatability comes from the content-hashed
response cache plus the pinned effort levels.

## Assumptions

- `case_id` sorts by the integer suffix, so `case_99` precedes `case_100`. The
  dataset is already in that order; sorting explicitly keeps it true under
  `--cases` or a reordered input.
- `system_id` is the resource's *owning* system, derived from the `resource_id`
  prefix. The activity stream routes some resources through other systems; the
  answer format never does.
- A step's `state` is a pure function of its `operation`, so an invalid pair
  cannot be constructed.
- Within a phase, steps order by the problem statement's operation table, then
  `system_id`, then `resource_id`, as plain string sorts. There is no separate
  dependency pass: the table already puts `DISABLE_DATA_FLOW` and
  `BLOCK_RESTORE` ahead of the record-level remediations.
- `RETAIN` means two different things. A security audit log or legal hold is
  verified present; a backup archive is kept while its restore path is blocked,
  which is what `rule_14`'s own condition says.
- An evidence citation prefers a `file_id`; an `activity_id` is used where no
  evidence file covers the resource, which the contract permits.
- `system_activity.csv` is not an evidence source. It is a stream of claims,
  handled separately.

Full reasoning for each, with the measurements behind it, is in
`code/evaluation/decisions.md`.
