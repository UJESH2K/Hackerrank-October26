"""Validate a finished ``output.csv`` against the submission contract.

Run as part of every pipeline run, so a contract violation fails loudly during
development rather than silently at grading time. The checks mirror the
requirements listed in the problem statement and in AGENTS.md §6.3-6.6.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from audit.loaders import case_sort_key
from audit.models import (
    OPERATION_PHASE,
    OPERATION_STATE,
    PHASE_RANK,
    STEP_FIELD_ORDER,
    DeletionStatus,
    Operation,
    Phase,
)

#: The exact header the grader expects.
EXPECTED_HEADER: tuple[str, ...] = ("case_id", "deletion_status", "steps_json")

#: The number of evaluation rows the submission must contain.
EXPECTED_ROWS = 518


@dataclass(slots=True)
class ValidationReport:
    """Everything wrong with an output file, or nothing.

    ``errors`` are contract violations: the grader would reject or misread the
    row. ``warnings`` are answers that are valid but weak, such as a ``COMPLETE``
    case with no verification steps - nothing is malformed, but nothing is
    proven either.
    """

    row_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether the file satisfies the contract."""
        return not self.errors

    def summary(self) -> str:
        """A one-line verdict, or the first few problems."""
        if self.ok:
            tail = f", {len(self.warnings)} warnings" if self.warnings else ""
            return (
                f"output.csv valid: {self.row_count} rows, contract checks passed{tail}"
            )
        head = "; ".join(self.errors[:5])
        more = f" (+{len(self.errors) - 5} more)" if len(self.errors) > 5 else ""
        return f"output.csv invalid: {head}{more}"


def validate_file(path: Path, expected_rows: int | None = EXPECTED_ROWS) -> ValidationReport:
    """Validate an output CSV on disk."""
    report = ValidationReport()
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = tuple(next(reader))
        except StopIteration:
            report.errors.append("file is empty")
            return report
        if header != EXPECTED_HEADER:
            report.errors.append(f"header is {header!r}, expected {EXPECTED_HEADER!r}")
        rows = list(reader)
    report.row_count = len(rows)
    if expected_rows is not None and len(rows) != expected_rows:
        report.errors.append(f"{len(rows)} data rows, expected {expected_rows}")
    _validate_rows(rows, report)
    return report


def _validate_rows(rows: Sequence[Sequence[str]], report: ValidationReport) -> None:
    """Check ordering and every row's contents."""
    case_ids = [row[0] for row in rows if row]
    if case_ids != sorted(case_ids, key=case_sort_key):
        report.errors.append("rows are not in natural numeric case_id order")
    if len(set(case_ids)) != len(case_ids):
        report.errors.append("duplicate case_id values")

    for row in rows:
        if len(row) != len(EXPECTED_HEADER):
            report.errors.append(f"{row[0] if row else '?'}: expected 3 columns")
            continue
        case_id, status_text, steps_json = row
        try:
            status = DeletionStatus(status_text)
        except ValueError:
            report.errors.append(f"{case_id}: invalid deletion_status {status_text!r}")
            continue
        try:
            steps = json.loads(steps_json)
        except json.JSONDecodeError as error:
            report.errors.append(f"{case_id}: steps_json is not valid JSON ({error})")
            continue
        if not isinstance(steps, list):
            report.errors.append(f"{case_id}: steps_json is not a JSON array")
            continue
        _validate_steps(case_id, status, steps, report)


def _validate_steps(
    case_id: str,
    status: DeletionStatus,
    steps: list[object],
    report: ValidationReport,
) -> None:
    """Check one case's step array."""
    if status is DeletionStatus.NOT_APPLICABLE:
        if steps:
            report.errors.append(f"{case_id}: NOT_APPLICABLE must have an empty array")
        return
    if not steps:
        if status is DeletionStatus.INCOMPLETE:
            report.errors.append(f"{case_id}: INCOMPLETE must contain a finding")
        else:
            # Valid but unproven: nothing could be attributed to the subject.
            report.warnings.append(f"{case_id}: COMPLETE with no verification steps")
        return

    previous_rank = -1
    has_finding = False
    for position, raw in enumerate(steps, start=1):
        if not isinstance(raw, dict):
            report.errors.append(f"{case_id} step {position}: not a JSON object")
            continue
        missing = set(STEP_FIELD_ORDER) - set(raw)
        extra = set(raw) - set(STEP_FIELD_ORDER)
        if missing:
            report.errors.append(
                f"{case_id} step {position}: missing fields {sorted(missing)}"
            )
            continue
        if extra:
            report.errors.append(
                f"{case_id} step {position}: unexpected fields {sorted(extra)}"
            )
        if raw["step_no"] != position:
            report.errors.append(
                f"{case_id} step {position}: step_no is {raw['step_no']!r}"
            )
        try:
            operation = Operation(raw["operation"])
            phase = Phase(raw["phase"])
        except ValueError:
            report.errors.append(
                f"{case_id} step {position}: unknown phase/operation "
                f"{raw['phase']!r}/{raw['operation']!r}"
            )
            continue
        if OPERATION_PHASE[operation] is not phase:
            report.errors.append(
                f"{case_id} step {position}: {operation} is not a {phase} operation"
            )
        if raw["state"] != str(OPERATION_STATE[operation]):
            report.errors.append(
                f"{case_id} step {position}: {operation} requires state "
                f"{OPERATION_STATE[operation]}, got {raw['state']!r}"
            )
        if PHASE_RANK[phase] < previous_rank:
            report.errors.append(f"{case_id} step {position}: phases out of order")
        previous_rank = max(previous_rank, PHASE_RANK[phase])

        if not str(raw["system_id"]).strip():
            report.errors.append(f"{case_id} step {position}: empty system_id")
        if not str(raw["resource_id"]).strip():
            report.errors.append(f"{case_id} step {position}: empty resource_id")

        cited = bool(str(raw["evidence_id"]).strip())
        if phase is Phase.FINDING:
            has_finding = True
            if not cited:
                report.errors.append(
                    f"{case_id} step {position}: a finding must cite evidence"
                )
        elif status is DeletionStatus.INCOMPLETE and cited:
            report.errors.append(
                f"{case_id} step {position}: proposed work must have an empty evidence_id"
            )
        elif status is DeletionStatus.COMPLETE and not cited:
            report.errors.append(
                f"{case_id} step {position}: a COMPLETE verification must cite evidence"
            )

    if status is DeletionStatus.COMPLETE and has_finding:
        report.errors.append(f"{case_id}: COMPLETE cases must not contain findings")
    if status is DeletionStatus.INCOMPLETE and not has_finding:
        report.errors.append(f"{case_id}: INCOMPLETE cases must contain a finding")
