# Token usage and cost

Every token count below is read from the Anthropic API's own `usage`
field on each response and recorded at the time of the call in
`code/evaluation/usage_run.json`. Nothing is estimated from text length.
Costs multiply those counts by the prices in `code/config.yaml`.

## Provider and models

- **Provider:** Anthropic (anthropic), Messages API via the official `anthropic` Python SDK.
- **Request-intent classification:** `claude-haiku-5-5` at effort `low`.
- **Document and screenshot extraction:** `claude-opus-5-5` at effort `medium`.
- No sampling parameters are sent: a non-default `temperature` is rejected by both models (decision D31). Repeatability comes from the on-disk response cache and the pinned effort levels.

### Prices used, per 1M tokens

| model | input | output |
|---|---|---|
| `claude-haiku-5-5` | $0.10 | $0.50 |
| `claude-opus-5-5` | $4.00 | $20.00 |
| `claude-sonnet-5-5` | $2.00 | $10.00 |

Price provenance: Source: Anthropic model and pricing documentation, via the bundled claude-api reference (https://docs.anthropic.com/en/docs/about-claude/pricing). Verified: 2026-10-25, against the reference cached 2026-10-06.

## Runs that spent tokens (the cold runs that filled the cache)

| run | cases | API calls | cache hits | input | output | cost |
|---|---|---|---|---|---|---|
| 2026-10-25T09:36:50+00:00 (cold) | 32 | 15 | 10 | 70,296 | 16,414 | $0.6095 |

## The final run that produced `output.csv`

- recorded: `2026-10-25T11:25:49+00:00`
- run type: **warm**
- cases: 518
- calls: 10 of which **0 reached the API**
- cache hits: 10
- tokens: 0 input, 0 output
- cost: **$0.0000**

**The submitted `output.csv` was produced entirely from cached
model answers.** The final run made no API call and spent no
tokens: every classification verdict and every document
extraction was served from the on-disk cache written by the cold
runs above. The run is therefore exactly reproducible from the
cache, and re-running it costs nothing.

## Earlier phases, recovered from `log.txt`

`usage_run.json` was reset between phases so each phase could be
measured on its own. These runs are therefore not in the current
history file; their figures come from the per-turn entries in
`log.txt`, which recorded them at the time.

| phase | cases | API calls | input | output | cost | note |
|---|---|---|---|---|---|---|
| live v1 | 32 | 16 | 65,036 | 18,068 | $0.5757 | first live run: 1 classifier batch + 15 document extractions |
| live v1 | 518 | 1 | 2,484 | 719 | $0.0006 | classifier batch for the evaluation set; documents cached |
| live v2 | 32 | 1 | 3,193 | 742 | $0.0007 | classifier prompt v2; the 15 extractions were cache hits (D41) |
| live v2 | 518 | 1 | 3,077 | 711 | $0.0007 | classifier batch for the evaluation set |

## Totals in the current history file

| | value |
|---|---|
| calls (including cache hits) | 65 |
| calls that reached the API | **15** |
| cache hits | 50 |
| input tokens | 70,296 |
| output tokens | 16,414 |
| total tokens | **86,710** |
| cost | **$0.6095** |

## Total real spend for the whole project

Current history plus the earlier phases above.

| | value |
|---|---|
| calls that reached the API | **34** |
| input tokens | 144,086 |
| output tokens | 36,654 |
| total tokens | **180,740** |
| average tokens per request (/518) | **348.9** |
| **total real spend** | **$1.1872** |
| average cost per request (/518) | $0.002292 |

## Per model

| model | calls | API calls | cache hits | input | output | cost |
|---|---|---|---|---|---|---|
| `claude-haiku-5-5` | 50 | 0 | 50 | 0 | 0 | $0.0000 |
| `claude-opus-5-5` | 15 | 15 | 0 | 70,296 | 16,414 | $0.6095 |

## Per purpose

| purpose | calls | API calls | cache hits | input | output |
|---|---|---|---|---|---|
| classify | 50 | 0 | 50 | 0 | 0 |
| extract | 15 | 15 | 0 | 70,296 | 16,414 |

## Why the bill is small

Three design choices, each measured:

1. **Rules answer most of the classification.** Stage 1 decides 508
   of 518 requests with no model call; only the 10 that carry a
   negation, a scoping word or no recognised deletion phrasing reach
   the model, batched into a single request (D32).
2. **Documents are read once, not once per case.** The 15 artefacts
   are case-independent, so their extractions are cached by file
   content hash and shared across all 518 cases.
3. **Each prompt has its own cache namespace.** Editing the
   classifier prompt re-pays only the classifier calls, and editing
   the extraction prompt re-pays only the extractions (D41). The
   classifier went from v1 to v2 for $0.0007 instead of $0.58.

## Reproducing

```bash
python code/main.py                 # warm: 0 tokens, identical output
python code/scripts/usage_report.py # regenerates this file
```

No API key is needed to reproduce `output.csv` from the shipped
cache. A key is needed only to re-derive the cached answers from
scratch.
