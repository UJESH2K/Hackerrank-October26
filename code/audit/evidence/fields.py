"""Shared field handling for every evidence parser.

Three jobs, all format-independent:

* flatten a nested record into string attributes;
* decide whether a record exists in the copy being described;
* collect the identifier values that can tie a record to a person.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from audit.loaders import parse_timestamp

#: Values that carry no information. ``[REDACTED]`` is a real value in this
#: dataset: it means the field was scrubbed, so it is not an identifier.
NULLISH: frozenset[str] = frozenset(
    {
        "",
        "null",
        "none",
        "nil",
        "n/a",
        "na",
        "-",
        "[redacted]",
        "redacted",
        "<redacted>",
        "no direct identifier",
        "not available",
        "unknown",
    }
)

#: Field names whose value is, or contains, a lookup key for a person. These are
#: the names the evidence actually uses; they come from reading every format.
LINK_KEY_FIELDS: frozenset[str] = frozenset(
    {
        "email",
        "emails",
        "email_address",
        "customer_email",
        "owner_email",
        "requester_email",
        "uploaded_by",
        "email_hash",
        "phone",
        "customer_phone",
        "owner_phone",
        "requester_phone",
        "user_id",
        "customer_reference",
        "subject_reference",
        "subject_ref",
        "subject_key",
        "subject_selector",
        "lookup_key",
        "lookup_value",
        "match_key",
        "matched_value",
        "correlation_key",
        "scan_key",
        "owner_key",
        "link_key",
        "query",
        "name",
        "full_name",
        "display_name",
        "requester_name",
    }
)

#: Names among ``LINK_KEY_FIELDS`` that hold a person's name rather than a
#: machine identifier. Names are weak links - two people can share one - so they
#: are kept separately and never used alone to attribute a record.
NAME_FIELDS: frozenset[str] = frozenset(
    {"name", "full_name", "display_name", "requester_name"}
)

#: Sub-object keys that merge into the parent record instead of being nested.
#: ``{"profile": {"email": ...}}`` should yield ``email``, not ``profile.email``.
TRANSPARENT_CONTAINERS: frozenset[str] = frozenset(
    {"profile", "document", "record", "hits", "payload", "data", "fields", "details"}
)

#: ``result`` / status values meaning the record exists in the copy. Labels such
#: as ``redacted`` or ``blocked`` describe how it exists, not whether it does.
PRESENT_RESULTS: frozenset[str] = frozenset(
    {
        "found",
        "hit",
        "present",
        "exists",
        "identifiable",
        "anonymized",
        "anonymised",
        "redacted",
        "active",
        "enabled",
        "blocked",
        "disabled",
        "retained",
        "ok",
    }
)

#: ``result`` / status values meaning the record is gone from the copy.
ABSENT_RESULTS: frozenset[str] = frozenset(
    {
        "not_found",
        "notfound",
        "not found",
        "missing",
        "miss",
        "absent",
        "deleted",
        "removed",
        "no_match",
        "none",
        "empty",
        "expired",
        "unavailable",
        "gone",
    }
)

#: HTTP statuses that prove absence rather than an unrelated failure.
ABSENT_STATUS_CODES: frozenset[int] = frozenset({404, 410})

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
USER_REFERENCE_PATTERN = re.compile(r"^user_\d+$", re.IGNORECASE)
EMAIL_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"^\+?[\d][\d\s().-]{6,}$")
_NON_DIGITS = re.compile(r"\D")
#: Record fields that state *when the record was observed*, in priority order.
#:
#: ``created_at``, ``expires_at`` and ``last_write`` are deliberately absent.
#: They are properties of the record, not of the observation: an export job
#: created in August and seen in a September snapshot was observed in September.
#: Treating ``created_at`` as the observation time dated that record five weeks
#: early and pushed it outside its own deletion episode.
_TIMESTAMP_FIELDS: tuple[str, ...] = (
    "observed_at",
    "recorded_at",
    "updated_at",
    "indexed_at",
    "captured_at",
    "event_time",
    "snapshot_at",
    "timestamp",
)


def is_nullish(value: Any) -> bool:
    """True when a value carries no information."""
    if value is None:
        return True
    return str(value).strip().casefold() in NULLISH


def as_text(value: Any) -> str:
    """Render a scalar as the string stored in ``Observation.attributes``.

    Booleans become ``true``/``false`` so that a SQLite ``0``/``1`` column and a
    JSON ``false`` compare equal downstream.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value).strip()


