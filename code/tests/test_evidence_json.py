"""Tests for the JSON evidence parser, against synthetic fixtures only."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from audit.evidence.context import ParseContext
from audit.evidence.json_parser import merge_duplicates, parse_json
from audit.models import Observation

CAPTURED_AT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def write(tmp_path: Path, payload: object, name: str = "evidence.json") -> ParseContext:
    """Write a fixture and return the context that parses it."""
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return ParseContext(path=path, evidence_id="evidence_test", captured_at=CAPTURED_AT)


def by_resource(observations: list[Observation]) -> dict[str, Observation]:
    """Index observations by resource id, asserting uniqueness."""
    merged = merge_duplicates(observations)
    index = {observation.resource_id: observation for observation in merged}
    assert len(index) == len(merged)
    return index


def test_account_lookup_404_proves_absence(tmp_path: Path) -> None:
    """``"profile": null`` with a 404 is how a deleted account reads."""
    context = write(
        tmp_path,
        {
            "exported_at": "2026-03-01T12:00:00Z",
            "service_exports": {
                "account_service": {
                    "profile_lookups": [
                        {
                            "observed_at": "2026-03-01T09:00:00Z",
                            "profile": None,
                            "resource_id": "account_470",
                            "status_code": 404,
                        }
                    ]
                }
            },
        },
    )
    found = by_resource(parse_json(context).observations)
    observation = found["account_470"]
    assert observation.present is False
    assert observation.system_id == "account_service"
    assert observation.observed_at == datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    assert observation.evidence_id == "evidence_test"


def test_account_lookup_200_proves_presence_and_carries_identifiers(tmp_path: Path) -> None:
    context = write(
        tmp_path,
        {
            "service_exports": {
                "account_service": {
                    "profile_lookups": [
                        {
                            "observed_at": "2026-03-01T09:30:00Z",
                            "profile": {
                                "email": "A.Rao@Example.com",
                                "phone": "+91 91726 13808",
                                "full_name": "Aarav Rao",
                                "customer_reference": None,
                            },
                            "resource_id": "account_2424",
                            "status_code": 200,
                        }
                    ]
                }
            }
        },
    )
    observation = by_resource(parse_json(context).observations)["account_2424"]
    assert observation.present is True
    assert observation.attributes["email"] == "A.Rao@Example.com"
    assert observation.link_keys == {"a.rao@example.com", "+919172613808"}


def test_null_identifiers_yield_no_link_keys(tmp_path: Path) -> None:
    context = write(
        tmp_path,
        {
            "captured_at": "2026-03-01T12:00:00Z",
            "data_store": "analytics_platform",
            "collection": "customer_analytics_reports",
            "records": [
                {
                    "resource_id": "analytics_901",
                    "user_id": None,
                    "email": None,
                    "phone": None,
                    "email_hash": None,
                }
            ],
        },
    )
    observation = by_resource(parse_json(context).observations)["analytics_901"]
    assert observation.link_keys == frozenset()
    assert observation.present is True


def test_search_hit_merges_with_its_query(tmp_path: Path) -> None:
    """The query holds the identity link; the hit document holds the fields."""
    context = write(
        tmp_path,
        {
            "service_exports": {
                "search_service": {
                    "index_queries": [
                        {
                            "observed_at": "2026-03-01T10:00:00Z",
                            "query": "noah@example.com",
                            "resource_id": "search_1993",
                            "hits": {
                                "total": 1,
                                "documents": [
                                    {
                                        "resource_id": "search_1993",
                                        "email": "noah@example.com",
                                        "display_name": None,
                                    }
                                ],
                            },
                        }
                    ]
                }
            }
        },
    )
    found = by_resource(parse_json(context).observations)
    assert set(found) == {"search_1993"}
    observation = found["search_1993"]
    assert observation.present is True
    assert observation.link_keys == {"noah@example.com"}


def test_empty_search_hits_prove_the_document_is_gone(tmp_path: Path) -> None:
    context = write(
        tmp_path,
        {
            "service_exports": {
                "search_service": {
                    "index_queries": [
                        {
                            "observed_at": "2026-03-01T10:00:00Z",
                            "query": "gone@example.com",
                            "resource_id": "search_77",
                            "hits": {"total": 0, "documents": []},
                        }
                    ]
                }
            }
        },
    )
    observation = by_resource(parse_json(context).observations)["search_77"]
    assert observation.present is False


def test_empty_records_list_becomes_a_coverage_fact(tmp_path: Path) -> None:
    """No resource id appears, so absence is recorded as coverage."""
    context = write(
        tmp_path,
        {
            "exported_at": "2026-03-01T15:00:00Z",
            "snapshots": [
                {
                    "captured_at": "2026-02-28T12:00:00Z",
                    "data_store": "account_service",
                    "collection": "customer_profiles",
                    "records": [],
                }
            ],
        },
    )
    result = parse_json(context)
    assert result.observations == []
    assert len(result.coverage) == 1
    fact = result.coverage[0]
    assert fact.system_id == "account_service"
    assert fact.record_count == 0
    assert fact.observed_at == datetime(2026, 2, 28, 12, 0, tzinfo=UTC)


def test_snapshot_records_inherit_the_snapshot_time(tmp_path: Path) -> None:
    context = write(
        tmp_path,
        {
            "exported_at": "2026-03-01T15:00:00Z",
            "snapshots": [
                {
                    "captured_at": "2026-02-28T12:00:00Z",
                    "data_store": "support_platform",
                    "collection": "support_conversations",
                    "records": [{"resource_id": "support_12", "requester_email": "a@b.com"}],
                }
            ],
        },
    )
    observation = by_resource(parse_json(context).observations)["support_12"]
    assert observation.observed_at == datetime(2026, 2, 28, 12, 0, tzinfo=UTC)
    assert observation.attributes["collection"] == "support_conversations"


def test_record_history_keeps_every_timed_observation(tmp_path: Path) -> None:
    """``operational_record_history`` holds several readings of one resource."""
    context = write(
        tmp_path,
        {
            "snapshots": [
                {
                    "captured_at": "2026-03-01T12:00:00Z",
                    "data_store": "privacy_treatment_service",
                    "collection": "operational_record_history",
                    "records": [
                        {
                            "recorded_at": "2026-02-10T09:00:00Z",
                            "system": "account_service",
                            "resource_id": "account_90",
                            "record_type": "USER_PROFILE",
                            "result": "FOUND",
                            "lookup_value": "a@b.com",
                        },
                        {
                            "recorded_at": "2026-02-20T09:00:00Z",
                            "system": "account_service",
                            "resource_id": "account_90",
                            "record_type": "USER_PROFILE",
                            "result": "NOT_FOUND",
                            "lookup_value": "a@b.com",
                        },
                    ],
                }
            ]
        },
    )
    observations = merge_duplicates(parse_json(context).observations)
    readings = sorted(
        (item for item in observations if item.resource_id == "account_90"),
        key=lambda item: item.observed_at,
    )
    assert [item.present for item in readings] == [True, False]
    assert [item.observed_at.day for item in readings] == [10, 20]


def test_export_collection_is_labelled_as_an_export_copy(tmp_path: Path) -> None:
    context = write(
        tmp_path,
        {
            "captured_at": "2026-03-01T12:00:00Z",
            "source": "user_export_store",
            "collection": "customer_export_jobs",
            "records": [
                {
                    "resource_id": "export_001",
                    "owner_email": "g.costa@example.com",
                    "download_available": True,
                }
            ],
        },
    )
    observation = by_resource(parse_json(context).observations)["export_001"]
    assert observation.copy_label == "export"
    assert observation.system_id == "export_service"


def test_unreadable_json_is_reported_not_raised(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    context = ParseContext(path=path, evidence_id="evidence_x", captured_at=CAPTURED_AT)
    result = parse_json(context)
    assert result.observations == []
    assert len(result.warnings) == 1
    assert "unreadable JSON" in result.warnings[0]


def test_merge_duplicates_unions_attributes_and_keys() -> None:
    base = Observation(
        evidence_id="e1",
        observed_at=CAPTURED_AT,
        system_id="account_service",
        resource_id="account_1",
        copy_label="primary",
        present=None,
        attributes={"email": "", "a": "1"},
        link_keys=frozenset({"x@y.com"}),
    )
    other = Observation(
        evidence_id="e1",
        observed_at=CAPTURED_AT,
        system_id="account_service",
        resource_id="account_1",
        copy_label="primary",
        present=True,
        attributes={"email": "x@y.com", "b": "2"},
        link_keys=frozenset({"user_1"}),
    )
    merged = merge_duplicates([base, other])
    assert len(merged) == 1
    assert merged[0].present is True
    assert merged[0].attributes == {"a": "1", "b": "2", "email": "x@y.com"}
    assert merged[0].link_keys == {"x@y.com", "user_1"}


def test_merge_duplicates_keeps_different_times_apart() -> None:
    first = Observation(
        evidence_id="e1",
        observed_at=CAPTURED_AT,
        system_id="account_service",
        resource_id="account_1",
    )
    second = Observation(
        evidence_id="e1",
        observed_at=datetime(2026, 4, 1, tzinfo=UTC),
        system_id="account_service",
        resource_id="account_1",
    )
    assert len(merge_duplicates([first, second])) == 2


@pytest.mark.parametrize("record_count", [0, 3])
def test_coverage_requires_a_declared_store_and_time(
    tmp_path: Path, record_count: int
) -> None:
    """A bare list of records with no store or time proves no coverage."""
    context = write(
        tmp_path,
        {
            "records": [
                {"resource_id": f"account_{index}"} for index in range(record_count)
            ]
        },
    )
    assert parse_json(context).coverage == []
