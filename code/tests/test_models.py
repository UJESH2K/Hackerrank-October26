"""Tests for the output-contract vocabularies and step serialisation."""

from __future__ import annotations

import json

from audit.models import (
    OPERATION_PHASE,
    OPERATION_RANK,
    OPERATION_STATE,
    STEP_FIELD_ORDER,
    CaseResult,
    DeletionStatus,
    Operation,
    Phase,
    State,
    Step,
)


def test_every_operation_has_exactly_one_state_and_phase() -> None:
    assert set(OPERATION_STATE) == set(Operation)
    assert set(OPERATION_PHASE) == set(Operation)


def test_operation_rank_follows_the_problem_statement_table() -> None:
    """DISABLE_DATA_FLOW and BLOCK_RESTORE precede the record-level remediations."""
    remediation = [
        operation
        for operation in Operation
        if OPERATION_PHASE[operation] is Phase.REMEDIATION
    ]
    assert remediation == [
        Operation.DISABLE_DATA_FLOW,
        Operation.BLOCK_RESTORE,
        Operation.DELETE_RECORD,
        Operation.REDACT_RECORD,
        Operation.ANONYMIZE_RECORD,
    ]
    assert [OPERATION_RANK[operation] for operation in remediation] == sorted(
        OPERATION_RANK[operation] for operation in remediation
    )


def test_step_serialises_with_the_canonical_field_set() -> None:
    step = Step(
        phase=Phase.FINDING,
        system_id="account_service",
        resource_id="account_005",
        operation=Operation.REPORT_REMAINING_RECORD,
        state=State.PRESENT,
        evidence_id="evidence_54",
        step_no=1,
    )
    assert tuple(step.to_dict()) == STEP_FIELD_ORDER
    assert step.to_dict()["state"] == "PRESENT"


def test_step_sort_key_orders_by_phase_then_operation_then_ids() -> None:
    verification = Step(
        phase=Phase.VERIFICATION,
        system_id="account_service",
        resource_id="account_001",
        operation=Operation.VERIFY_RECORD_ABSENT,
        state=State.ABSENT,
    )
    finding = Step(
        phase=Phase.FINDING,
        system_id="search_service",
        resource_id="search_900",
        operation=Operation.REPORT_REMAINING_RECORD,
        state=State.PRESENT,
        evidence_id="evidence_01",
    )
    assert sorted([verification, finding], key=lambda step: step.sort_key) == [
        finding,
        verification,
    ]


def test_renumbered_assigns_step_no_without_mutating() -> None:
    step = Step(
        phase=Phase.REMEDIATION,
        system_id="search_service",
        resource_id="search_900",
        operation=Operation.DELETE_RECORD,
        state=State.ABSENT,
    )
    assert step.step_no == 0
    assert step.renumbered(3).step_no == 3
    assert step.step_no == 0


def test_not_applicable_case_serialises_to_an_empty_array() -> None:
    result = CaseResult(case_id="case_1", deletion_status=DeletionStatus.NOT_APPLICABLE)
    assert result.steps_json() == "[]"


def test_steps_json_is_valid_json_in_array_order() -> None:
    steps = tuple(
        Step(
            phase=Phase.VERIFICATION,
            system_id="account_service",
            resource_id=f"account_{index}",
            operation=Operation.VERIFY_RECORD_ABSENT,
            state=State.ABSENT,
            evidence_id="evidence_54",
            step_no=index,
        )
        for index in (1, 2)
    )
    parsed = json.loads(
        CaseResult("case_1", DeletionStatus.COMPLETE, steps).steps_json()
    )
    assert [item["step_no"] for item in parsed] == [1, 2]
