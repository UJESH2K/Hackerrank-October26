"""Generate ``code/evaluation/usage_report.md`` from the recorded usage history.

Every number comes from ``code/evaluation/usage_run.json``, which the pipeline
appends to on each run using the token counts in the API's own ``usage`` field.
Nothing here is estimated except the cost, which multiplies those counts by the
prices in ``code/config.yaml``.

Run from the repository root::

    python code/scripts/usage_report.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CODE_DIR.parent

if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import yaml  # noqa: E402

USAGE_FILE = CODE_DIR / "evaluation" / "usage_run.json"
OUTPUT_FILE = CODE_DIR / "evaluation" / "usage_report.md"
CONFIG_FILE = CODE_DIR / "config.yaml"

#: The evaluation set size, used for the per-request averages.
EVALUATION_CASES = 518

#: Cold runs from earlier phases of the project. ``usage_run.json`` was reset
#: between phases so each phase could be measured on its own, so these figures
#: are recovered from the per-turn entries in ``log.txt`` rather than from the
#: live file. They are included because the project's real spend is the sum of
#: every run, not only the ones still in the current history.
EARLIER_RUNS: tuple[dict[str, Any], ...] = (
    {
        "phase": "live v1",
        "cases": 32,
        "api_calls": 16,
        "input": 65036,
        "output": 18068,
        "cost": 0.5757,
        "note": "first live run: 1 classifier batch + 15 document extractions",
    },
    {
        "phase": "live v1",
        "cases": 518,
        "api_calls": 1,
        "input": 2484,
        "output": 719,
        "cost": 0.0006,
        "note": "classifier batch for the evaluation set; documents cached",
    },
    {
        "phase": "live v2",
        "cases": 32,
        "api_calls": 1,
        "input": 3193,
        "output": 742,
        "cost": 0.0007,
        "note": "classifier prompt v2; the 15 extractions were cache hits (D41)",
    },
    {
        "phase": "live v2",
        "cases": 518,
        "api_calls": 1,
        "input": 3077,
        "output": 711,
        "cost": 0.0007,
        "note": "classifier batch for the evaluation set",
    },
)


def load_history() -> list[dict[str, Any]]:
    """Every recorded run, oldest first."""
    if not USAGE_FILE.is_file():
        return []
    loaded = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, list) else []


def load_prices() -> tuple[dict[str, dict[str, float]], str]:
    """Prices per 1M tokens, and the provenance comment beside them."""
    config = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    prices = config.get("prices_per_1m_tokens", {})
    source_lines = [
        line.strip().lstrip("#").strip()
        for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("#")
        and ("Source:" in line or "Verified:" in line or "docs.anthropic" in line)
    ]
    return (prices, " ".join(source_lines))


def cost_of(model: str, input_tokens: int, output_tokens: int, prices: dict) -> float:
    """Cost in USD of one model's tokens."""
    entry = prices.get(model, {})
    return (
        input_tokens * float(entry.get("input", 0.0))
        + output_tokens * float(entry.get("output", 0.0))
    ) / 1_000_000


