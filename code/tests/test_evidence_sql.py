"""Tests for the SQL snapshot parser, against synthetic fixtures only."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from audit.evidence.context import ParseContext
from audit.evidence.sql_parser import parse_sql

CAPTURED_AT = datetime(2026, 9, 2, 23, 0, tzinfo=UTC)

TYPED_SNAPSHOT_WITH_DRIFT = """
-- Ghost Data Still Exists: point-in-time database snapshot
-- Dialect: SQLite-compatible SQL
-- Captured at: 2026-09-02T23:00:00Z
-- Database topology: failover_replica

BEGIN TRANSACTION;
CREATE TABLE customer_profiles_failover_replica (
    resource_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    full_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL,
    account_status TEXT NOT NULL, updated_at TEXT NOT NULL
);
INSERT INTO "customer_profiles_failover_replica" VALUES('account_005','user_05',
    'Leila Haddad','leila.haddad@yahoo.com','+212 6 48 27 15 93','ACTIVE',
    '2026-09-02T22:45:00Z');
INSERT INTO "customer_profiles_failover_replica" VALUES('account_8741','user_8741',
    'Karim El-Masri','karim.elmasri@gmail.com','+971 50 731 6284','ACTIVE',
    '2026-09-02T22:45:00Z');
CREATE TABLE customer_profiles_primary (
    resource_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    full_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL,
    account_status TEXT NOT NULL, updated_at TEXT NOT NULL
);
INSERT INTO "customer_profiles_primary" VALUES('account_8741','user_8741',
    'Karim El-Masri','karim.elmasri@gmail.com','+971 50 731 6284','ACTIVE',
    '2026-09-02T22:45:00Z');
COMMIT;
"""

GENERIC_SNAPSHOT_WITH_JSON_PAYLOAD = """
-- Captured at: 2026-09-20T12:00:00Z
-- Database topology: failover_replica
BEGIN TRANSACTION;
CREATE TABLE customer_data_failover_replica (
    resource_id TEXT PRIMARY KEY,
    origin_system TEXT NOT NULL,
    collection_name TEXT NOT NULL,
    record_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO "customer_data_failover_replica" VALUES('tombstone_205','identity_service',
    'deleted_customer_references',
    '{"resource_id":"tombstone_205","subject_reference":"user_543","name":null,
      "email":null,"phone":null,"email_hash":null,"account_status":"DELETED"}',
    '2026-09-20T12:00:00Z');
INSERT INTO "customer_data_failover_replica" VALUES('tombstone_3745','identity_service',
    'deleted_customer_references',
    '{"resource_id":"tombstone_3745","subject_reference":"user_548",
      "name":"Sofia Fernandez","email":"fernandez.sofia@icloud.com",
      "phone":"+34 668 324 642","email_hash":null,"account_status":"DELETED"}',
    '2026-09-20T12:00:00Z');
