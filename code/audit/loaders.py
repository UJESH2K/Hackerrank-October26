"""Read the participant-facing CSV files in ``dataset/`` into frozen models.

This module is the only place that knows about CSV column names. Everything
downstream works with the dataclasses in :mod:`audit.models`, so a dataset
schema change is a one-file change here.

All timestamps become timezone-aware UTC ``datetime`` objects at load time, so no
later comparison against a case's ``audit_at`` can silently mix naive and aware
values.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from audit.models import (
    Activity,
    AuditEntry,
    EvidenceFile,
    Identity,
    ReportedStatus,
    Request,
    RequiredAction,
    Rule,
)

#: Dataset filenames, relative to the dataset directory.
REQUESTS_FILE = "deletion_requests.csv"
SAMPLE_REQUESTS_FILE = "sample_deletion_requests.csv"
IDENTITIES_FILE = "user_identities.csv"
AUDIT_LOG_FILE = "user_audit_log.csv"
ACTIVITY_FILE = "system_activity.csv"
RULES_FILE = "deletion_rules.csv"
EVIDENCE_MANIFEST_FILE = "evidence_files.csv"


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 dataset timestamp into an aware UTC ``datetime``.

    Dataset timestamps carry a trailing ``Z``. Values without an offset are
    assumed to be UTC, which is what every dataset file uses.

    Raises:
        ValueError: if ``value`` is empty or not ISO-8601.
    """
    text = value.strip()
    if not text:
        raise ValueError("empty timestamp")
    if text.endswith(("z", "Z")):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def case_sort_key(case_id: str) -> tuple[int, int, str]:
    """Sort key placing case ids in natural numeric order.

    ``deletion_requests.csv`` numbers cases without zero padding, so plain
    lexicographic order would put ``case_100`` before ``case_99``. Sorting on the
    integer suffix restores the dataset's own order. Ids without a numeric suffix
    sort after the numbered ones, by their raw text, so the key is still total.
    """
    _, separator, tail = case_id.rpartition("_")
    if separator and tail.isdigit():
        return (0, int(tail), case_id)
    return (1, 0, case_id)


def _rows(path: Path) -> Iterator[dict[str, str]]:
    """Yield the rows of a UTF-8 CSV file as dictionaries."""
    with path.open(encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def load_requests(path: Path) -> tuple[Request, ...]:
    """Load deletion requests, sorted into natural numeric ``case_id`` order."""
    requests = [
        Request(
            case_id=row["case_id"],
            email=row["email"],
            requested_at=parse_timestamp(row["requested_at"]),
            reported_status=ReportedStatus(row["reported_status"]),
            reported_at=parse_timestamp(row["reported_at"]),
            audit_at=parse_timestamp(row["audit_at"]),
            request_text=row["request_text"],
        )
        for row in _rows(path)
    ]
    requests.sort(key=lambda request: case_sort_key(request.case_id))
    return tuple(requests)


def load_identities(path: Path) -> tuple[Identity, ...]:
    """Load the current identity snapshot of every user."""
    return tuple(
        Identity(
            user_id=row["user_id"],
            name=row["name"],
            email=row["email"],
            phone=row["phone"],
            email_hash=row["email_hash"],
            updated_at=parse_timestamp(row["updated_at"]),
        )
        for row in _rows(path)
    )


def load_audit_log(path: Path) -> tuple[AuditEntry, ...]:
    """Load historical identity changes, oldest first."""
    entries = [
        AuditEntry(
            audit_id=row["audit_id"],
            user_id=row["user_id"],
            event_time=parse_timestamp(row["event_time"]),
            field_name=row["field_name"],
            old_value=row["old_value"],
            new_value=row["new_value"],
        )
        for row in _rows(path)
    ]
    entries.sort(key=lambda entry: (entry.event_time, entry.audit_id))
    return tuple(entries)


def load_activities(path: Path) -> tuple[Activity, ...]:
    """Load the global system activity stream, oldest first."""
    activities = [
        Activity(
            activity_id=row["activity_id"],
            event_time=parse_timestamp(row["event_time"]),
            event_type=row["event_type"],
            source_system=row["source_system"],
            target_system=row["target_system"],
            resource_id=row["resource_id"],
            reported_result=row["reported_result"],
            details=row["details"],
        )
        for row in _rows(path)
    ]
    activities.sort(key=lambda activity: (activity.event_time, activity.activity_id))
    return tuple(activities)


def load_rules(path: Path) -> tuple[Rule, ...]:
    """Load the deletion policy."""
    return tuple(
        Rule(
            rule_id=row["rule_id"],
            data_category=row["data_category"],
            condition=row["condition"],
            required_action=RequiredAction(row["required_action"]),
        )
        for row in _rows(path)
    )


def load_evidence_files(path: Path) -> tuple[EvidenceFile, ...]:
    """Load the evidence manifest."""
    return tuple(
        EvidenceFile(
            file_id=row["file_id"],
            file_path=row["file_path"],
            origin=row["origin"],
            captured_at=parse_timestamp(row["captured_at"]),
        )
        for row in _rows(path)
    )


@dataclass(frozen=True, slots=True)
class Dataset:
    """Every participant-facing input, loaded once and shared by the pipeline."""

    dataset_dir: Path
    requests: tuple[Request, ...]
    identities: tuple[Identity, ...]
    audit_log: tuple[AuditEntry, ...]
    activities: tuple[Activity, ...]
    rules: tuple[Rule, ...]
    evidence_files: tuple[EvidenceFile, ...]

    def evidence_path(self, evidence_file: EvidenceFile) -> Path:
        """Absolute path of an evidence artefact.

        Manifest paths are written relative to the dataset directory, for example
        ``evidence/logs/service_events.log``.
        """
        return self.dataset_dir / evidence_file.file_path

    def select(self, case_ids: Sequence[str]) -> tuple[Request, ...]:
        """Return the requests whose ``case_id`` appears in ``case_ids``.

        Order follows the dataset, not the order the caller listed.
        """
        wanted = set(case_ids)
        return tuple(request for request in self.requests if request.case_id in wanted)


def load_dataset(dataset_dir: Path, requests_file: Path | None = None) -> Dataset:
    """Load every dataset file.

    Args:
        dataset_dir: the ``dataset/`` directory.
        requests_file: the requests CSV to audit. Defaults to the full evaluation
            set; the evaluation harness passes the sample file instead.
    """
    return Dataset(
        dataset_dir=dataset_dir,
        requests=load_requests(requests_file or dataset_dir / REQUESTS_FILE),
        identities=load_identities(dataset_dir / IDENTITIES_FILE),
        audit_log=load_audit_log(dataset_dir / AUDIT_LOG_FILE),
        activities=load_activities(dataset_dir / ACTIVITY_FILE),
        rules=load_rules(dataset_dir / RULES_FILE),
        evidence_files=load_evidence_files(dataset_dir / EVIDENCE_MANIFEST_FILE),
    )
