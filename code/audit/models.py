"""Frozen domain models and the closed vocabularies of the output contract.

Every dataclass here mirrors one participant-facing dataset file or one piece of
the required ``steps_json`` output. They are frozen because the pipeline treats
loaded data as immutable facts: anything derived is a new object, never a
mutation of the input.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

# --------------------------------------------------------------------------- #
# Closed vocabularies
# --------------------------------------------------------------------------- #


class DeletionStatus(StrEnum):
    """Permitted values of the ``deletion_status`` output column."""

    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReportedStatus(StrEnum):
    """The deletion workflow's claim. A claim, never proof of the real state."""

    COMPLETE = "COMPLETE"
    PARTIALLY_COMPLETE = "PARTIALLY_COMPLETE"
    NOT_STARTED = "NOT_STARTED"


class Phase(StrEnum):
    """Step phases, in their required output order."""

    FINDING = "FINDING"
    REMEDIATION = "REMEDIATION"
    VERIFICATION = "VERIFICATION"


class Operation(StrEnum):
    """The fifteen permitted step operations, in problem-statement table order."""

    REPORT_REMAINING_RECORD = "REPORT_REMAINING_RECORD"
    REPORT_EXPOSED_PII = "REPORT_EXPOSED_PII"
    REPORT_ACTIVE_DATA_FLOW = "REPORT_ACTIVE_DATA_FLOW"
    REPORT_RESTORE_RISK = "REPORT_RESTORE_RISK"

    DISABLE_DATA_FLOW = "DISABLE_DATA_FLOW"
    BLOCK_RESTORE = "BLOCK_RESTORE"
    DELETE_RECORD = "DELETE_RECORD"
    REDACT_RECORD = "REDACT_RECORD"
    ANONYMIZE_RECORD = "ANONYMIZE_RECORD"

    VERIFY_RECORD_ABSENT = "VERIFY_RECORD_ABSENT"
    VERIFY_RECORD_PRESENT = "VERIFY_RECORD_PRESENT"
    VERIFY_RECORD_REDACTED = "VERIFY_RECORD_REDACTED"
    VERIFY_RECORD_ANONYMIZED = "VERIFY_RECORD_ANONYMIZED"
    VERIFY_DATA_FLOW_DISABLED = "VERIFY_DATA_FLOW_DISABLED"
    VERIFY_RESTORE_BLOCKED = "VERIFY_RESTORE_BLOCKED"


class State(StrEnum):
    """The resource/process states a step may assert."""

    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    BLOCKED = "BLOCKED"
    REDACTED = "REDACTED"
    ANONYMIZED = "ANONYMIZED"


class RequiredAction(StrEnum):
    """The treatments ``deletion_rules.csv`` can require."""

    DELETE = "DELETE"
    REDACT = "REDACT"
    ANONYMIZE = "ANONYMIZE"
    RETAIN = "RETAIN"


#: ``state`` is a pure function of ``operation``; it is never a free choice.
OPERATION_STATE: Mapping[Operation, State] = {
    Operation.REPORT_REMAINING_RECORD: State.PRESENT,
    Operation.REPORT_EXPOSED_PII: State.PRESENT,
    Operation.REPORT_ACTIVE_DATA_FLOW: State.ACTIVE,
    Operation.REPORT_RESTORE_RISK: State.ACTIVE,
    Operation.DISABLE_DATA_FLOW: State.DISABLED,
    Operation.BLOCK_RESTORE: State.BLOCKED,
    Operation.DELETE_RECORD: State.ABSENT,
    Operation.REDACT_RECORD: State.REDACTED,
    Operation.ANONYMIZE_RECORD: State.ANONYMIZED,
    Operation.VERIFY_RECORD_ABSENT: State.ABSENT,
    Operation.VERIFY_RECORD_PRESENT: State.PRESENT,
    Operation.VERIFY_RECORD_REDACTED: State.REDACTED,
    Operation.VERIFY_RECORD_ANONYMIZED: State.ANONYMIZED,
    Operation.VERIFY_DATA_FLOW_DISABLED: State.DISABLED,
    Operation.VERIFY_RESTORE_BLOCKED: State.BLOCKED,
}

