# Evaluation summary

Recorded 2026-10-25T11:31:21+00:00 by `python code/evaluation/main.py`.

| suite | cases | status accuracy | step f1 | sequence exact | exact cases |
|---|---|---|---|---|---|
| samples | 32 | 32/32 (100.0%) | 98.7% | 29/32 | 29/32 |
| email_chain | 1 | 1/1 (100.0%) | 100.0% | 1/1 | 1/1 |
| lookalike_non_deletion | 2 | 2/2 (100.0%) | 100.0% | 2/2 | 2/2 |
| post_audit_evidence | 1 | 1/1 (100.0%) | 100.0% | 1/1 | 1/1 |
| replica_drift_resync | 1 | 1/1 (100.0%) | 100.0% | 1/1 | 1/1 |
| tombstone_retention | 1 | 1/1 (100.0%) | 100.0% | 1/1 | 1/1 |

## samples

- cases: **32**
- status accuracy: **100.0%**
- step precision / recall / f1: **99.5% / 97.9% / 98.7%**
- resource discovery recall: **96.4%**
- field accuracy (state / evidence_id / step_no): **100.0% / 100.0% / 97.9%** on 189 matched steps
- sequence exact: **29/32**, mean normalised edit distance **0.0374**
- identity led to a gold resource: **31/32**
- exact cases: **29/32**

Mismatch causes: unrecoverable 2, attribution 1

| case | cause | gold | predicted | missing | extra |
|---|---|---|---|---|---|
| case_04 | attribution | COMPLETE(2) | COMPLETE(1) | account_004, analytics_901 | account_631 |
| case_18 | unrecoverable | INCOMPLETE(14) | INCOMPLETE(13) | audit_record_6645 | - |
| case_20 | unrecoverable | INCOMPLETE(8) | INCOMPLETE(7) | audit_record_5569 | - |


## email_chain

- cases: **1**
- status accuracy: **100.0%**
- step precision / recall / f1: **100.0% / 100.0% / 100.0%**
- resource discovery recall: **100.0%**
- field accuracy (state / evidence_id / step_no): **100.0% / 100.0% / 100.0%** on 9 matched steps
- sequence exact: **1/1**, mean normalised edit distance **0.0000**
- identity led to a gold resource: **1/1**
- exact cases: **1/1**


## lookalike_non_deletion

- cases: **2**
- status accuracy: **100.0%**
- step precision / recall / f1: **100.0% / 100.0% / 100.0%**
- resource discovery recall: **100.0%**
- field accuracy (state / evidence_id / step_no): **100.0% / 100.0% / 100.0%** on 6 matched steps
- sequence exact: **2/2**, mean normalised edit distance **0.0000**
- identity led to a gold resource: **1/2**
- exact cases: **2/2**


## post_audit_evidence

- cases: **1**
- status accuracy: **100.0%**
- step precision / recall / f1: **100.0% / 100.0% / 100.0%**
- resource discovery recall: **100.0%**
- field accuracy (state / evidence_id / step_no): **100.0% / 100.0% / 100.0%** on 3 matched steps
- sequence exact: **1/1**, mean normalised edit distance **0.0000**
- identity led to a gold resource: **1/1**
- exact cases: **1/1**


## replica_drift_resync

- cases: **1**
- status accuracy: **100.0%**
- step precision / recall / f1: **100.0% / 100.0% / 100.0%**
- resource discovery recall: **100.0%**
- field accuracy (state / evidence_id / step_no): **100.0% / 100.0% / 100.0%** on 9 matched steps
- sequence exact: **1/1**, mean normalised edit distance **0.0000**
- identity led to a gold resource: **1/1**
- exact cases: **1/1**


## tombstone_retention

- cases: **1**
- status accuracy: **100.0%**
- step precision / recall / f1: **100.0% / 100.0% / 100.0%**
- resource discovery recall: **100.0%**
- field accuracy (state / evidence_id / step_no): **100.0% / 100.0% / 100.0%** on 5 matched steps
- sequence exact: **1/1**, mean normalised edit distance **0.0000**
- identity led to a gold resource: **1/1**
- exact cases: **1/1**
