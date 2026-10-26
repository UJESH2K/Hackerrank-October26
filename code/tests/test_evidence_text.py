"""Tests for the shift-handover text parser."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from audit.evidence.context import ParseContext
from audit.evidence.text_parser import parse_text

CAPTURED_AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

HANDOVER = """CUSTOMER PLATFORM - SHIFT HANDOVER
Snapshot: 2026-10-01T12:00:00Z

Account and downstream checks completed during the shift:

- 2026-10-01T12:00:00Z | account_service | account_146 | USER_PROFILE | NOT_FOUND | e.lindberg18@yahoo.com
- 2026-10-01T12:00:00Z | account_service | account_1810 | USER_PROFILE | NOT_FOUND | CHOI.JIHOON94@GMAIL.COM
- 2026-10-01T12:00:00Z | analytics_platform | analytics_2034 | ANALYTICS_PROFILE | IDENTIFIABLE | novak.tomasz@icloud.com
- 2026-10-01T12:00:00Z | analytics_platform | analytics_6830 | ANALYTICS_PROFILE | ANONYMIZED | no direct identifier
- 2026-10-01T12:00:00Z | backup_service | backup_3870 | BACKUP_ARCHIVE | ACTIVE | y.tanaka32@gmail.com
- 2026-10-01T12:00:00Z | backup_service | backup_706 | BACKUP_ARCHIVE | BLOCKED | user_39
- 2026-10-01T12:00:00Z | integration_service | data_flow_2901 | DATA_FLOW | DISABLED | user_39
- 2026-10-01T12:00:00Z | support_platform | support_2667 | SUPPORT_MESSAGE | REDACTED | user_39
"""


def context_for(tmp_path: Path, body: str) -> ParseContext:
    """Write a fixture and return the context that parses it."""
    path = tmp_path / "handover.txt"
    path.write_text(body, encoding="utf-8")
    return ParseContext(path=path, evidence_id="evidence_test", captured_at=CAPTURED_AT)


def test_every_check_line_becomes_an_observation(tmp_path: Path) -> None:
    result = parse_text(context_for(tmp_path, HANDOVER))
    assert len(result.observations) == 8
    assert result.warnings == []


def test_prose_lines_are_skipped_without_warnings(tmp_path: Path) -> None:
    result = parse_text(context_for(tmp_path, HANDOVER))
    assert result.warnings == []


def test_not_found_proves_absence(tmp_path: Path) -> None:
    found = {
        item.resource_id: item for item in parse_text(context_for(tmp_path, HANDOVER)).observations
    }
    assert found["account_146"].present is False
    assert found["account_146"].system_id == "account_service"
    assert found["account_146"].link_keys == {"e.lindberg18@yahoo.com"}


def test_upper_case_emails_normalise(tmp_path: Path) -> None:
    found = {
        item.resource_id: item for item in parse_text(context_for(tmp_path, HANDOVER)).observations
    }
    assert found["account_1810"].link_keys == {"choi.jihoon94@gmail.com"}


@pytest.mark.parametrize(
    ("resource_id", "status"),
    [
        ("analytics_2034", "IDENTIFIABLE"),
        ("analytics_6830", "ANONYMIZED"),
        ("backup_3870", "ACTIVE"),
        ("backup_706", "BLOCKED"),
        ("data_flow_2901", "DISABLED"),
        ("support_2667", "REDACTED"),
    ],
)
def test_rich_statuses_all_mean_the_record_exists(
    tmp_path: Path, resource_id: str, status: str
) -> None:
    """These labels say *how* a record exists, never that it is gone."""
    found = {
        item.resource_id: item for item in parse_text(context_for(tmp_path, HANDOVER)).observations
    }
    observation = found[resource_id]
    assert observation.present is True
    assert observation.attributes["result"] == status


def test_the_record_type_column_is_kept_as_the_data_category(tmp_path: Path) -> None:
    """``record_type`` is the only place a resource is tied to a policy category."""
    found = {
        item.resource_id: item for item in parse_text(context_for(tmp_path, HANDOVER)).observations
    }
    assert found["support_2667"].attributes["record_type"] == "SUPPORT_MESSAGE"
    assert found["backup_706"].attributes["record_type"] == "BACKUP_ARCHIVE"


def test_no_direct_identifier_is_not_a_link_key(tmp_path: Path) -> None:
    """An anonymised record must not be linked to anyone by that phrase."""
    found = {
        item.resource_id: item for item in parse_text(context_for(tmp_path, HANDOVER)).observations
    }
    assert found["analytics_6830"].link_keys == frozenset()
    assert "lookup_value" not in found["analytics_6830"].attributes


def test_user_references_are_link_keys(tmp_path: Path) -> None:
    found = {
        item.resource_id: item for item in parse_text(context_for(tmp_path, HANDOVER)).observations
    }
    assert found["support_2667"].link_keys == {"user_39"}


def test_a_malformed_bullet_line_is_reported(tmp_path: Path) -> None:
    body = "Snapshot: 2026-10-01T12:00:00Z\n\n- 2026-10-01T12:00:00Z | account_service\n"
    result = parse_text(context_for(tmp_path, body))
    assert result.observations == []
    assert len(result.warnings) == 1
    assert "unparsed handover line" in result.warnings[0]