#: Each operation belongs to exactly one phase.
OPERATION_PHASE: Mapping[Operation, Phase] = {
    Operation.REPORT_REMAINING_RECORD: Phase.FINDING,
    Operation.REPORT_EXPOSED_PII: Phase.FINDING,
    Operation.REPORT_ACTIVE_DATA_FLOW: Phase.FINDING,
    Operation.REPORT_RESTORE_RISK: Phase.FINDING,
    Operation.DISABLE_DATA_FLOW: Phase.REMEDIATION,
    Operation.BLOCK_RESTORE: Phase.REMEDIATION,
    Operation.DELETE_RECORD: Phase.REMEDIATION,
    Operation.REDACT_RECORD: Phase.REMEDIATION,
    Operation.ANONYMIZE_RECORD: Phase.REMEDIATION,
    Operation.VERIFY_RECORD_ABSENT: Phase.VERIFICATION,
    Operation.VERIFY_RECORD_PRESENT: Phase.VERIFICATION,
    Operation.VERIFY_RECORD_REDACTED: Phase.VERIFICATION,
    Operation.VERIFY_RECORD_ANONYMIZED: Phase.VERIFICATION,
    Operation.VERIFY_DATA_FLOW_DISABLED: Phase.VERIFICATION,
    Operation.VERIFY_RESTORE_BLOCKED: Phase.VERIFICATION,
}

#: Sort rank of each phase in the output array.
PHASE_RANK: Mapping[Phase, int] = {
    Phase.FINDING: 0,
    Phase.REMEDIATION: 1,
    Phase.VERIFICATION: 2,
}

#: Sort rank of each operation, following the problem statement's table order.
#: This is the only ordering rule within a phase; there is no separate dependency
#: reordering, because the table already places DISABLE_DATA_FLOW and
#: BLOCK_RESTORE ahead of the record-level remediations.
OPERATION_RANK: Mapping[Operation, int] = {
    operation: rank for rank, operation in enumerate(Operation)
}

#: Canonical key order for a serialised step. The grader ignores key order; we fix
#: it anyway so runs are byte-for-byte reproducible.
STEP_FIELD_ORDER: tuple[str, ...] = (
    "step_no",
    "phase",
    "system_id",
    "resource_id",
    "operation",
    "state",
    "evidence_id",
)


# --------------------------------------------------------------------------- #
# Dataset records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Request:
    """One row of ``deletion_requests.csv`` - a case to audit."""

    case_id: str
    email: str
    requested_at: datetime
    reported_status: ReportedStatus
    reported_at: datetime
    audit_at: datetime
    request_text: str


