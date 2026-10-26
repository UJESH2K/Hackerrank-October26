"""Parse the shift-handover text record.

The format is one bullet per check::

    - 2026-10-01T12:00:00Z | account_service | account_146 | USER_PROFILE
      | NOT_FOUND | e.lindberg18@yahoo.com

The status column is the richest in the dataset: beyond ``FOUND`` and
``NOT_FOUND`` it carries ``IDENTIFIABLE``, ``ANONYMIZED``, ``REDACTED``,
``ACTIVE``, ``BLOCKED`` and ``DISABLED``. All of those mean the record exists;
they describe *how* it exists, which is what the policy layer needs. The last
column is a lookup key, except when it reads ``no direct identifier`` - that is
the absence of a link, not a link.
"""

from __future__ import annotations

import re

from audit.evidence.context import ParseContext, ParseResult
from audit.evidence.fields import (
    is_nullish,
    link_keys,
    present_from_attributes,
)
from audit.evidence.systems import DEFAULT_COPY_LABEL, system_for_resource
from audit.loaders import parse_timestamp
from audit.models import Observation

#: A handover check line. The leading bullet and the field count are both fixed.
_LINE = re.compile(
    r"^-\s*(?P<timestamp>\d{4}-\d{2}-\d{2}T[\d:]{8}Z)\s*\|"
    r"\s*(?P<system>[\w.-]+)\s*\|"
    r"\s*(?P<resource_id>[\w.-]+)\s*\|"
    r"\s*(?P<record_type>[\w.-]+)\s*\|"
    r"\s*(?P<status>[\w.-]+)\s*\|"
    r"\s*(?P<lookup>.*?)\s*$"
)

#: The snapshot time stated in the header, used if a line omits its own.
_SNAPSHOT = re.compile(r"^Snapshot:\s*(?P<timestamp>\S+)\s*$", re.MULTILINE)


def parse_text(context: ParseContext) -> ParseResult:
    """Parse a handover text record into observations.

    Lines that are prose - the title, the snapshot header, the section heading -
    are not checks and are skipped without a warning. A bullet line that does not
    match the field layout is a genuine parse failure and is reported.
    """
    result = ParseResult()
    body = context.path.read_text(encoding="utf-8", errors="replace")
    header = _SNAPSHOT.search(body)
    snapshot_time = None
    if header is not None:
        try:
            snapshot_time = parse_timestamp(header.group("timestamp"))
        except ValueError:
            snapshot_time = None

    for number, line in enumerate(body.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        match = _LINE.match(stripped)
        if match is None:
            result.warnings.append(f"{context.path.name}:{number}: unparsed handover line")
            continue
        attributes = {
            "event": "handover_check",
            "system": match.group("system"),
            "resource_id": match.group("resource_id"),
            "record_type": match.group("record_type"),
            "result": match.group("status"),
        }
        lookup = match.group("lookup").strip()
        if not is_nullish(lookup):
            attributes["lookup_value"] = lookup
        resource_id = attributes["resource_id"]
        present = present_from_attributes(attributes)
        try:
            observed_at = parse_timestamp(match.group("timestamp"))
        except ValueError:
            observed_at = snapshot_time or context.captured_at
        result.observations.append(
            Observation(
                evidence_id=context.evidence_id,
                observed_at=observed_at,
                system_id=system_for_resource(resource_id, attributes["system"]),
                resource_id=resource_id,
                copy_label=DEFAULT_COPY_LABEL,
                present=present,
                attributes=dict(sorted(attributes.items())),
                link_keys=frozenset(link_keys(attributes)),
            )
        )
    return result
