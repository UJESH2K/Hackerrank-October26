"""Score predictions against gold answers, for the samples and our own cases.

Three ways to run it::

    python code/evaluation/main.py                  # the 32 public samples
    python code/evaluation/main.py --suite all      # samples + every custom case
    python code/evaluation/main.py --dataset-dir code/evaluation/custom_cases/email_chain

A custom case is a self-contained dataset directory holding the real
participant-facing file formats plus a hand-written ``expected_output.csv``.
Those run with ``--no-llm``, so the suite is free, offline and reproducible; the
public samples use whatever the model layer is configured for.

Results are written to ``code/evaluation/results/`` as JSON, with a markdown
summary at ``results/latest_summary.md``.

This is the only module permitted to read gold answers; the audit pipeline never
does (decision D6).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CODE_DIR.parent

if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import yaml  # noqa: E402

from audit.evidence.registry import parse_all  # noqa: E402
from audit.llm import LlmGateway, LlmSettings  # noqa: E402
from audit.loaders import (  # noqa: E402
    REQUESTS_FILE,
    SAMPLE_REQUESTS_FILE,
    case_sort_key,
    load_dataset,
    load_requests,
)
from audit.pipeline import audit  # noqa: E402
from evaluation.metrics import STATUSES, Counts, SuiteScore, score_case  # noqa: E402

DATASET_DIR = REPO_ROOT / "dataset"
CUSTOM_CASES_DIR = CODE_DIR / "evaluation" / "custom_cases"
RESULTS_DIR = CODE_DIR / "evaluation" / "results"
PROMPTS_DIR = CODE_DIR / "prompts"
CACHE_DIR = CODE_DIR / ".cache"

#: Gold answers for the public samples.
SAMPLE_GOLD = DATASET_DIR / "sample_output.csv"

#: What a custom case calls its gold file.
EXPECTED_OUTPUT = "expected_output.csv"

#: Optional per-fixture recorded classifier answers, so a fixture that needs
#: an intent verdict still runs offline and free.
RECORDED_INTENTS = "recorded_intents.csv"


@dataclass(frozen=True, slots=True)
class Suite:
    """One set of cases to score."""

    name: str
    dataset_dir: Path
    requests_file: Path
    gold_file: Path
    use_llm: bool


def read_output(path: Path) -> dict[str, tuple[str, list[dict[str, Any]]]]:
    """Read an output CSV into ``case_id -> (status, steps)``."""
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {
        row["case_id"]: (row["deletion_status"], json.loads(row["steps_json"]))
        for row in rows
    }


def unrecoverable_resources(
    dataset_dir: Path, gold: dict[str, tuple[str, list[dict[str, Any]]]]
) -> set[str]:
    """Gold resources the dataset contains nowhere.

    Detected from the data rather than listed: a resource that appears in no
    parsed observation and no activity row cannot be found by any method, so a
    case that misses it is not a defect in any module (decision D22).
    """
    dataset = load_dataset(dataset_dir, dataset_dir / REQUESTS_FILE)
    parsed = parse_all(dataset, None)
    seen = {observation.resource_id for observation in parsed.observations}
    seen |= {activity.resource_id for activity in dataset.activities}
    wanted = {
        str(step["resource_id"]) for _status, steps in gold.values() for step in steps
    }
    return wanted - seen


def _gateway(use_llm: bool) -> LlmGateway:
    """A gateway configured from ``config.yaml``."""
    settings = LlmSettings.from_config(
        yaml.safe_load((CODE_DIR / "config.yaml").read_text(encoding="utf-8"))
    )
    return LlmGateway(settings, cache_dir=CACHE_DIR / "llm", enabled=use_llm)


def read_recorded_intents(path: Path) -> dict[str, Any] | None:
    """Read a fixture's recorded classifier answers, if it has any.

    A fixture whose expected answer depends on request intent cannot wait for a
    model: the suite must stay free and offline. Recording the verdict keeps the
    fixture honest about where it came from - the file is right there next to the
    expected output - while leaving the pipeline's own classifier untouched.
    """
    if not path.is_file():
        return None
    from audit.classifier import Decider, IntentDecision, RequestType

    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    decisions: dict[str, Any] = {}
    for row in rows:
        is_deletion = row["is_deletion_request"].strip().casefold() == "true"
        decisions[row["case_id"]] = IntentDecision(
            case_id=row["case_id"],
            is_deletion_request=is_deletion,
            request_type=RequestType(row["request_type"].strip()),
            confidence=float(row["confidence"]),
            reason=f"recorded: {row['reason']}",
            decided_by=Decider.MODEL,
        )
    return decisions


def run_pipeline(suite: Suite) -> dict[str, tuple[str, list[dict[str, Any]]]]:
    """Audit a suite's requests in-process and return the predictions."""
    dataset = load_dataset(suite.dataset_dir, suite.requests_file)
    full_requests = suite.dataset_dir / REQUESTS_FILE
    learning = (
        load_requests(full_requests)
        if full_requests.is_file() and full_requests != suite.requests_file
        else None
    )
    run = audit(
        dataset,
        cache_dir=CACHE_DIR,
        learning_requests=learning,
        gateway=_gateway(suite.use_llm),
        prompts_dir=PROMPTS_DIR,
        decisions=read_recorded_intents(suite.dataset_dir / RECORDED_INTENTS),
    )
    return {
        result.case_id: (str(result.deletion_status), json.loads(result.steps_json()))
        for result in run.results
    }