@dataclass(frozen=True, slots=True)
class Identity:
    """One row of ``user_identities.csv`` - a user's current identity snapshot."""

    user_id: str
    name: str
    email: str
    phone: str
    email_hash: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One row of ``user_audit_log.csv`` - a historical identity field change."""

    audit_id: str
    user_id: str
    event_time: datetime
    field_name: str
    old_value: str
    new_value: str


@dataclass(frozen=True, slots=True)
class Activity:
    """One row of ``system_activity.csv``.

    The stream is global: a row may concern an unrelated person. ``reported_result``
    is a claim about an operation, not proof of the resulting resource state.
    """

    activity_id: str
    event_time: datetime
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


@dataclass(frozen=True, slots=True)
class Rule:
    """One row of ``deletion_rules.csv`` - the treatment a data category requires."""

    rule_id: str
    data_category: str
    condition: str
    required_action: RequiredAction

    @property
    def is_unconditional(self) -> bool:
        """True when the rule applies regardless of record-specific context."""
        return self.condition.strip().casefold() == "always"


@dataclass(frozen=True, slots=True)
class EvidenceFile:
    """One row of ``evidence_files.csv`` - where an evidence artefact lives.

    The manifest never says which case or user a file concerns; that link must be
    established from the file's contents.
    """

    file_id: str
    file_path: str
    origin: str
    captured_at: datetime

    @property
    def suffix(self) -> str:
        """Lower-case file extension, e.g. ``.sql``, used to pick a parser."""
        head, sep, tail = self.file_path.rpartition(".")
        return f".{tail.lower()}" if sep else ""


# --------------------------------------------------------------------------- #
# Derived records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Observation:
    """An evidence-grounded fact about one copy of one resource at one time.

    Parsers produce observations; the state reconstructor folds them into a
    per-resource state at ``audit_at``. ``evidence_id`` is the participant-visible
    identifier that proves the fact: a ``file_id``, ``activity_id`` or ``audit_id``.

    Attributes:
        evidence_id: the ``file_id`` from ``evidence_files.csv``; this is what a
            step cites.
        observed_at: the time the observation describes. The most specific time
            available: the record's own time, else the snapshot or scope time,
            else the file's ``captured_at``.
        system_id: the canonical system that owns the resource, derived from the
            ``resource_id`` prefix.
        resource_id: the resource observed.
        copy_label: where this copy lives, for example ``primary``,
            ``read_replica``, ``replica_eu_west``, ``failover_replica``,
            ``export`` or ``trace``. Replication drift is a disagreement between
            two copy labels of the same resource.
        present: whether the record exists in that copy. ``None`` when the
            evidence describes the record without establishing existence.
        attributes: everything else the record carried, flattened to strings.
        link_keys: lower-cased identifier values that can tie the record to a
            person: emails, phones, email hashes and user references, including
            the lookup key of a query that returned the record.
    """

    evidence_id: str
    observed_at: datetime
    system_id: str
    resource_id: str
    copy_label: str = "primary"
    present: bool | None = None
    attributes: Mapping[str, str] = field(default_factory=dict)
    link_keys: frozenset[str] = frozenset()

    @property
    def identity_key(self) -> tuple[str, str, str, str, datetime]:
        """Key identifying the same fact, used to merge duplicate parses.

        A nested record can be reached by more than one path through a file - a
        search hit document also appears under its query - and both paths
        describe one fact.
        """
        return (
            self.evidence_id,
            self.system_id,
            self.resource_id,
            self.copy_label,
            self.observed_at,
        )


@dataclass(frozen=True, slots=True)
class CoverageFact:
    """A statement that one snapshot fully covered one system copy at one time.

    Coverage is what makes absence provable: if ``account_service`` was
    snapshotted at ``T`` and ``account_005`` is not among the observations from
    that snapshot, then ``account_005`` was absent at ``T``. Without the coverage
    fact, a missing resource id is merely unobserved.
    """

    evidence_id: str
    observed_at: datetime
    system_id: str
    copy_label: str = "primary"
    collection: str = ""
    record_count: int = 0


# --------------------------------------------------------------------------- #
# Output contract
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Step:
    """One element of a case's ``steps_json`` array.

    ``step_no`` is assigned by the planner once the array order is final, so it
    defaults to ``0`` while a step is still being built.
    """

    phase: Phase
    system_id: str
    resource_id: str
    operation: Operation
    state: State
    evidence_id: str = ""
    step_no: int = 0

    def renumbered(self, step_no: int) -> Step:
        """Return a copy carrying ``step_no``."""
        return Step(
            phase=self.phase,
            system_id=self.system_id,
            resource_id=self.resource_id,
            operation=self.operation,
            state=self.state,
            evidence_id=self.evidence_id,
            step_no=step_no,
        )

    @property
    def sort_key(self) -> tuple[int, int, str, str]:
        """Phase, then the operation table order, then system_id, then resource_id."""
        return (
            PHASE_RANK[self.phase],
            OPERATION_RANK[self.operation],
            self.system_id,
            self.resource_id,
        )

    def to_dict(self) -> dict[str, object]:
        """Serialise with the canonical key order of ``STEP_FIELD_ORDER``."""
        values: dict[str, object] = {
            "step_no": self.step_no,
            "phase": str(self.phase),
            "system_id": self.system_id,
            "resource_id": self.resource_id,
            "operation": str(self.operation),
            "state": str(self.state),
            "evidence_id": self.evidence_id,
        }
        return {key: values[key] for key in STEP_FIELD_ORDER}


@dataclass(frozen=True, slots=True)
class CaseResult:
    """The audit verdict for one case: the row written to ``output.csv``."""

    case_id: str
    deletion_status: DeletionStatus
    steps: tuple[Step, ...] = ()

    def steps_json(self) -> str:
        """The ``steps_json`` column value: a compact, deterministic JSON array."""
        return json.dumps(
            [step.to_dict() for step in self.steps],
            separators=(",", ":"),
            ensure_ascii=False,
        )
