"""Tests for state reconstruction at ``audit_at``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from audit.evidence.index import build_index
from audit.loaders import Dataset
from audit.models import Activity, CoverageFact, EvidenceFile, Observation
from audit.state import StateResolver


def at(day: int, hour: int = 12) -> datetime:
    """A September 2026 timestamp."""
    return datetime(2026, 9, day, hour, tzinfo=UTC)


MANIFEST = (
    EvidenceFile("evidence_early", "evidence/a.sql", "capture", at(2)),
    EvidenceFile("evidence_late", "evidence/b.sql", "capture", at(20)),
)


def observation(
    resource_id: str,
    observed_at: datetime,
    *,
    evidence_id: str = "evidence_early",
    present: bool | None = True,
    copy_label: str = "primary",
    system_id: str = "account_service",
    attributes: dict[str, str] | None = None,
) -> Observation:
    """Build an observation fixture."""
    return Observation(
        evidence_id=evidence_id,
        observed_at=observed_at,
        system_id=system_id,
        resource_id=resource_id,
        copy_label=copy_label,
        present=present,
        attributes=attributes or {},
    )


def resolver(
    observations: tuple[Observation, ...] = (),
    coverage: tuple[CoverageFact, ...] = (),
    activities: tuple[Activity, ...] = (),
) -> StateResolver:
    """Build a state resolver over fixture data."""
    dataset = Dataset(
        dataset_dir=Path("dataset"),
        requests=(),
        identities=(),
        audit_log=(),
        activities=activities,
        rules=(),
        evidence_files=MANIFEST,
    )
    return StateResolver(dataset, build_index(dataset, list(observations), list(coverage)))


# -- admissibility --------------------------------------------------------- #


def test_evidence_after_the_cutoff_cannot_establish_state() -> None:
    """A deletion that happened after audit_at must not make the case look clean."""
    before = observation("account_1", at(1), present=True)
    after = observation("account_1", at(21), evidence_id="evidence_late", present=False)
    state = resolver((before, after)).resolve("account_1", "account_service", at(10))
    assert state.present is True
    assert state.evidence_id == "evidence_early"


def test_the_latest_admissible_observation_wins() -> None:
    old = observation("account_1", at(1), present=True)
    new = observation("account_1", at(5), present=False)
    state = resolver((old, new)).resolve("account_1", "account_service", at(10))
    assert state.present is False
    assert state.observed_at == at(5)


# -- presence and absence -------------------------------------------------- #


def test_no_observation_and_no_coverage_means_unknown_not_absent() -> None:
    """Unknown must not be reported as a remaining record, nor as proven gone."""
    state = resolver().resolve("account_1", "account_service", at(10))
    assert state.present is None
    assert state.evidence_id == ""


def test_coverage_proves_absence_when_nothing_was_observed() -> None:
    coverage = CoverageFact(
        evidence_id="evidence_early",
        observed_at=at(2),
        system_id="account_service",
        copy_label="primary",
        collection="customer_profiles",
        record_count=3,
    )
    state = resolver((), (coverage,)).resolve("account_1", "account_service", at(10))
    assert state.present is False
    assert state.coverage is coverage
    assert state.evidence_id == "evidence_early"


def test_replica_drift_keeps_the_record_present() -> None:
    """Deleted from the primary but still served by a replica is still present."""
    primary = CoverageFact(
        evidence_id="evidence_early",
        observed_at=at(2),
        system_id="account_service",
        copy_label="primary",
        collection="customer_profiles_primary",
        record_count=3,
    )
    replica = observation(
        "account_005", at(2), present=True, copy_label="failover_replica"
    )
    state = resolver((replica,), (primary,)).resolve(
        "account_005", "account_service", at(10)
    )
    assert state.present is True
    assert state.copies[0].copy_label == "failover_replica"


def test_disagreeing_copies_at_the_same_time_resolve_to_present() -> None:
    gone = observation("account_1", at(2), present=False, copy_label="primary")
    kept = observation("account_1", at(2), present=True, copy_label="read_replica")
    state = resolver((gone, kept)).resolve("account_1", "account_service", at(10))
    assert state.present is True
    assert state.drifted


# -- the case evidence file as source of truth ----------------------------- #


def test_the_case_evidence_file_is_preferred_when_it_holds_the_resource() -> None:
    case_file = observation(
        "account_1", at(2), evidence_id="evidence_early", present=False
    )
    other = observation("account_1", at(3), evidence_id="evidence_late", present=True)
    state = resolver((case_file, other)).resolve(
        "account_1", "account_service", at(25), case_evidence_id="evidence_early"
    )
    assert state.present is False
    assert state.evidence_id == "evidence_early"


def test_it_falls_back_to_any_file_when_the_case_file_lacks_the_resource() -> None:
    other = observation("account_1", at(3), evidence_id="evidence_late", present=True)
    state = resolver((other,)).resolve(
        "account_1", "account_service", at(25), case_evidence_id="evidence_early"
    )
    assert state.present is True
    assert state.evidence_id == "evidence_late"


# -- exposure signals ------------------------------------------------------ #


@pytest.mark.parametrize(
    ("attributes", "expected"),
    [
        ({"email": "a@b.com"}, True),
        ({"email": "null", "phone": "null", "name": "null"}, False),
        ({"email": "[REDACTED]"}, False),
        ({"result": "IDENTIFIABLE"}, True),
        ({"result": "ANONYMIZED"}, False),
        ({"result": "REDACTED"}, False),
        ({"direct_identifiers": "1"}, True),
        ({"direct_identifiers": "0"}, False),
        ({"matched_value": "a@b.com", "detector": "direct_identifier"}, True),
        ({"index_name": "customer_directory_v3"}, None),
    ],
)
def test_exposure_signals(attributes: dict[str, str], expected: bool | None) -> None:
    state = resolver(
        (observation("analytics_1", at(2), attributes=attributes),)
    ).resolve("analytics_1", "analytics_platform", at(10))
    assert state.identifiers_exposed is expected


def test_a_tombstone_keeping_only_its_reference_is_not_exposed() -> None:
    attributes = {
        "subject_reference": "user_7",
        "email": "null",
        "phone": "null",
        "name": "null",
        "email_hash": "null",
        "account_status": "DELETED",
    }
    state = resolver(
        (observation("tombstone_1", at(2), attributes=attributes),)
    ).resolve("tombstone_1", "identity_service", at(10))
    assert state.identifiers_exposed is False
    assert state.subject_reference_only


def test_a_later_redaction_overrides_an_earlier_exposure() -> None:
    exposed = observation("support_1", at(2), attributes={"requester_email": "a@b.com"})
    redacted = observation("support_1", at(3), attributes={"result": "REDACTED"})
    state = resolver((exposed, redacted)).resolve("support_1", "support_platform", at(10))
    assert state.identifiers_exposed is False


# -- data flow signals ----------------------------------------------------- #


@pytest.mark.parametrize(
    ("attributes", "expected"),
    [
        ({"enabled": "true"}, True),
        ({"enabled": "false"}, False),
        ({"enabled": "1"}, True),
        ({"enabled": "0"}, False),
        ({"scheduler_registered": "true"}, True),
        ({"result": "ACTIVE"}, True),
        ({"result": "DISABLED"}, False),
        ({"connector_name": "customer_events_connector"}, None),
    ],
)
def test_flow_signals(attributes: dict[str, str], expected: bool | None) -> None:
    state = resolver(
        (observation("data_flow_1", at(2), attributes=attributes),)
    ).resolve("data_flow_1", "integration_service", at(10))
    assert state.flow_enabled is expected


# -- backup signals -------------------------------------------------------- #


@pytest.mark.parametrize(
    ("attributes", "expected"),
    [
        ({"restore_allowed": "true"}, True),
        ({"restore_allowed": "false"}, False),
        ({"result": "ACTIVE"}, True),
        ({"result": "BLOCKED"}, False),
        ({"decision": "deny"}, False),
        ({"decision": "allow"}, True),
        ({"eligible": "true", "deletion_block": "false"}, True),
        ({"eligible": "true", "deletion_block": "true"}, False),
        ({"eligible": "false", "deletion_block": "false"}, False),
        ({"archive_key": "archive/x.tar.zst"}, None),
    ],
)
def test_backup_signals(attributes: dict[str, str], expected: bool | None) -> None:
    """The eligible/deletion_block pair was worth one sample case on its own."""
    state = resolver(
        (observation("backup_1", at(2), attributes=attributes),)
    ).resolve("backup_1", "backup_service", at(10))
    assert state.restorable is expected


# -- recreation ------------------------------------------------------------ #


def recreation_activity(resource_id: str, detail: str, when: datetime) -> Activity:
    """An activity row stating data was put back."""
    return Activity(
        activity_id="activity_9",
        event_time=when,
        event_type="SYNC_RECORD",
        source_system="integration_service",
        target_system="search_service",
        resource_id=resource_id,
        reported_result="SUCCESS",
        details=detail,
    )


def test_a_repopulation_event_shows_data_can_come_back() -> None:
    activity = recreation_activity(
        "search_1", "Active connector repopulated downstream resource search_1.", at(5)
    )
    state = resolver(activities=(activity,)).resolve("search_1", "search_service", at(10))
    assert len(state.recreation) == 1
    assert state.recreation[0].activity_id == "activity_9"


def test_a_repopulation_after_the_cutoff_is_ignored() -> None:
    activity = recreation_activity(
        "search_1", "Active connector repopulated downstream resource search_1.", at(25)
    )
    state = resolver(activities=(activity,)).resolve("search_1", "search_service", at(10))
    assert state.recreation == ()


def test_evidence_beats_a_recreation_event_for_flow_state() -> None:
    """Activity rows are claims; evidence saying the flow is off wins."""
    activity = recreation_activity(
        "data_flow_1", "Active connector repopulated downstream resource search_1.", at(5)
    )
    off = observation(
        "data_flow_1", at(6), attributes={"enabled": "false"}, system_id="integration_service"
    )
    state = resolver((off,), activities=(activity,)).resolve(
        "data_flow_1", "integration_service", at(10)
    )
    assert state.flow_enabled is False


def test_a_success_claim_never_proves_absence() -> None:
    """A deletion the worker called SUCCESS does not make the record gone."""
    claim = Activity(
        activity_id="activity_1",
        event_time=at(3),
        event_type="DELETE_RECORD",
        source_system="deletion_worker",
        target_system="account_service",
        resource_id="account_1",
        reported_result="SUCCESS",
        details="Deletion worker submitted primary profile account_1 to account_service.",
    )
    still_there = observation("account_1", at(4), present=True)
    state = resolver((still_there,), activities=(claim,)).resolve(
        "account_1", "account_service", at(10)
    )
    assert state.present is True