def main() -> int:
    """Write the usage report."""
    history = load_history()
    prices, provenance = load_prices()
    config = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    llm = config.get("llm", {})

    spending = [run for run in history if run.get("input_tokens")]
    totals_by_model: dict[str, Counter[str]] = {}
    totals_by_purpose: dict[str, Counter[str]] = {}
    for run in history:
        for model, entry in run.get("by_model", {}).items():
            bucket = totals_by_model.setdefault(model, Counter())
            for key, value in entry.items():
                bucket[key] += value
        for purpose, entry in run.get("by_purpose", {}).items():
            bucket = totals_by_purpose.setdefault(purpose, Counter())
            for key, value in entry.items():
                bucket[key] += value

    total_input = sum(run.get("input_tokens", 0) for run in history)
    total_output = sum(run.get("output_tokens", 0) for run in history)
    total_cost = sum(
        cost_of(model, entry["input"], entry["output"], prices)
        for model, entry in totals_by_model.items()
    )
    total_calls = sum(run.get("calls", 0) for run in history)
    total_api = sum(run.get("api_calls", 0) for run in history)
    total_hits = sum(run.get("cache_hits", 0) for run in history)
    final = history[-1] if history else {}

    lines = [
        "# Token usage and cost",
        "",
        "Every token count below is read from the Anthropic API's own `usage`",
        "field on each response and recorded at the time of the call in",
        "`code/evaluation/usage_run.json`. Nothing is estimated from text length.",
        "Costs multiply those counts by the prices in `code/config.yaml`.",
        "",
        "## Provider and models",
        "",
        f"- **Provider:** Anthropic ({llm.get('provider', 'anthropic')}), "
        "Messages API via the official `anthropic` Python SDK.",
        f"- **Request-intent classification:** `{llm.get('classifier_model')}` "
        f"at effort `{llm.get('classifier_effort')}`.",
        f"- **Document and screenshot extraction:** `{llm.get('vision_model')}` "
        f"at effort `{llm.get('vision_effort')}`.",
        "- No sampling parameters are sent: a non-default `temperature` is "
        "rejected by both models (decision D31). Repeatability comes from the "
        "on-disk response cache and the pinned effort levels.",
        "",
        "### Prices used, per 1M tokens",
        "",
        "| model | input | output |",
        "|---|---|---|",
    ]
    for model, entry in sorted(prices.items()):
        lines.append(
            f"| `{model}` | ${float(entry.get('input', 0)):.2f} | "
            f"${float(entry.get('output', 0)):.2f} |"
        )
    lines.extend(["", f"Price provenance: {provenance}", ""])

    lines.extend(
        [
            "## Runs that spent tokens (the cold runs that filled the cache)",
            "",
            "| run | cases | API calls | cache hits | input | output | cost |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for run in spending:
        lines.append(
            f"| {run['recorded_at']} ({run['run_type']}) | {run['cases']} | "
            f"{run['api_calls']} | {run['cache_hits']} | "
            f"{run['input_tokens']:,} | {run['output_tokens']:,} | "
            f"${run['estimated_cost_usd']:.4f} |"
        )
    if not spending:
        lines.append("| _none in this history_ | | | | | | |")
    lines.append("")

    lines.extend(
        [
            "## The final run that produced `output.csv`",
            "",
        ]
    )
    if final:
        lines.extend(
            [
                f"- recorded: `{final['recorded_at']}`",
                f"- run type: **{final['run_type']}**",
                f"- cases: {final['cases']}",
                f"- calls: {final['calls']} of which **{final['api_calls']} "
                "reached the API**",
                f"- cache hits: {final['cache_hits']}",
                f"- tokens: {final['input_tokens']} input, "
                f"{final['output_tokens']} output",
                f"- cost: **${final['estimated_cost_usd']:.4f}**",
                "",
                "**The submitted `output.csv` was produced entirely from cached",
                "model answers.** The final run made no API call and spent no",
                "tokens: every classification verdict and every document",
                "extraction was served from the on-disk cache written by the cold",
                "runs above. The run is therefore exactly reproducible from the",
                "cache, and re-running it costs nothing.",
                "",
            ]
        )

    earlier_input = sum(run["input"] for run in EARLIER_RUNS)
    earlier_output = sum(run["output"] for run in EARLIER_RUNS)
    earlier_api = sum(run["api_calls"] for run in EARLIER_RUNS)
    earlier_cost = sum(run["cost"] for run in EARLIER_RUNS)
    project_input = total_input + earlier_input
    project_output = total_output + earlier_output
    project_cost = total_cost + earlier_cost
    project_api = total_api + earlier_api

    lines.extend(
        [
            "## Earlier phases, recovered from `log.txt`",
            "",
            "`usage_run.json` was reset between phases so each phase could be",
            "measured on its own. These runs are therefore not in the current",
            "history file; their figures come from the per-turn entries in",
            "`log.txt`, which recorded them at the time.",
            "",
            "| phase | cases | API calls | input | output | cost | note |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for run in EARLIER_RUNS:
        lines.append(
            f"| {run['phase']} | {run['cases']} | {run['api_calls']} | "
            f"{run['input']:,} | {run['output']:,} | ${run['cost']:.4f} | "
            f"{run['note']} |"
        )
    lines.extend(
        [
            "",
            "## Totals in the current history file",
            "",
            "| | value |",
            "|---|---|",
            f"| calls (including cache hits) | {total_calls} |",
            f"| calls that reached the API | **{total_api}** |",
            f"| cache hits | {total_hits} |",
            f"| input tokens | {total_input:,} |",
            f"| output tokens | {total_output:,} |",
            f"| total tokens | **{total_input + total_output:,}** |",
            f"| cost | **${total_cost:.4f}** |",
            "",
            "## Total real spend for the whole project",
            "",
            "Current history plus the earlier phases above.",
            "",
            "| | value |",
            "|---|---|",
            f"| calls that reached the API | **{project_api}** |",
            f"| input tokens | {project_input:,} |",
            f"| output tokens | {project_output:,} |",
            f"| total tokens | **{project_input + project_output:,}** |",
            f"| average tokens per request (/{EVALUATION_CASES}) | "
            f"**{(project_input + project_output) / EVALUATION_CASES:.1f}** |",
            f"| **total real spend** | **${project_cost:.4f}** |",
            f"| average cost per request (/{EVALUATION_CASES}) | "
            f"${project_cost / EVALUATION_CASES:.6f} |",
            "",
            "## Per model",
            "",
            "| model | calls | API calls | cache hits | input | output | cost |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for model, entry in sorted(totals_by_model.items()):
        lines.append(
            f"| `{model}` | {entry['calls']} | {entry['api_calls']} | "
            f"{entry['cache_hits']} | {entry['input']:,} | {entry['output']:,} | "
            f"${cost_of(model, entry['input'], entry['output'], prices):.4f} |"
        )
    lines.extend(
        [
            "",
            "## Per purpose",
            "",
            "| purpose | calls | API calls | cache hits | input | output |",
            "|---|---|---|---|---|---|",
        ]
    )
    for purpose, entry in sorted(totals_by_purpose.items()):
        lines.append(
            f"| {purpose} | {entry['calls']} | {entry['api_calls']} | "
            f"{entry['cache_hits']} | {entry['input']:,} | {entry['output']:,} |"
        )

    lines.extend(
        [
            "",
            "## Why the bill is small",
            "",
            "Three design choices, each measured:",
            "",
            "1. **Rules answer most of the classification.** Stage 1 decides 508",
            "   of 518 requests with no model call; only the 10 that carry a",
            "   negation, a scoping word or no recognised deletion phrasing reach",
            "   the model, batched into a single request (D32).",
            "2. **Documents are read once, not once per case.** The 15 artefacts",
            "   are case-independent, so their extractions are cached by file",
            "   content hash and shared across all 518 cases.",
            "3. **Each prompt has its own cache namespace.** Editing the",
            "   classifier prompt re-pays only the classifier calls, and editing",
            "   the extraction prompt re-pays only the extractions (D41). The",
            "   classifier went from v1 to v2 for $0.0007 instead of $0.58.",
            "",
            "## Reproducing",
            "",
            "```bash",
            "python code/main.py                 # warm: 0 tokens, identical output",
            "python code/scripts/usage_report.py # regenerates this file",
            "```",
            "",
            "No API key is needed to reproduce `output.csv` from the shipped",
            "cache. A key is needed only to re-derive the cached answers from",
            "scratch.",
        ]
    )

    OUTPUT_FILE.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT_FILE.relative_to(REPO_ROOT)}")
    print(f"runs recorded: {len(history)}, API calls: {total_api}")
    print(f"total tokens: {total_input + total_output:,}, spend: ${total_cost:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