def flatten(record: Mapping[str, Any], prefix: str = "") -> dict[str, str]:
    """Flatten a nested record into string attributes.

    Keys in :data:`TRANSPARENT_CONTAINERS` merge into the parent rather than
    nesting, because the evidence wraps the same identity fields in ``profile``,
    ``document`` and ``record`` objects depending on the format. Everything else
    keeps a dotted path, so ``metrics.sessions_30d`` stays distinguishable.
    """
    flat: dict[str, str] = {}
    for key, value in record.items():
        name = str(key)
        path = name if not prefix else f"{prefix}.{name}"
        if isinstance(value, Mapping):
            inner = "" if name in TRANSPARENT_CONTAINERS and not prefix else path
            flat.update(flatten(value, inner))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                if isinstance(item, Mapping):
                    flat.update(flatten(item, f"{path}.{index}"))
                elif not is_nullish(item):
                    flat[f"{path}.{index}"] = as_text(item)
        elif value is not None:
            flat[path] = as_text(value)
    return flat


def normalise_link_key(value: str) -> str:
    """Canonical form of an identifier, used when storing and when querying.

    Emails, hashes and user references lower-case. A phone number keeps only its
    digits, because the same number is written ``+971 50 482 7316`` in one file
    and ``+971504827316`` in another.
    """
    text = value.strip()
    if PHONE_PATTERN.match(text) and not EMAIL_PATTERN.search(text):
        digits = _NON_DIGITS.sub("", text)
        return f"+{digits}" if digits else ""
    return text.casefold()


def is_identifier_value(value: str) -> bool:
    """True when a value can identify a person on its own."""
    text = value.strip()
    if is_nullish(text):
        return False
    if EMAIL_PATTERN.search(text):
        return True
    if USER_REFERENCE_PATTERN.match(text):
        return True
    if EMAIL_HASH_PATTERN.match(text):
        return True
    return bool(PHONE_PATTERN.match(text))


def link_keys(attributes: Mapping[str, str], include_names: bool = False) -> set[str]:
    """Collect the identifier values in ``attributes``.

    Looks at the fields named in :data:`LINK_KEY_FIELDS` - matched on the leaf of
    a dotted path, so ``documents.0.email`` counts - and additionally harvests
    any email address embedded in free text, which is how support messages and
    handover notes carry identity.

    Args:
        attributes: the flattened record.
        include_names: also return person names. Off by default: a name is not a
            unique identifier, so it is a hint, not a link.
    """
    found: set[str] = set()
    for key, raw in attributes.items():
        leaf = key.rpartition(".")[2]
        value = raw.strip()
        if is_nullish(value):
            continue
        if leaf in LINK_KEY_FIELDS:
            if leaf in NAME_FIELDS:
                if include_names:
                    found.add(normalise_link_key(value))
                continue
            if is_identifier_value(value):
                found.add(normalise_link_key(value))
        for embedded in EMAIL_PATTERN.findall(value):
            found.add(normalise_link_key(embedded))
    return {key for key in found if key}


