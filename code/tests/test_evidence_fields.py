"""Tests for the shared field helpers: flattening, link keys, presence, time."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from audit.evidence.fields import (
    flatten,
    is_identifier_value,
    is_nullish,
    link_keys,
    most_specific_time,
    normalise_link_key,
    presence_from_readback,
    present_from_attributes,
)
from audit.evidence.systems import split_table_name, system_for_resource


def test_flatten_merges_transparent_containers() -> None:
    """A ``profile`` wrapper must not become part of the attribute name."""
    flat = flatten({"resource_id": "account_1", "profile": {"email": "a@b.com"}})
    assert flat == {"resource_id": "account_1", "email": "a@b.com"}


def test_flatten_keeps_dotted_paths_for_real_nesting() -> None:
    flat = flatten({"resource_id": "analytics_1", "metrics": {"sessions_30d": 9}})
    assert flat["metrics.sessions_30d"] == "9"


def test_flatten_indexes_lists_of_objects() -> None:
    flat = flatten({"recent_activity": [{"event": "product_viewed"}]})
    assert flat["recent_activity.0.event"] == "product_viewed"


def test_flatten_renders_booleans_consistently() -> None:
    """A SQLite ``1`` and a JSON ``true`` must compare equal downstream."""
    assert flatten({"enabled": True})["enabled"] == "true"
    assert flatten({"restore_allowed": False})["restore_allowed"] == "false"


@pytest.mark.parametrize(
    "value",
    ["", "null", "NULL", "none", "[REDACTED]", "no direct identifier", "-"],
)
def test_nullish_values(value: str) -> None:
    assert is_nullish(value)


def test_null_identifiers_are_not_link_keys() -> None:
    """A tombstone with scrubbed fields must yield no identifier links."""
    attributes = {
        "resource_id": "tombstone_1",
        "subject_reference": "user_7",
        "email": "null",
        "phone": "[REDACTED]",
        "name": "null",
    }
    assert link_keys(attributes) == {"user_7"}


def test_link_keys_find_emails_in_free_text() -> None:
    attributes = {"message_text": "Please contact me at Jo.Blogs@Example.com about this."}
    assert link_keys(attributes) == {"jo.blogs@example.com"}


def test_link_keys_match_on_the_leaf_of_a_dotted_path() -> None:
    attributes = {"documents.0.email": "a@b.com"}
    assert link_keys(attributes) == {"a@b.com"}


def test_names_are_excluded_unless_requested() -> None:
    attributes = {"full_name": "Leila Haddad"}
    assert link_keys(attributes) == set()
    assert link_keys(attributes, include_names=True) == {"leila haddad"}


def test_phone_link_keys_normalise_to_digits() -> None:
    """The same number is punctuated differently in different files."""
    assert normalise_link_key("+212 6 48 27 15 93") == "+212648271593"
    assert normalise_link_key("+212648271593") == "+212648271593"


def test_email_link_keys_normalise_case() -> None:
    assert normalise_link_key("O.ERIKSEN10@YAHOO.COM") == "o.eriksen10@yahoo.com"


@pytest.mark.parametrize(
    "value",
    ["a@b.com", "user_42", "+44 7700 900123", "a" * 0 + "0" * 64],
)
def test_identifier_shapes(value: str) -> None:
    assert is_identifier_value(value)


def test_plain_words_are_not_identifiers() -> None:
    assert not is_identifier_value("customer_directory_v3")
    assert not is_identifier_value("ACTIVE")


@pytest.mark.parametrize(
    ("attributes", "expected"),
    [
        ({"status_code": "200"}, True),
        ({"status_code": "404"}, False),
        ({"http_status": "404"}, False),
        ({"rows": "0"}, False),
        ({"rows": "1"}, True),
        ({"result": "FOUND"}, True),
        ({"result": "NOT_FOUND"}, False),
        ({"result": "hit"}, True),
        ({"result": "miss"}, False),
        ({"result": "REDACTED"}, True),
        ({"result": "ANONYMIZED"}, True),
        ({"result": "BLOCKED"}, True),
        ({"result": "DISABLED"}, True),
        ({"query": "a@b.com", "total": "0"}, False),
        ({"query": "a@b.com", "total": "1"}, True),
        ({"index_name": "customer_directory_v3"}, None),
    ],
)
def test_presence_signals(attributes: dict[str, str], expected: bool | None) -> None:
    assert present_from_attributes(attributes) is expected


def test_status_label_does_not_override_a_status_code() -> None:
    """``status_code`` speaks to existence more directly than a label."""
    assert present_from_attributes({"status_code": "404", "result": "FOUND"}) is False


def test_most_specific_time_prefers_the_record() -> None:
    snapshot = datetime(2026, 2, 1, tzinfo=UTC)
    captured = datetime(2026, 3, 1, tzinfo=UTC)
    attributes = {"observed_at": "2026-01-15T09:00:00Z"}
    assert most_specific_time(attributes, snapshot, captured) == datetime(
        2026, 1, 15, 9, 0, tzinfo=UTC
    )


def test_most_specific_time_falls_back_in_order() -> None:
    snapshot = datetime(2026, 2, 1, tzinfo=UTC)
    captured = datetime(2026, 3, 1, tzinfo=UTC)
    assert most_specific_time({}, snapshot, captured) == snapshot
    assert most_specific_time({}, None, captured) == captured


def test_most_specific_time_requires_a_time() -> None:
    with pytest.raises(ValueError, match="no timestamp"):
        most_specific_time({})


@pytest.mark.parametrize(
    ("resource_id", "system_id"),
    [
        ("account_005", "account_service"),
        ("search_1874", "search_service"),
        ("analytics_901", "analytics_platform"),
        ("support_002", "support_platform"),
        ("export_001", "export_service"),
        ("data_flow_3669", "integration_service"),
        ("backup_706", "backup_service"),
        ("audit_record_003", "security_service"),
        ("tombstone_205", "identity_service"),
        ("legal_hold_12", "legal_service"),
    ],
)
def test_system_for_resource(resource_id: str, system_id: str) -> None:
    assert system_for_resource(resource_id) == system_id


def test_audit_record_prefix_is_not_read_as_audit() -> None:
    """Longest-prefix matching: ``audit_record_1`` is not an ``audit`` resource."""
    assert system_for_resource("audit_record_1") == "security_service"


def test_unknown_prefix_uses_the_fallback() -> None:
    assert system_for_resource("widget_9", "widget_service") == "widget_service"
    assert system_for_resource("widget_9") == ""


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ("customer_profiles", ("customer_profiles", "primary")),
        ("customer_profiles_primary", ("customer_profiles", "primary")),
        ("customer_data_read_replica", ("customer_data", "read_replica")),
        ("customer_data_reporting_replica", ("customer_data", "reporting_replica")),
        ("customer_profiles_failover_replica", ("customer_profiles", "failover_replica")),
        ("customer_data_replica_eu_west", ("customer_data", "replica_eu_west")),
        ("customer_data_replica_ap_south", ("customer_data", "replica_ap_south")),
    ],
)
def test_copy_label_comes_from_the_table_suffix(
    table: str, expected: tuple[str, str]
) -> None:
    assert split_table_name(table) == expected


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        ("profile_lookup_completed", True),
        ("analytics_profile_read", True),
        ("connector_config_loaded", True),
        ("message_pii_scan", True),
        ("restore_candidate_evaluated", True),
        ("connector_heartbeat", True),
        ("profile_lookup_started", None),
        ("analytics_profile_read_started", None),
        ("thumbnail_failed", None),
        ("delivery_abandoned", None),
        ("", None),
        ("profile_lookup_started,profile_lookup_completed", True),
    ],
)
def test_readback_inference(events: str, expected: bool | None) -> None:
    """A completed read proves the record was there; an opened one proves nothing."""
    assert presence_from_readback({"events": events}) is expected


def test_readback_never_overrides_an_explicit_signal() -> None:
    """A 404 on a completed lookup still means absent."""
    attributes = {"events": "profile_lookup_completed", "http_status": "404"}
    assert present_from_attributes(attributes) is False
    assert presence_from_readback(attributes) is True