def identity_outcome(
    suite: Suite, gold: dict[str, tuple[str, list[dict[str, Any]]]]
) -> tuple[int, int]:
    """How often the request email resolved, and resolved to the right person.

    "Right" means the resolved subject owns at least one of the case's gold
    resources, judged through attribution. A case whose gold resources are all
    unreachable cannot be judged this way and counts as resolved but not correct.
    """
    from audit.attribution import Attributor
    from audit.evidence.index import build_index
    from audit.identity import build_resolver

    dataset = load_dataset(suite.dataset_dir, suite.requests_file)
    parsed = parse_all(dataset, None)
    index = build_index(dataset, parsed.observations, parsed.coverage)
    resolver = build_resolver(dataset)
    attributor = Attributor(dataset, index, resolver, dataset.requests)
    attributions = attributor.attribute_all(dataset.requests)

    resolved = 0
    correct = 0
    for request in dataset.requests:
        attribution = attributions[request.case_id]
        if attribution.subject is None:
            continue
        resolved += 1
        wanted = {
            str(step["resource_id"]) for step in gold.get(request.case_id, ("", []))[1]
        }
        if wanted & attribution.resource_ids():
            correct += 1
    return (resolved, correct)


def _score(
    name: str,
    gold: dict[str, tuple[str, list[dict[str, Any]]]],
    predicted: dict[str, tuple[str, list[dict[str, Any]]]],
    unrecoverable: set[str],
) -> SuiteScore:
    """Fold per-case scores into one suite score."""
    score = SuiteScore(name=name)
    score.field_hits = {"state": 0, "evidence_id": 0, "step_no": 0}
    for case_id in sorted(gold, key=case_sort_key):
        gold_status, gold_steps = gold[case_id]
        predicted_status, predicted_steps = predicted.get(case_id, ("MISSING", []))
        case, steps, resources, fields, matched = score_case(
            case_id,
            gold_status,
            gold_steps,
            predicted_status,
            predicted_steps,
            unrecoverable,
        )
        score.cases.append(case)
        score.steps.true_positive += steps.true_positive
        score.steps.false_positive += steps.false_positive
        score.steps.false_negative += steps.false_negative
        score.resources.true_positive += resources.true_positive
        score.resources.false_positive += resources.false_positive
        score.resources.false_negative += resources.false_negative
        for field_name, hits in fields.items():
            score.field_hits[field_name] = score.field_hits.get(field_name, 0) + hits
        score.field_total += matched
    return score


def score_suite(suite: Suite) -> SuiteScore:
    """Score one suite end to end."""
    gold = read_output(suite.gold_file)
    predicted = run_pipeline(suite)
    unrecoverable = unrecoverable_resources(suite.dataset_dir, gold)
    score = _score(suite.name, gold, predicted, unrecoverable)
    score.identity_resolved, score.identity_correct = identity_outcome(suite, gold)
    return score


def discover_custom_cases() -> list[Suite]:
    """Every custom case directory holding an expected output."""
    if not CUSTOM_CASES_DIR.is_dir():
        return []
    suites: list[Suite] = []
    for directory in sorted(CUSTOM_CASES_DIR.iterdir()):
        gold = directory / EXPECTED_OUTPUT
        requests = directory / REQUESTS_FILE
        if gold.is_file() and requests.is_file():
            suites.append(
                Suite(
                    name=directory.name,
                    dataset_dir=directory,
                    requests_file=requests,
                    gold_file=gold,
                    # Custom cases must be free and reproducible, so no model.
                    use_llm=False,
                )
            )
    return suites


