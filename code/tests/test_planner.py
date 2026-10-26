"""Tests for step ordering, the needs-action rule and the status decision."""

from __future__ import annotations

import pytest

from audit.attribution import AttributedResource, LinkKind
from audit.models import (
    OPERATION_PHASE,
    OPERATION_STATE,
    Operation,
    Phase,
    Step,
)
from audit.planner import needs_action, order_steps
from audit.policy import Treatment
from audit.state import ResourceState

#: The operation order the problem statement's table specifies, in full. This is
#: the table-driven expectation: if the enum is reordered, this test fails.
TABLE_ORDER: tuple[Operation, ...] = (
    Operation.REPORT_REMAINING_RECORD,
    Operation.REPORT_EXPOSED_PII,
    Operation.REPORT_ACTIVE_DATA_FLOW,
    Operation.REPORT_RESTORE_RISK,
    Operation.DISABLE_DATA_FLOW,
    Operation.BLOCK_RESTORE,
    Operation.DELETE_RECORD,
    Operation.REDACT_RECORD,
    Operation.ANONYMIZE_RECORD,
    Operation.VERIFY_RECORD_ABSENT,
    Operation.VERIFY_RECORD_PRESENT,
    Operation.VERIFY_RECORD_REDACTED,
    Operation.VERIFY_RECORD_ANONYMIZED,
    Operation.VERIFY_DATA_FLOW_DISABLED,
    Operation.VERIFY_RESTORE_BLOCKED,
)


def step(
    operation: Operation,
    system_id: str = "account_service",
    resource_id: str = "account_1",
    evidence_id: str = "",
) -> Step:
    """Build a step, deriving phase and state from the operation."""
    return Step(
        phase=OPERATION_PHASE[operation],
        system_id=system_id,
        resource_id=resource_id,
        operation=operation,
        state=OPERATION_STATE[operation],
        evidence_id=evidence_id,
    )


def resource(resource_id: str, system_id: str) -> AttributedResource:
    """An attributed resource fixture."""
    return AttributedResource(
        system_id=system_id,
        resource_id=resource_id,
        how=LinkKind.EVIDENCE_LINK,
        confidence=1.0,
    )


# -- ordering -------------------------------------------------------------- #


def test_ordering_follows_the_problem_statement_table() -> None:
    """One step per operation, shuffled, must come back in table order."""
    shuffled = tuple(step(operation) for operation in reversed(TABLE_ORDER))
    ordered = order_steps(shuffled)
    assert [item.operation for item in ordered] == list(TABLE_ORDER)


def test_ordering_numbers_steps_consecutively_from_one() -> None:
    ordered = order_steps(tuple(step(operation) for operation in TABLE_ORDER))
    assert [item.step_no for item in ordered] == list(range(1, len(TABLE_ORDER) + 1))


def test_phases_group_before_operations() -> None:
    steps = (
        step(Operation.VERIFY_RECORD_ABSENT),
        step(Operation.DELETE_RECORD),
        step(Operation.REPORT_REMAINING_RECORD),
    )
    assert [item.phase for item in order_steps(steps)] == [
        Phase.FINDING,
        Phase.REMEDIATION,
        Phase.VERIFICATION,
    ]


def test_remediation_disables_flows_and_blocks_restores_first() -> None:
    """The table's own order already satisfies the safety requirement."""
    steps = (
        step(Operation.ANONYMIZE_RECORD, "analytics_platform", "analytics_1"),
        step(Operation.DELETE_RECORD, "account_service", "account_1"),
        step(Operation.BLOCK_RESTORE, "backup_service", "backup_1"),
        step(Operation.REDACT_RECORD, "support_platform", "support_1"),
        step(Operation.DISABLE_DATA_FLOW, "integration_service", "data_flow_1"),
    )
    assert [item.operation for item in order_steps(steps)] == [
        Operation.DISABLE_DATA_FLOW,
        Operation.BLOCK_RESTORE,
        Operation.DELETE_RECORD,
        Operation.REDACT_RECORD,
        Operation.ANONYMIZE_RECORD,
    ]


def test_ties_break_on_system_then_resource_as_strings() -> None:
    steps = (
        step(Operation.VERIFY_RECORD_ABSENT, "search_service", "search_9"),
        step(Operation.VERIFY_RECORD_ABSENT, "account_service", "account_9"),
        step(Operation.VERIFY_RECORD_ABSENT, "account_service", "account_10"),
    )
    assert [(item.system_id, item.resource_id) for item in order_steps(steps)] == [
        ("account_service", "account_10"),
        ("account_service", "account_9"),
        ("search_service", "search_9"),
    ]


def test_ordering_is_stable_and_idempotent() -> None:
    steps = tuple(step(operation) for operation in reversed(TABLE_ORDER))
    once = order_steps(steps)
    assert order_steps(once) == once


# -- needs_action ---------------------------------------------------------- #


def with_state(**kwargs: object) -> ResourceState:
    """A resource state carrying only the fields under test."""
    return ResourceState(resource_id="r_1", system_id="s", **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("treatment", "fields", "expected"),
    [
        (Treatment.DELETE, {"present": True}, True),
        (Treatment.DELETE, {"present": False}, False),
        (Treatment.DELETE, {"present": None}, False),
        (Treatment.DISABLE_FLOW, {"flow_enabled": True}, True),
        (Treatment.DISABLE_FLOW, {"flow_enabled": False}, False),
        (Treatment.DISABLE_FLOW, {"flow_enabled": None}, False),
        (Treatment.BLOCK_RESTORE, {"restorable": True}, True),
        (Treatment.BLOCK_RESTORE, {"restorable": False}, False),
        (Treatment.BLOCK_RESTORE, {"restorable": None}, False),
        (Treatment.REDACT, {"present": True, "identifiers_exposed": True}, True),
        (Treatment.REDACT, {"present": True, "identifiers_exposed": False}, False),
        (Treatment.REDACT, {"present": True, "identifiers_exposed": None}, False),
        (Treatment.ANONYMIZE, {"present": True, "identifiers_exposed": True}, True),
        (Treatment.ANONYMIZE, {"present": True, "identifiers_exposed": False}, False),
        (Treatment.RETAIN, {"present": True}, False),
        (Treatment.RETAIN, {"present": False}, False),
    ],
)
def test_needs_action(
    treatment: Treatment, fields: dict[str, object], expected: bool
) -> None:
    assert needs_action(treatment, with_state(**fields)) is expected


def test_an_unknown_state_never_produces_a_finding() -> None:
    """A finding requires positive evidence, so unknown means verify only."""
    for treatment in (
        Treatment.DELETE,
        Treatment.REDACT,
        Treatment.ANONYMIZE,
        Treatment.DISABLE_FLOW,
        Treatment.BLOCK_RESTORE,
    ):
        assert needs_action(treatment, with_state()) is False


def test_an_absent_record_needs_no_redaction() -> None:
    """You cannot expose identifiers in a record that is gone."""
    assert (
        needs_action(
            Treatment.REDACT, with_state(present=False, identifiers_exposed=True)
        )
        is False
    )


def test_a_retained_record_is_never_remediated() -> None:
    """A security audit log in retention must survive the deletion."""
    assert needs_action(Treatment.RETAIN, with_state(present=True)) is False
