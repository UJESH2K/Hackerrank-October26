"""Measure what the model layer changes, and cross-check the classifier.

Reports, in order:

* sample metrics before and after the model layer;
* every ``NOT_APPLICABLE`` case with its reason, and agreement with the
  ``CLASSIFY_REQUEST`` activity rows - a **diagnostic only**, never an input;
* records extracted per document;
* cases whose status or citations changed because of a document;
* the full 518-case status distribution and validator verdict;
* usage for a cold run and then a warm one.

Run from the repository root::

    python code/evaluation/check_llm_layer.py              # real model, needs a key
    python code/evaluation/check_llm_layer.py --stand-in   # no key needed

``--stand-in`` substitutes a scripted client for the API so the wiring can be
verified without spending anything. It is **not** a measurement of model
quality: the classification verdicts come from reading the request text for the
known non-deletion phrasings, and the document records are a fixture
transcribed by hand from the artefact. Anything it reports is the behaviour of
the pipeline *given* those answers.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CODE_DIR.parent

if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import yaml  # noqa: E402

from audit.classifier import Decider  # noqa: E402
from audit.llm import LlmGateway, LlmSettings  # noqa: E402
from audit.loaders import (  # noqa: E402
    REQUESTS_FILE,
    SAMPLE_REQUESTS_FILE,
    load_dataset,
    load_requests,
)
from audit.models import DeletionStatus  # noqa: E402
from audit.pipeline import audit, write_rows  # noqa: E402
from audit.validate import validate_file  # noqa: E402

DATASET_DIR = REPO_ROOT / "dataset"
PROMPTS_DIR = CODE_DIR / "prompts"
CACHE_DIR = CODE_DIR / ".cache"

#: ``CLASSIFY_REQUEST`` rows stating a request was ruled non-deletion. Used only
#: to cross-check the classifier after the fact.
CLASSIFY_PATTERN = re.compile(
    r"classified request for (?P<resource_id>\S+) as non-deletion"
)

#: Document types whose observations come from the model.
DOCUMENT_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}

# --------------------------------------------------------------------------- #
# The stand-in client. Not a model, and not a measurement of one.
# --------------------------------------------------------------------------- #

STAND_IN_NON_DELETION_MARKERS = (
    "not asking for deletion",
    "not a request to erase",
    "don't delete or change anything",
    "leave the account active",
    "keep the account as it is",
    "do not close the account",
    "without removing any of my records",
    "marketing lists only",
    "leave my profile and purchase history in place",
    "continue using the service",
)

#: Transcribed by hand from ``delivery_correction_attachment.pdf``, which states
#: "Support reference CS-4827 / support_001", the customer's name, email and
#: phone, and "captured 12 Sep 2026 07 30 UTC".
STAND_IN_ATTACHMENT = {
    "captured_at": "2026-09-12T07:30:00Z",
    "records": [
        {
            "resource_id": "support_001",
            "system_id": "support_platform",
            "record_kind": "support attachment",
            "status": "FOUND",
            "present": True,
            "identifiers": {
                "name": "Priya Raman",
                "email": "priya.raman@gmail.com",
                "phone": "+91 98765 73142",
                "user_reference": None,
            },
            "flags": {},
            "observed_at": "2026-09-12T07:00:00Z",
        }
    ],
}


class StandInMessages:
    """Scripted ``messages.create``."""

    def __init__(self) -> None:
        """Start with no calls recorded."""
        self.calls = 0

    def create(self, **request: Any) -> Any:
        """Answer a classification batch or a document extraction."""
        self.calls += 1
        blocks = request["messages"][0]["content"]
        if any(block.get("type") in {"image", "document"} for block in blocks):
            text = next(
                (block["text"] for block in blocks if block.get("type") == "text"), ""
            )
            payload = (
                STAND_IN_ATTACHMENT
                if "delivery_correction_attachment.pdf" in text
                else {"captured_at": None, "records": []}
            )
            return _reply(json.dumps(payload))

        answers = []
        for chunk in blocks[0]["text"].split("id: ")[1:]:
            identifier, _, body = chunk.partition("\n")
            lowered = body.casefold()
            is_deletion = not any(
                marker in lowered for marker in STAND_IN_NON_DELETION_MARKERS
            )
            answers.append(
                {
                    "id": identifier.strip(),
                    "is_deletion_request": is_deletion,
                    "request_type": "ACCOUNT_DELETION"
                    if is_deletion
                    else "ACCESS_REQUEST",
                    "confidence": 0.95,
                    "short_reason": "stand-in: read from the request text",
                }
            )
        return _reply(json.dumps(answers))


class StandInClient:
    """A client whose ``messages`` namespace is scripted."""

    def __init__(self) -> None:
        """Build the namespace."""
        self.messages = StandInMessages()


def _reply(text: str) -> Any:
    """A minimal response object with the fields the gateway reads."""

    class Block:
        type = "text"

        def __init__(self, value: str) -> None:
            self.text = value

    class Usage:
        def __init__(self) -> None:
            # Representative of a real reply of this size; the real run reads
            # the API's own usage field instead.
            self.input_tokens = 1800
            self.output_tokens = 220

    class Response:
        def __init__(self, value: str) -> None:
            self.content = [Block(value)]
            self.usage = Usage()

    return Response(text)


# --------------------------------------------------------------------------- #


def settings() -> LlmSettings:
    """Model ids and prices from the real config."""
    return LlmSettings.from_config(
        yaml.safe_load((CODE_DIR / "config.yaml").read_text(encoding="utf-8"))
    )


def run_audit(
    sample: bool,
    cache_dir: Path,
    stand_in: bool,
    enabled: bool = True,
) -> Any:
    """Audit the sample or the full set, with or without the model layer."""
    requests_file = DATASET_DIR / (SAMPLE_REQUESTS_FILE if sample else REQUESTS_FILE)
    dataset = load_dataset(DATASET_DIR, requests_file)
    gateway = LlmGateway(
        settings(),
        cache_dir=cache_dir / "llm",
        client=StandInClient() if stand_in else None,
        enabled=enabled,
    )
    return audit(
        dataset,
        cache_dir=cache_dir,
        learning_requests=load_requests(DATASET_DIR / REQUESTS_FILE),
        gateway=gateway,
        prompts_dir=PROMPTS_DIR,
    )


def score_against_samples(run: Any) -> dict[str, float]:
    """Sample metrics for one run."""
    import csv

    gold_path = DATASET_DIR / "sample_output.csv"
    with gold_path.open(encoding="utf-8", newline="") as handle:
        gold_rows = list(csv.DictReader(handle))
    gold = {
        row["case_id"]: (row["deletion_status"], json.loads(row["steps_json"]))
        for row in gold_rows
    }

    def key(step: dict[str, Any]) -> tuple[str, ...]:
        return (
            step["phase"],
            step["system_id"],
            step["resource_id"],
            step["operation"],
            step["state"],
            step["evidence_id"],
        )

    predicted = {
        result.case_id: (str(result.deletion_status), json.loads(result.steps_json()))
        for result in run.results
    }
    status_ok = sum(
        1
        for case_id, (status, _) in gold.items()
        if predicted.get(case_id, ("", []))[0] == status
    )
    true_positive = false_positive = false_negative = exact = 0
    for case_id, (status, steps) in gold.items():
        predicted_status, predicted_steps = predicted.get(case_id, ("", []))
        wanted = {key(step) for step in steps}
        got = {key(step) for step in predicted_steps}
        true_positive += len(wanted & got)
        false_positive += len(got - wanted)
        false_negative += len(wanted - got)
        if status == predicted_status and wanted == got:
            exact += 1
    precision = true_positive / (true_positive + false_positive or 1)
    recall = true_positive / (true_positive + false_negative or 1)
    return {
        "status": status_ok,
        "cases": len(gold),
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall or 1),
        "exact": exact,
    }


def main(argv: list[str] | None = None) -> int:
    """Produce the report."""
    parser = argparse.ArgumentParser(
        prog="python code/evaluation/check_llm_layer.py",
        description="Measure what the model layer changes.",
    )
    parser.add_argument(
        "--stand-in",
        action="store_true",
        help="use a scripted client instead of the API (no key needed)",
    )
    args = parser.parse_args(argv)

    if args.stand_in:
        print("=" * 72)
        print("STAND-IN MODE: a scripted client replaces the API.")
        print("This verifies the pipeline's wiring, not the model's quality.")
        print("=" * 72)
        print()

    with tempfile.TemporaryDirectory() as temporary:
        cold_cache = Path(temporary) / "cold"

        print("## 1. Sample metrics, before and after the model layer")
        print()
        before = score_against_samples(
            run_audit(sample=True, cache_dir=cold_cache / "off", stand_in=False, enabled=False)
        )
        after = score_against_samples(
            run_audit(sample=True, cache_dir=cold_cache / "on", stand_in=args.stand_in)
        )
        print(f"  {'metric':<28} {'before':>10} {'after':>10}")
        print(f"  {'status accuracy':<28} {before['status']:>7}/32 {after['status']:>7}/32")
        print(f"  {'step precision':<28} {before['precision']:>9.1%} {after['precision']:>9.1%}")
        print(f"  {'step recall':<28} {before['recall']:>9.1%} {after['recall']:>9.1%}")
        print(f"  {'step f1':<28} {before['f1']:>9.1%} {after['f1']:>9.1%}")
        print(f"  {'exact cases':<28} {before['exact']:>7}/32 {after['exact']:>7}/32")
        print()

        full = run_audit(sample=False, cache_dir=cold_cache / "full", stand_in=args.stand_in)

        print("## 2. NOT_APPLICABLE cases and the CLASSIFY_REQUEST cross-check")
        print()
        dataset = load_dataset(DATASET_DIR, DATASET_DIR / REQUESTS_FILE)
        texts = {request.case_id: request.request_text for request in dataset.requests}
        not_applicable = list(full.not_applicable())
        print(f"  classified as non-deletion: {len(not_applicable)}")
        for case_id in not_applicable:
            decision = full.decisions.get(case_id)
            reason = decision.reason if decision else "?"
            kind = str(decision.request_type) if decision else "?"
            print(f"    {case_id:<10} {kind:<20} {reason}")
            print(f"      {' '.join(texts.get(case_id, '').split())[:100]}")
        activity_rows = [
            activity
            for activity in dataset.activities
            if activity.event_type == "CLASSIFY_REQUEST"
            and CLASSIFY_PATTERN.search(activity.details)
        ]
        print()
        print(
            f"  CLASSIFY_REQUEST non-deletion rows: {len(activity_rows)} "
            "(diagnostic only; never an input)"
        )
        print(
            f"  agreement on count: "
            f"{'yes' if len(activity_rows) == len(not_applicable) else 'NO'}"
        )
        print()

        deciders = dict.fromkeys(Decider, 0)
        for decision in full.decisions.values():
            deciders[decision.decided_by] += 1
        print("  how each request was decided")
        for decider, count in deciders.items():
            print(f"    {str(decider):<10} {count:>5}")
        print()

        print("## 3. Records extracted per document")
        print()
        manifest = {
            evidence.file_id: evidence
            for evidence in dataset.evidence_files
            if Path(evidence.file_path).suffix.lower() in DOCUMENT_SUFFIXES
        }
        total_records = 0
        for file_id, evidence in sorted(manifest.items()):
            observations = full.index.by_evidence.get(file_id, ())
            total_records += len(observations)
            resources = ", ".join(sorted({item.resource_id for item in observations}))
            print(
                f"    {file_id:<12} {Path(evidence.file_path).name:<44} "
                f"{len(observations):>3}  {resources}"
            )
        print()
        print(f"  documents: {len(manifest)}, observations from documents: {total_records}")
        print()

        print("## 4. Cases changed by a document")
        print()
        print(
            "  The hand-built cases that depend on documents are in the sample "
            "set, so both sets are compared."
        )
        print()
        document_ids = set(manifest)
        total_changed = 0
        for label, sample_set in (("sample", True), ("evaluation", False)):
            after_run = (
                run_audit(sample=True, cache_dir=cold_cache / "on", stand_in=args.stand_in)
                if sample_set
                else full
            )
            before_run = run_audit(
                sample=sample_set,
                cache_dir=cold_cache / f"baseline-{label}",
                stand_in=False,
                enabled=False,
            )
            before_rows = {
                result.case_id: (str(result.deletion_status), result.steps_json())
                for result in before_run.results
            }
            changed = 0
            for result in after_run.results:
                previous = before_rows.get(result.case_id)
                now = (str(result.deletion_status), result.steps_json())
                if previous == now:
                    continue
                cites = {
                    step.evidence_id for step in result.steps if step.evidence_id
                } & document_ids
                if not cites:
                    # Changed by the classifier, not by a document.
                    continue
                changed += 1
                total_changed += 1
                print(
                    f"    {label:<11} {result.case_id:<10} {previous[0]:<15} -> "
                    f"{str(result.deletion_status):<15} cites {sorted(cites)}"
                )
            print(f"    {label:<11} cases changed by a document: {changed}")
        print()
        print(f"  total cases changed by document evidence: {total_changed}")
        print()

        print("## 5. Status distribution over all 518 cases")
        print()
        statuses = dict.fromkeys(DeletionStatus, 0)
        for result in full.results:
            statuses[result.deletion_status] += 1
        for status, count in statuses.items():
            print(f"    {str(status):<16} {count:>5}")
        steps = sum(len(result.steps) for result in full.results)
        print(f"    {'steps':<16} {steps:>5}")
        print()

        print("## 6. Validator")
        print()
        output = Path(temporary) / "llm_output.csv"
        write_rows(output, full.rows())
        report = validate_file(output, expected_rows=518)
        print(f"    {report.summary()}")
        for error in report.errors[:10]:
            print(f"      error:   {error}")
        for warning in report.warnings[:10]:
            print(f"      warning: {warning}")
        print()

        print("## 7. Usage")
        print()
        cost_settings = settings()
        for label, run in (("cold", full),):
            usage = run.usage
            print(f"    {label} run")
            print(f"      run type       {'cold' if usage.is_cold else 'warm'}")
            print(f"      calls          {usage.calls} ({usage.api_calls} to the API)")
            print(f"      cache hits     {usage.cache_hits}")
            print(f"      input tokens   {usage.input_tokens}")
            print(f"      output tokens  {usage.output_tokens}")
            print(f"      cost estimate  ${usage.cost(cost_settings):.4f}")
            for model, totals in sorted(usage.by_model().items()):
                print(f"        {model:<20} {totals}")
            for purpose, totals in sorted(usage.by_purpose().items()):
                print(f"        {purpose:<20} {totals}")

        warm = run_audit(sample=False, cache_dir=cold_cache / "full", stand_in=args.stand_in)
        usage = warm.usage
        print()
        print("    warm run (same cache)")
        print(f"      run type       {'cold' if usage.is_cold else 'warm'}")
        print(f"      calls          {usage.calls} ({usage.api_calls} to the API)")
        print(f"      cache hits     {usage.cache_hits}")
        print(f"      input tokens   {usage.input_tokens}")
        print(f"      output tokens  {usage.output_tokens}")
        print(f"      cost estimate  ${usage.cost(cost_settings):.4f}")
        identical = [
            (result.case_id, str(result.deletion_status), result.steps_json())
            for result in warm.results
        ] == [
            (result.case_id, str(result.deletion_status), result.steps_json())
            for result in full.results
        ]
        print(f"      answers identical to the cold run: {'yes' if identical else 'NO'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