def sample_suite(use_llm: bool) -> Suite:
    """The 32 public sample cases."""
    return Suite(
        name="samples",
        dataset_dir=DATASET_DIR,
        requests_file=DATASET_DIR / SAMPLE_REQUESTS_FILE,
        gold_file=SAMPLE_GOLD,
        use_llm=use_llm,
    )


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def print_suite(score: SuiteScore, verbose: bool = True) -> None:
    """Print one suite's full report."""
    total = score.total or 1
    print(f"## {score.name}  ({score.total} cases)")
    print()
    print(
        f"  status accuracy        {score.status_correct}/{score.total} = "
        f"{score.status_correct / total:.1%}"
    )
    print()
    matrix = score.confusion()
    corner = "gold \\ predicted"
    print("  " + f"{corner:<18}" + "".join(f"{name:>16}" for name in STATUSES))
    for gold_status in STATUSES:
        row = "".join(f"{matrix[gold_status].get(name, 0):>16}" for name in STATUSES)
        print(f"  {gold_status:<18}{row}")
    print()
    print("  step identity (phase, operation, system_id, resource_id)")
    print(f"    precision            {score.steps.precision:.1%}")
    print(f"    recall               {score.steps.recall:.1%}")
    print(f"    f1                   {score.steps.f1:.1%}")
    gold_steps = score.steps.true_positive + score.steps.false_negative
    predicted_steps = score.steps.true_positive + score.steps.false_positive
    print(f"    gold / predicted     {gold_steps} / {predicted_steps}")
    print()
    print(f"  field accuracy on {score.field_total} matched steps")
    for name in ("state", "evidence_id", "step_no"):
        print(f"    {name:<20} {score.field_accuracy(name):.1%}")
    print()
    print("  resource discovery")
    print(f"    recall               {score.resources.recall:.1%}")
    print(f"    precision            {score.resources.precision:.1%}")
    print()
    print("  sequence")
    print(
        f"    exact                {score.sequence_exact}/{score.total} = "
        f"{score.sequence_exact / total:.1%}"
    )
    print(f"    mean edit distance   {score.mean_sequence_distance:.4f}")
    print()
    print("  identity")
    print(f"    resolved             {score.identity_resolved}/{score.total}")
    print(
        f"    led to a gold resource  {score.identity_correct}/{score.total} = "
        f"{score.identity_correct / total:.1%}"
    )
    print()
    print(
        f"  exact cases            {score.exact}/{score.total} = "
        f"{score.exact / total:.1%}"
    )
    print()
    causes = score.causes()
    if causes:
        print("  mismatch causes")
        for cause, count in causes.items():
            print(f"    {cause:<20} {count}")
        print()
    if verbose:
        failures = [case for case in score.cases if case.cause]
        if failures:
            print("  failing cases")
            for case in failures:
                print(
                    f"    {case.case_id:<12} {case.cause:<14} "
                    f"gold={case.gold_status}({case.gold_steps}) "
                    f"pred={case.predicted_status}({case.predicted_steps})"
                )
                if case.missing_resources:
                    print(f"       missing: {', '.join(case.missing_resources)}")
                if case.extra_resources:
                    print(f"       extra:   {', '.join(case.extra_resources)}")
                for error in case.field_errors:
                    print(f"       field:   {error}")
            print()


def combined_table(scores: Sequence[SuiteScore]) -> list[str]:
    """One row per suite, for the combined view."""
    lines = [
        f"  {'suite':<24} {'cases':>5} {'status':>8} {'step f1':>8} "
        f"{'seq':>7} {'exact':>7}",
        f"  {'-' * 24} {'-' * 5} {'-' * 8} {'-' * 8} {'-' * 7} {'-' * 7}",
    ]
    totals = Counts()
    cases = exact = status = 0
    for score in scores:
        divisor = score.total or 1
        lines.append(
            f"  {score.name:<24} {score.total:>5} "
            f"{score.status_correct / divisor:>7.1%} "
            f"{score.steps.f1:>7.1%} "
            f"{score.sequence_exact / divisor:>6.1%} "
            f"{score.exact / divisor:>6.1%}"
        )
        totals.true_positive += score.steps.true_positive
        totals.false_positive += score.steps.false_positive
        totals.false_negative += score.steps.false_negative
        cases += score.total
        exact += score.exact
        status += score.status_correct
    divisor = cases or 1
    lines.append(
        f"  {'TOTAL':<24} {cases:>5} {status / divisor:>7.1%} "
        f"{totals.f1:>7.1%} {'':>7} {exact / divisor:>6.1%}"
    )
    return lines