COMMIT;
"""

INVENTORY_SNAPSHOT = """
PRAGMA foreign_keys=ON;
BEGIN TRANSACTION;
CREATE TABLE snapshot_scope (origin_system TEXT PRIMARY KEY, captured_at TEXT NOT NULL);
CREATE TABLE customer_profiles (resource_id TEXT PRIMARY KEY, customer_reference TEXT,
    full_name TEXT, email TEXT, phone TEXT, email_hash TEXT,
    account_status TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE connector_subscriptions (resource_id TEXT PRIMARY KEY, customer_reference TEXT,
    customer_email TEXT, connector_name TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
    last_delivery_status TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO snapshot_scope VALUES ('account_service', '2026-02-13T07:43:27Z');
INSERT INTO snapshot_scope VALUES ('integration_service', '2026-02-13T07:43:27Z');
INSERT INTO customer_profiles VALUES ('account_634', NULL, NULL, 'O.ERIKSEN10@YAHOO.COM',
    NULL, NULL, 'DELETION_PENDING', '2026-02-13T06:34:26Z');
INSERT INTO connector_subscriptions VALUES ('data_flow_006', NULL,
    'INES.ROUSSEAU@OUTLOOK.COM', 'customer_events_connector', '1', 'delivered',
    '2026-02-13T07:20:06Z');
COMMIT;
-- snapshot_completed_at=2026-02-13T07:43:27Z
"""


def context_for(tmp_path: Path, sql: str, name: str = "snapshot.sql") -> ParseContext:
    """Write a fixture and return the context that parses it."""
    path = tmp_path / name
    path.write_text(sql, encoding="utf-8")
    return ParseContext(path=path, evidence_id="evidence_test", captured_at=CAPTURED_AT)


def test_replica_drift_is_visible_as_two_copies(tmp_path: Path) -> None:
    """A record in the replica and not in the primary is a remaining record."""
    result = parse_sql(context_for(tmp_path, TYPED_SNAPSHOT_WITH_DRIFT))
    copies = {
        (observation.resource_id, observation.copy_label): observation
        for observation in result.observations
    }
    assert ("account_005", "failover_replica") in copies
    assert ("account_005", "primary") not in copies
    assert ("account_8741", "primary") in copies
    assert ("account_8741", "failover_replica") in copies


def test_primary_coverage_makes_the_drift_provable(tmp_path: Path) -> None:
    """The primary table's coverage is what proves account_005 is gone there."""
    result = parse_sql(context_for(tmp_path, TYPED_SNAPSHOT_WITH_DRIFT))
    primary = [fact for fact in result.coverage if fact.copy_label == "primary"]
    assert len(primary) == 1
    assert primary[0].system_id == "account_service"
    assert primary[0].record_count == 1
    assert primary[0].observed_at == datetime(2026, 9, 2, 23, 0, tzinfo=UTC)


def test_row_level_time_beats_the_header(tmp_path: Path) -> None:
    result = parse_sql(context_for(tmp_path, TYPED_SNAPSHOT_WITH_DRIFT))
    observation = next(
        item for item in result.observations if item.resource_id == "account_005"
    )
    assert observation.observed_at == datetime(2026, 9, 2, 22, 45, tzinfo=UTC)
    assert observation.attributes["topology"] == "failover_replica"
    assert observation.attributes["snapshot_table"] == "customer_profiles_failover_replica"


def test_identifiers_including_phones_become_link_keys(tmp_path: Path) -> None:
    result = parse_sql(context_for(tmp_path, TYPED_SNAPSHOT_WITH_DRIFT))
    observation = next(
        item for item in result.observations if item.resource_id == "account_005"
    )
    assert observation.link_keys == {
        "leila.haddad@yahoo.com",
        "+212648271593",
        "user_05",
    }


def test_json_payload_columns_are_parsed_and_flattened(tmp_path: Path) -> None:
    result = parse_sql(context_for(tmp_path, GENERIC_SNAPSHOT_WITH_JSON_PAYLOAD))
    found = {item.resource_id: item for item in result.observations}
    scrubbed = found["tombstone_205"]
    assert scrubbed.system_id == "identity_service"
    assert scrubbed.copy_label == "failover_replica"
    assert scrubbed.attributes["account_status"] == "DELETED"
    assert scrubbed.link_keys == {"user_543"}


def test_a_tombstone_that_still_exposes_identifiers_is_visible(tmp_path: Path) -> None:
    """The point of the tombstone rule: the stable reference may stay, the rest may not."""
    result = parse_sql(context_for(tmp_path, GENERIC_SNAPSHOT_WITH_JSON_PAYLOAD))
    exposed = next(
        item for item in result.observations if item.resource_id == "tombstone_3745"
    )
    assert exposed.attributes["email"] == "fernandez.sofia@icloud.com"
    assert "fernandez.sofia@icloud.com" in exposed.link_keys
    assert "user_548" in exposed.link_keys


def test_generic_table_coverage_comes_from_origin_system(tmp_path: Path) -> None:
    result = parse_sql(context_for(tmp_path, GENERIC_SNAPSHOT_WITH_JSON_PAYLOAD))
    assert [
        (fact.system_id, fact.copy_label, fact.record_count) for fact in result.coverage
    ] == [("identity_service", "failover_replica", 2)]


def test_inventory_schema_uses_snapshot_scope_for_coverage(tmp_path: Path) -> None:
    result = parse_sql(context_for(tmp_path, INVENTORY_SNAPSHOT))
    coverage = {(fact.system_id, fact.record_count) for fact in result.coverage}
    assert coverage == {("account_service", 1), ("integration_service", 1)}
    assert all(fact.copy_label == "primary" for fact in result.coverage)


def test_inventory_rows_become_observations_with_upper_case_emails_normalised(
    tmp_path: Path,
) -> None:
    result = parse_sql(context_for(tmp_path, INVENTORY_SNAPSHOT))
    found = {item.resource_id: item for item in result.observations}
    assert found["account_634"].present is True
    assert found["account_634"].link_keys == {"o.eriksen10@yahoo.com"}
    assert found["data_flow_006"].system_id == "integration_service"
    assert found["data_flow_006"].attributes["enabled"] == "1"


def test_broken_sql_is_reported_not_raised(tmp_path: Path) -> None:
    result = parse_sql(context_for(tmp_path, "CREATE TABLE ;;; broken", "bad.sql"))
    assert result.observations == []
    assert len(result.warnings) == 1
    assert "did not execute" in result.warnings[0]
