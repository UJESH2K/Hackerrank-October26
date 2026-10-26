"""Tests for the output-contract validator."""

from __future__ import annotations

import json
from pathlib import Path

from audit.validate import validate_file

HEADER = "case_id,deletion_status,steps_json\n"


def write(tmp_path: Path, body: str, header: str = HEADER) -> Path:
    """Write an output CSV fixture."""
    path = tmp_path / "output.csv"
    path.write_text(header + body, encoding="utf-8")
    return path


def row(case_id: str, status: str, steps: list[dict[str, object]]) -> str:
    """One CSV row, quoted the way the writer quotes it."""
    payload = json.dumps(steps, separators=(",", ":")).replace('"', '""')
    return f'{case_id},{status},"{payload}"\n'


def step(
    number: int,
    phase: str,
    operation: str,
    state: str,
    evidence_id: str = "",
    resource_id: str = "account_1",
) -> dict[str, object]:
    """One step object."""
    return {
        "step_no": number,
        "phase": phase,
        "system_id": "account_service",
        "resource_id": resource_id,
        "operation": operation,
        "state": state,
        "evidence_id": evidence_id,
    }


COMPLETE_STEP = step(1, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_01")
FINDING_STEP = step(
    1, "FINDING", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01"
)
REMEDIATION_STEP = step(2, "REMEDIATION", "DELETE_RECORD", "ABSENT")
VERIFY_STEP = step(3, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT")


def test_a_valid_file_passes(tmp_path: Path) -> None:
    body = row("case_1", "COMPLETE", [COMPLETE_STEP]) + row(
        "case_2", "INCOMPLETE", [FINDING_STEP, REMEDIATION_STEP, VERIFY_STEP]
    )
    report = validate_file(write(tmp_path, body), expected_rows=2)
    assert report.ok, report.errors
    assert report.row_count == 2


def test_a_wrong_header_is_rejected(tmp_path: Path) -> None:
    report = validate_file(
        write(tmp_path, "", header="case,status,steps\n"), expected_rows=0
    )
    assert any("header" in error for error in report.errors)


def test_the_row_count_is_checked(tmp_path: Path) -> None:
    report = validate_file(write(tmp_path, row("case_1", "COMPLETE", [COMPLETE_STEP])))
    assert any("518" in error for error in report.errors)


def test_rows_must_be_in_natural_numeric_order(tmp_path: Path) -> None:
    body = row("case_100", "COMPLETE", [COMPLETE_STEP]) + row(
        "case_99", "COMPLETE", [COMPLETE_STEP]
    )
    report = validate_file(write(tmp_path, body), expected_rows=2)
    assert any("numeric" in error for error in report.errors)


def test_lexicographic_order_is_not_accepted(tmp_path: Path) -> None:
    """``case_100`` before ``case_99`` is exactly the mistake to catch."""
    body = row("case_9", "COMPLETE", [COMPLETE_STEP]) + row(
        "case_10", "COMPLETE", [COMPLETE_STEP]
    )
    assert validate_file(write(tmp_path, body), expected_rows=2).ok


def test_duplicate_case_ids_are_rejected(tmp_path: Path) -> None:
    body = row("case_1", "COMPLETE", [COMPLETE_STEP]) * 2
    report = validate_file(write(tmp_path, body), expected_rows=2)
    assert any("duplicate" in error for error in report.errors)


def test_not_applicable_must_have_an_empty_array(tmp_path: Path) -> None:
    good = validate_file(
        write(tmp_path, row("case_1", "NOT_APPLICABLE", [])), expected_rows=1
    )
    assert good.ok
    bad = validate_file(
        write(tmp_path, row("case_1", "NOT_APPLICABLE", [COMPLETE_STEP])),
        expected_rows=1,
    )
    assert any("empty array" in error for error in bad.errors)


def test_step_numbers_must_be_consecutive(tmp_path: Path) -> None:
    broken = dict(COMPLETE_STEP)
    broken["step_no"] = 7
    report = validate_file(
        write(tmp_path, row("case_1", "COMPLETE", [broken])), expected_rows=1
    )
    assert any("step_no" in error for error in report.errors)


def test_an_invalid_operation_state_pair_is_rejected(tmp_path: Path) -> None:
    broken = dict(COMPLETE_STEP)
    broken["state"] = "PRESENT"
    report = validate_file(
        write(tmp_path, row("case_1", "COMPLETE", [broken])), expected_rows=1
    )
    assert any("requires state" in error for error in report.errors)


def test_an_operation_in_the_wrong_phase_is_rejected(tmp_path: Path) -> None:
    broken = dict(COMPLETE_STEP)
    broken["phase"] = "FINDING"
    report = validate_file(
        write(tmp_path, row("case_1", "COMPLETE", [broken])), expected_rows=1
    )
    assert any("not a" in error for error in report.errors)


def test_phases_must_not_be_out_of_order(tmp_path: Path) -> None:
    steps = [
        step(1, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT"),
        step(2, "FINDING", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01"),
    ]
    report = validate_file(
        write(tmp_path, row("case_1", "INCOMPLETE", steps)), expected_rows=1
    )
    assert any("out of order" in error for error in report.errors)


def test_a_finding_must_cite_evidence(tmp_path: Path) -> None:
    uncited = dict(FINDING_STEP)
    uncited["evidence_id"] = ""
    steps = [uncited, REMEDIATION_STEP, VERIFY_STEP]
    report = validate_file(
        write(tmp_path, row("case_1", "INCOMPLETE", steps)), expected_rows=1
    )
    assert any("must cite evidence" in error for error in report.errors)


def test_incomplete_remediation_must_not_cite_evidence(tmp_path: Path) -> None:
    cited = dict(REMEDIATION_STEP)
    cited["evidence_id"] = "evidence_01"
    steps = [FINDING_STEP, cited, VERIFY_STEP]
    report = validate_file(
        write(tmp_path, row("case_1", "INCOMPLETE", steps)), expected_rows=1
    )
    assert any("empty evidence_id" in error for error in report.errors)


def test_a_complete_verification_must_cite_evidence(tmp_path: Path) -> None:
    uncited = dict(COMPLETE_STEP)
    uncited["evidence_id"] = ""
    report = validate_file(
        write(tmp_path, row("case_1", "COMPLETE", [uncited])), expected_rows=1
    )
    assert any("must cite evidence" in error for error in report.errors)


def test_a_complete_case_must_not_contain_findings(tmp_path: Path) -> None:
    report = validate_file(
        write(tmp_path, row("case_1", "COMPLETE", [FINDING_STEP])), expected_rows=1
    )
    assert any("must not contain findings" in error for error in report.errors)


def test_an_incomplete_case_must_contain_a_finding(tmp_path: Path) -> None:
    report = validate_file(
        write(tmp_path, row("case_1", "INCOMPLETE", [VERIFY_STEP])), expected_rows=1
    )
    assert any("must contain a finding" in error for error in report.errors)


def test_an_empty_complete_case_is_a_warning_not_an_error(tmp_path: Path) -> None:
    """Nothing attributable is a weak answer, not a malformed one."""
    report = validate_file(write(tmp_path, row("case_1", "COMPLETE", [])), expected_rows=1)
    assert report.ok
    assert any("no verification steps" in warning for warning in report.warnings)


def test_malformed_json_is_reported(tmp_path: Path) -> None:
    report = validate_file(
        write(tmp_path, 'case_1,COMPLETE,"[{not json}]"\n'), expected_rows=1
    )
    assert any("not valid JSON" in error for error in report.errors)


def test_an_unknown_status_is_reported(tmp_path: Path) -> None:
    report = validate_file(
        write(tmp_path, row("case_1", "DONE", [COMPLETE_STEP])), expected_rows=1
    )
    assert any("invalid deletion_status" in error for error in report.errors)


def test_extra_step_fields_are_reported(tmp_path: Path) -> None:
    extra = dict(COMPLETE_STEP)
    extra["note"] = "why"
    report = validate_file(
        write(tmp_path, row("case_1", "COMPLETE", [extra])), expected_rows=1
    )
    assert any("unexpected fields" in error for error in report.errors)