def present_from_attributes(attributes: Mapping[str, str]) -> bool | None:
    """Decide whether the described record exists in the copy.

    Reads the presence signals the evidence uses, in order of how directly they
    speak to existence. Returns ``None`` when the record is described without its
    existence being established.
    """
    for field_name in ("status_code", "http_status", "status"):
        raw = attributes.get(field_name, "").strip()
        if raw.isdigit():
            code = int(raw)
            if code in ABSENT_STATUS_CODES:
                return False
            if 200 <= code < 300:
                return True

    for field_name in ("rows", "row_count", "matched_records", "records"):
        raw = attributes.get(field_name, "").strip()
        if raw.isdigit():
            return int(raw) > 0

    for field_name in ("result", "state", "record_state", "lookup_result", "outcome"):
        raw = attributes.get(field_name, "").strip().casefold()
        if not raw:
            continue
        if raw in ABSENT_RESULTS:
            return False
        if raw in PRESENT_RESULTS:
            return True

    if "query" in attributes:
        raw = attributes.get("total", "").strip()
        if raw.isdigit():
            return int(raw) > 0
    return None


#: Event-name endings that report the outcome of inspecting a named resource.
#: The log vocabulary is consistent: ``*_started`` opens an operation, and these
#: report what was found. Matched on the *end* of an event name, because
#: ``analytics_profile_read_started`` contains ``_read`` but is not a read-back.
READBACK_EVENT_SUFFIXES: tuple[str, ...] = (
    "_completed",
    "_evaluated",
    "_loaded",
    "_read",
    "_scan",
    "heartbeat",
)

#: Event-name fragments that mark an operation that did not complete. A failure
#: says nothing about whether the resource exists.
FAILED_EVENT_FRAGMENTS: tuple[str, ...] = ("_failed", "_error", "_abandoned", "_denied")


def presence_from_readback(attributes: Mapping[str, str]) -> bool | None:
    """Infer presence from a completed read, scan or evaluation.

    A service that reports on a named resource has read that resource, so the
    resource exists. This is what makes the scan and heartbeat lines usable:
    ``message_pii_scan`` with ``matched_value`` set says the support message is
    still there *and* still exposes an identifier, which is the whole basis of a
    ``REPORT_EXPOSED_PII`` finding. Likewise ``connector_heartbeat`` with
    ``enabled=true`` is what a ``REPORT_ACTIVE_DATA_FLOW`` finding rests on.

    The inference is sound here because the log event vocabulary was enumerated
    in full: every ``*_completed``, ``*_read``, ``*_loaded``, ``*_scan`` and
    ``*_evaluated`` event in ``dataset/evidence/logs/`` reports a read, and none
    reports a deletion. Deletions live in ``system_activity.csv``, which is
    deliberately not an evidence source (decision D14).

    Returns ``None`` when the event has no such shape. Callers apply this only
    after :func:`present_from_attributes`, so an explicit ``404`` or ``rows=0``
    always wins.
    """
    raw = attributes.get("events", attributes.get("event", "")).casefold()
    if not raw:
        return None
    events = [event.strip() for event in raw.split(",") if event.strip()]
    for event in events:
        if any(fragment in event for fragment in FAILED_EVENT_FRAGMENTS):
            continue
        if any(event.endswith(suffix) for suffix in READBACK_EVENT_SUFFIXES):
            return True
    return None


def most_specific_time(
    attributes: Mapping[str, str],
    *fallbacks: datetime | None,
) -> datetime:
    """The most specific time available for an observation.

    Prefers a record-level time from ``attributes``, then each fallback in turn:
    the enclosing snapshot's time, then the file's ``captured_at``.

    Raises:
        ValueError: if neither the record nor any fallback carries a time.
    """
    for field_name in _TIMESTAMP_FIELDS:
        raw = attributes.get(field_name, "").strip()
        if not raw or is_nullish(raw):
            continue
        try:
            return parse_timestamp(raw)
        except ValueError:
            continue
    for fallback in fallbacks:
        if fallback is not None:
            return fallback
    raise ValueError("no timestamp available for observation")


def first_timestamp(values: Iterable[str]) -> datetime | None:
    """Parse the first value that is a timestamp, or return ``None``."""
    for value in values:
        text = value.strip()
        if not text or is_nullish(text):
            continue
        try:
            return parse_timestamp(text)
        except ValueError:
            continue
    return None