def write_results(scores: Sequence[SuiteScore]) -> tuple[Path, Path]:
    """Write the JSON report and the markdown summary."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "suites": [score.as_dict() for score in scores],
    }
    rendered = json.dumps(payload, indent=1, ensure_ascii=False)
    json_path = RESULTS_DIR / f"results_{stamp}.json"
    json_path.write_text(rendered, encoding="utf-8")
    (RESULTS_DIR / "latest.json").write_text(rendered, encoding="utf-8")

    lines = [
        "# Evaluation summary",
        "",
        f"Recorded {payload['recorded_at']} by `python code/evaluation/main.py`.",
        "",
        "| suite | cases | status accuracy | step f1 | sequence exact | exact cases |",
        "|---|---|---|---|---|---|",
    ]
    for score in scores:
        divisor = score.total or 1
        lines.append(
            f"| {score.name} | {score.total} | "
            f"{score.status_correct}/{score.total} "
            f"({score.status_correct / divisor:.1%}) | "
            f"{score.steps.f1:.1%} | "
            f"{score.sequence_exact}/{score.total} | "
            f"{score.exact}/{score.total} |"
        )
    for score in scores:
        divisor = score.total or 1
        lines.extend(
            [
                "",
                f"## {score.name}",
                "",
                f"- cases: **{score.total}**",
                f"- status accuracy: **{score.status_correct / divisor:.1%}**",
                f"- step precision / recall / f1: "
                f"**{score.steps.precision:.1%} / {score.steps.recall:.1%} / "
                f"{score.steps.f1:.1%}**",
                f"- resource discovery recall: **{score.resources.recall:.1%}**",
                f"- field accuracy (state / evidence_id / step_no): "
                f"**{score.field_accuracy('state'):.1%} / "
                f"{score.field_accuracy('evidence_id'):.1%} / "
                f"{score.field_accuracy('step_no'):.1%}** "
                f"on {score.field_total} matched steps",
                f"- sequence exact: **{score.sequence_exact}/{score.total}**, "
                f"mean normalised edit distance **{score.mean_sequence_distance:.4f}**",
                f"- identity led to a gold resource: "
                f"**{score.identity_correct}/{score.total}**",
                f"- exact cases: **{score.exact}/{score.total}**",
                "",
            ]
        )
        causes = score.causes()
        if causes:
            lines.append(
                "Mismatch causes: "
                + ", ".join(f"{cause} {count}" for cause, count in causes.items())
            )
            lines.append("")
        failures = [case for case in score.cases if case.cause]
        if failures:
            lines.extend(
                [
                    "| case | cause | gold | predicted | missing | extra |",
                    "|---|---|---|---|---|---|",
                ]
            )
            for case in failures:
                lines.append(
                    f"| {case.case_id} | {case.cause} | "
                    f"{case.gold_status}({case.gold_steps}) | "
                    f"{case.predicted_status}({case.predicted_steps}) | "
                    f"{', '.join(case.missing_resources) or '-'} | "
                    f"{', '.join(case.extra_resources) or '-'} |"
                )
            lines.append("")

    summary_path = RESULTS_DIR / "latest_summary.md"
    summary_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return (json_path, summary_path)


def main(argv: list[str] | None = None) -> int:
    """Score the requested suites and write the reports."""
    parser = argparse.ArgumentParser(
        prog="python code/evaluation/main.py",
        description="Score deletion-audit predictions against gold answers.",
    )
    parser.add_argument(
        "--suite",
        choices=("samples", "custom", "all"),
        default="samples",
        help="which cases to score (default: the public samples)",
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=None,
        help="score one fixture directory holding expected_output.csv",
    )
    parser.add_argument(
        "--predictions",
        type=Path,
        default=None,
        help="score an existing output CSV against the samples instead of running",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="score the deterministic pipeline only",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print the combined table only",
    )
    args = parser.parse_args(argv)

    if args.predictions is not None:
        gold = read_output(SAMPLE_GOLD)
        score = _score(
            f"samples ({args.predictions.name})",
            gold,
            read_output(args.predictions),
            unrecoverable_resources(DATASET_DIR, gold),
        )
        print_suite(score, verbose=not args.quiet)
        return 0

    suites: list[Suite] = []
    if args.dataset_dir is not None:
        directory = args.dataset_dir
        suites.append(
            Suite(
                name=directory.name,
                dataset_dir=directory,
                requests_file=directory / REQUESTS_FILE,
                gold_file=directory / EXPECTED_OUTPUT,
                use_llm=False,
            )
        )
    else:
        if args.suite in ("samples", "all"):
            suites.append(sample_suite(use_llm=not args.no_llm))
        if args.suite in ("custom", "all"):
            suites.extend(discover_custom_cases())

    if not suites:
        print("no suites to score")
        return 1

    scores = [score_suite(suite) for suite in suites]
    if not args.quiet:
        for score in scores:
            print_suite(score)
    print("## combined")
    print()
    for line in combined_table(scores):
        print(line)
    print()
    json_path, summary_path = write_results(scores)
    print(f"wrote {json_path.relative_to(REPO_ROOT)}")
    print(f"wrote {summary_path.relative_to(REPO_ROOT)}")

    failures = sum(1 for score in scores for case in score.cases if case.cause)
    print(f"failing cases: {failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
