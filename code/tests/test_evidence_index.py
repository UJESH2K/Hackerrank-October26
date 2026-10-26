"""Tests for the evidence index, in particular the admissibility rule."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from audit.evidence.index import build_index
from audit.loaders import Dataset
from audit.models import CoverageFact, EvidenceFile, Observation


def at(day: int, hour: int = 12) -> datetime:
    """A September 2026 timestamp, for readable fixtures."""
    return datetime(2026, 9, day, hour, tzinfo=UTC)


def dataset_with(*files: EvidenceFile) -> Dataset:
    """A dataset carrying only an evidence manifest."""
    return Dataset(
        dataset_dir=Path("dataset"),
        requests=(),
        identities=(),
        audit_log=(),
        activities=(),
        rules=(),
        evidence_files=files,
    )


EARLY = EvidenceFile(
    file_id="evidence_early",
    file_path="evidence/databases/early.sql",
    origin="capture",
    captured_at=at(2),
)
LATE = EvidenceFile(
    file_id="evidence_late",
    file_path="evidence/databases/late.sql",
    origin="capture",
    captured_at=at(20),
)


def observation(
    evidence_id: str,
    resource_id: str,
    observed_at: datetime,
    *,
    present: bool | None = True,
    copy_label: str = "primary",
    keys: frozenset[str] = frozenset(),
) -> Observation:
    """Build an observation for a fixture."""
    return Observation(
        evidence_id=evidence_id,
        observed_at=observed_at,
        system_id="account_service",
        resource_id=resource_id,
        copy_label=copy_label,
        present=present,
        link_keys=keys,
    )


def test_state_ignores_evidence_captured_after_the_cutoff() -> None:
    """Evidence from after ``audit_at`` must not change a case's answer."""
    early = observation("evidence_early", "account_5", at(2), present=True)
    late = observation("evidence_late", "account_5", at(20), present=False)
    index = build_index(dataset_with(EARLY, LATE), [early, late])

    assert index.state_for_resource("account_5", at(10)) == (early,)
    assert index.latest_state("account_5", at=at(10)) == early
    assert len(index.state_for_resource("account_5", at(25))) == 2
    assert index.latest_state("account_5", at=at(25)) == late


def test_a_record_dated_after_the_cutoff_is_excluded_too() -> None:
    """An admissible file can still contain a record from after the cutoff."""
    inside = observation("evidence_early", "account_5", at(1))
    outside = observation("evidence_early", "account_5", at(9))
    index = build_index(dataset_with(EARLY), [inside, outside])
    assert index.state_for_resource("account_5", at(5)) == (inside,)


def test_identity_linkage_uses_any_evidence_regardless_of_time() -> None:
    """Who a resource belongs to does not change, so a late capture still tells it."""
    late = observation(
        "evidence_late", "account_5", at(20), keys=frozenset({"a@b.com"})
    )
    index = build_index(dataset_with(EARLY, LATE), [late])

    assert index.for_link_key("a@b.com") == (late,)
    assert index.resources_for_link_keys(["A@B.com"]) == {"a@b.com": {"account_5"}}
    # With a cutoff the same lookup is restricted, for callers that need state.
    assert index.for_link_key("a@b.com", at(10)) == ()


def test_link_keys_are_matched_after_normalisation() -> None:
    record = observation(
        "evidence_early", "account_5", at(2), keys=frozenset({"+212648271593"})
    )
    index = build_index(dataset_with(EARLY), [record])
    assert index.for_link_key("+212 6 48 27 15 93") == (record,)


def test_copies_of_exposes_replication_drift() -> None:
    primary = observation(
        "evidence_early", "account_5", at(2), present=False, copy_label="primary"
    )
    replica = observation(
        "evidence_early",
        "account_5",
        at(2),
        present=True,
        copy_label="failover_replica",
    )
    index = build_index(dataset_with(EARLY), [primary, replica])
    copies = index.copies_of("account_5", at(5))
    assert copies["primary"].present is False
    assert copies["failover_replica"].present is True


def test_latest_state_can_be_restricted_to_one_copy() -> None:
    primary = observation("evidence_early", "account_5", at(2), copy_label="primary")
    replica = observation(
        "evidence_early", "account_5", at(3), copy_label="read_replica"
    )
    index = build_index(dataset_with(EARLY), [primary, replica])
    assert index.latest_state("account_5", "primary", at(5)) == primary
    assert index.latest_state("account_5", at=at(5)) == replica


def test_absence_is_provable_from_coverage() -> None:
    """A snapshot that enumerated the system without the resource proves absence."""
    coverage = CoverageFact(
        evidence_id="evidence_early",
        observed_at=at(2),
        system_id="account_service",
        copy_label="primary",
        collection="customer_profiles",
        record_count=3,
    )
    other = observation("evidence_early", "account_9", at(2))
    index = build_index(dataset_with(EARLY), [other], [coverage])

    assert index.was_enumerated("account_service", at(5))
    assert index.absent_by_coverage("account_5", "account_service", at(5)) == (coverage,)
    # account_9 was in that snapshot, so the snapshot does not prove it absent.
    assert index.absent_by_coverage("account_9", "account_service", at(5)) == ()


def test_coverage_after_the_cutoff_proves_nothing() -> None:
    coverage = CoverageFact(
        evidence_id="evidence_late",
        observed_at=at(20),
        system_id="account_service",
    )
    index = build_index(dataset_with(EARLY, LATE), [], [coverage])
    assert index.absent_by_coverage("account_5", "account_service", at(10)) == ()
    assert not index.was_enumerated("account_service", at(10))


def test_an_unknown_evidence_id_is_inadmissible_for_state() -> None:
    """Without a manifest capture time there is nothing to compare to the cutoff."""
    stray = observation("evidence_missing", "account_5", at(2))
    index = build_index(dataset_with(EARLY), [stray])
    assert index.state_for_resource("account_5", at(10)) == ()
    assert index.state_for_resource("account_5") == (stray,)
