"""Tests for the input layer: row counts, timestamp handling and case ordering."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from audit.loaders import (
    SAMPLE_REQUESTS_FILE,
    Dataset,
    case_sort_key,
    load_dataset,
    load_requests,
    parse_timestamp,
)
from audit.models import ReportedStatus, RequiredAction

EXPECTED_REQUESTS = 518
EXPECTED_SAMPLE_REQUESTS = 32
EXPECTED_RULES = 15
EXPECTED_EVIDENCE_FILES = 94


def test_request_count(dataset: Dataset) -> None:
    assert len(dataset.requests) == EXPECTED_REQUESTS


def test_sample_request_count(dataset_dir: Path) -> None:
    samples = load_requests(dataset_dir / SAMPLE_REQUESTS_FILE)
    assert len(samples) == EXPECTED_SAMPLE_REQUESTS


def test_rule_count(dataset: Dataset) -> None:
    assert len(dataset.rules) == EXPECTED_RULES


def test_evidence_file_count(dataset: Dataset) -> None:
    assert len(dataset.evidence_files) == EXPECTED_EVIDENCE_FILES


def test_identity_and_activity_counts(dataset: Dataset) -> None:
    assert len(dataset.identities) == 850
    assert len(dataset.audit_log) == 306
    assert len(dataset.activities) == 4011


def test_case_ids_are_unique(dataset: Dataset) -> None:
    case_ids = [request.case_id for request in dataset.requests]
    assert len(set(case_ids)) == len(case_ids)


def test_requests_are_in_natural_numeric_order(dataset: Dataset) -> None:
    numbers = [int(request.case_id.rpartition("_")[2]) for request in dataset.requests]
    assert numbers == sorted(numbers)


def test_case_sort_key_is_numeric_not_lexicographic() -> None:
    """``case_99`` must come before ``case_100``; plain string sorting fails this."""
    unsorted = ["case_100", "case_9", "case_99", "case_10"]
    assert sorted(unsorted, key=case_sort_key) == [
        "case_9",
        "case_10",
        "case_99",
        "case_100",
    ]
    assert sorted(unsorted) != sorted(unsorted, key=case_sort_key)


def test_case_sort_key_handles_missing_suffix() -> None:
    """Ids without a numeric suffix still sort deterministically, after the rest."""
    assert sorted(["case_x", "case_2"], key=case_sort_key) == ["case_2", "case_x"]


def test_timestamps_are_aware_utc(dataset: Dataset) -> None:
    request = dataset.requests[0]
    assert request.audit_at.tzinfo is not None
    assert request.audit_at.utcoffset() == datetime.now(UTC).utcoffset()


def test_parse_timestamp_accepts_zulu_suffix() -> None:
    assert parse_timestamp("2026-07-16T08:00:00Z") == datetime(
        2026, 7, 16, 8, 0, tzinfo=UTC
    )


def test_parse_timestamp_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty timestamp"):
        parse_timestamp("   ")


def test_reported_status_values_are_in_the_closed_vocabulary(dataset: Dataset) -> None:
    assert {request.reported_status for request in dataset.requests} <= set(ReportedStatus)


def test_required_actions_are_in_the_closed_vocabulary(dataset: Dataset) -> None:
    assert {rule.required_action for rule in dataset.rules} <= set(RequiredAction)


def test_every_evidence_file_exists_on_disk(dataset: Dataset) -> None:
    missing = [
        evidence.file_id
        for evidence in dataset.evidence_files
        if not dataset.evidence_path(evidence).is_file()
    ]
    assert missing == []


def test_select_filters_and_keeps_dataset_order(dataset: Dataset) -> None:
    wanted = [dataset.requests[5].case_id, dataset.requests[1].case_id]
    selected = dataset.select(wanted)
    assert [request.case_id for request in selected] == [
        dataset.requests[1].case_id,
        dataset.requests[5].case_id,
    ]


def test_loading_the_sample_file_keeps_the_other_tables(dataset_dir: Path) -> None:
    sample_dataset = load_dataset(dataset_dir, dataset_dir / SAMPLE_REQUESTS_FILE)
    assert len(sample_dataset.requests) == EXPECTED_SAMPLE_REQUESTS
    assert len(sample_dataset.rules) == EXPECTED_RULES
