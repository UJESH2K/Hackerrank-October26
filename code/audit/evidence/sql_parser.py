"""Parse SQLite-compatible snapshot exports into observations.

The ``.sql`` files are executed into an in-memory SQLite database rather than
pattern-matched, so quoting, escapes and multi-line DDL are SQLite's problem
rather than ours.

Two schemas appear:

* **typed** - one table per record kind, optionally suffixed with a copy label:
  ``customer_profiles_primary`` and ``customer_profiles_failover_replica`` side by
  side. A resource present in the replica table and absent from the primary is
  replication drift, which decision D10 records as a remaining record.
* **generic** - ``customer_data_<copy>(resource_id, origin_system,
  collection_name, record_json, updated_at)``, where the real record is a JSON
  payload in a column.

Coverage facts matter as much as the rows: a snapshot that enumerated
``account_service`` at ``T`` proves the absence of every ``account_*`` it does
not contain. The ``snapshot_scope`` table states that coverage outright; for
other schemas it comes from the table's base name or its ``origin_system``
values.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from audit.evidence.context import ParseContext, ParseResult
from audit.evidence.fields import (
    flatten,
    is_nullish,
    link_keys,
    most_specific_time,
    present_from_attributes,
)
from audit.evidence.systems import (
    TABLE_BASE_SYSTEM,
    split_table_name,
    system_for_resource,
)
from audit.loaders import parse_timestamp
from audit.models import CoverageFact, Observation

#: The header line every generated snapshot carries.
_CAPTURED_AT = re.compile(r"^--\s*Captured at:\s*(?P<timestamp>\S+)\s*$", re.MULTILINE)
_TOPOLOGY = re.compile(r"^--\s*Database topology:\s*(?P<topology>\S+)\s*$", re.MULTILINE)

#: The table that declares what a snapshot covered.
SCOPE_TABLE = "snapshot_scope"

#: Columns that hold a JSON document rather than a scalar.
_JSON_COLUMN_HINTS: frozenset[str] = frozenset(
    {"record_json", "payload", "document", "record", "data", "body"}
)


def _header_time(sql: str) -> datetime | None:
    """The ``-- Captured at:`` header time, if present."""
    match = _CAPTURED_AT.search(sql)
    if match is None:
        return None
    try:
        return parse_timestamp(match.group("timestamp"))
    except ValueError:
        return None


def _topology(sql: str) -> str:
    """The ``-- Database topology:`` header value, for diagnostics."""
    match = _TOPOLOGY.search(sql)
    return match.group("topology") if match else ""


def _load(sql: str) -> sqlite3.Connection:
    """Execute a snapshot export into a fresh in-memory database."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(sql)
    return connection


def _tables(connection: sqlite3.Connection) -> list[str]:
    """Every table in the snapshot, in a stable order."""
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    return [str(row["name"]) for row in rows]


def _scope(connection: sqlite3.Connection, tables: list[str]) -> dict[str, datetime]:
    """Read ``snapshot_scope`` into ``origin_system -> captured_at``."""
    if SCOPE_TABLE not in tables:
        return {}
    scope: dict[str, datetime] = {}
    for row in connection.execute(f"SELECT * FROM {SCOPE_TABLE}").fetchall():
        try:
            scope[str(row["origin_system"])] = parse_timestamp(str(row["captured_at"]))
        except (ValueError, IndexError, KeyError):
            continue
    return scope


def _expand_json_columns(row: Mapping[str, Any]) -> dict[str, Any]:
    """Replace JSON-document columns with their parsed contents.

    A payload's keys are added only where the row does not already define them,
    so a real column always wins over the document that mirrors it.
    """
    expanded: dict[str, Any] = {}
    payloads: list[Mapping[str, Any]] = []
    for key, value in row.items():
        name = str(key)
        if isinstance(value, str) and (
            name in _JSON_COLUMN_HINTS or value.strip().startswith("{")
        ):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                expanded[name] = value
                continue
            if isinstance(parsed, Mapping):
                payloads.append(parsed)
                continue
        expanded[name] = value
    merged = flatten(expanded)
    for payload in payloads:
        for key, value in flatten(payload).items():
            if is_nullish(merged.get(key, "")):
                merged[key] = value
    return dict(merged)


def _coverage_systems(
    table: str,
    base: str,
    connection: sqlite3.Connection,
    columns: set[str],
) -> set[str]:
    """Which systems a table enumerates.

    Taken from the table's base name when that is known, otherwise from the
    distinct ``origin_system`` values the table holds. The base name is preferred
    because an empty table still covers its system.
    """
    if base in TABLE_BASE_SYSTEM:
        return {TABLE_BASE_SYSTEM[base]}
    if "origin_system" not in columns:
        return set()
    rows = connection.execute(
        f'SELECT DISTINCT origin_system FROM "{table}"'  # noqa: S608 - name from sqlite_master
    ).fetchall()
    return {str(row[0]) for row in rows if row[0]}


def parse_sql(context: ParseContext) -> ParseResult:
    """Parse a SQL snapshot export into observations and coverage facts."""
    result = ParseResult()
    sql = context.path.read_text(encoding="utf-8", errors="replace")
    try:
        connection = _load(sql)
    except sqlite3.Error as error:
        result.warnings.append(f"{context.path.name}: SQL did not execute ({error})")
        return result

    header_time = _header_time(sql)
    topology = _topology(sql)
    try:
        tables = _tables(connection)
        scope = _scope(connection, tables)
        for table in tables:
            if table == SCOPE_TABLE:
                continue
            base, copy_label = split_table_name(table)
            rows = connection.execute(f'SELECT * FROM "{table}"').fetchall()  # noqa: S608
            # sqlite3.Row iterates values, so .keys() is the only way to read names.
            columns = {
                str(key) for row in rows for key in row.keys()  # noqa: SIM118
            } if rows else set(
                _columns(connection, table)
            )
            covered = _coverage_systems(table, base, connection, columns)
            counts: dict[str, int] = dict.fromkeys(covered, 0)

            for row in rows:
                attributes = _expand_json_columns(dict(row))
                resource_id = attributes.get("resource_id", "").strip()
                if not resource_id:
                    continue
                origin = attributes.get("origin_system", "")
                system_id = system_for_resource(
                    resource_id, origin or TABLE_BASE_SYSTEM.get(base, "")
                )
                scope_time = scope.get(origin) or scope.get(system_id)
                attributes.setdefault("snapshot_table", table)
                if topology:
                    attributes.setdefault("topology", topology)
                present = present_from_attributes(attributes)
                result.observations.append(
                    Observation(
                        evidence_id=context.evidence_id,
                        observed_at=most_specific_time(
                            attributes, scope_time, header_time, context.captured_at
                        ),
                        system_id=system_id,
                        resource_id=resource_id,
                        copy_label=copy_label,
                        present=True if present is None else present,
                        attributes=dict(sorted(attributes.items())),
                        link_keys=frozenset(link_keys(attributes)),
                    )
                )
                counts[system_id] = counts.get(system_id, 0) + 1

            for system_id in sorted(set(covered) | set(counts)):
                observed_at = scope.get(system_id) or header_time or context.captured_at
                result.coverage.append(
                    CoverageFact(
                        evidence_id=context.evidence_id,
                        observed_at=observed_at,
                        system_id=system_id,
                        copy_label=copy_label,
                        collection=table,
                        record_count=counts.get(system_id, 0),
                    )
                )
    finally:
        connection.close()
    return result


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    """Column names of a table, used when it holds no rows."""
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [str(row["name"]) for row in rows]
