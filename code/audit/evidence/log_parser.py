r"""Parse service logs into observations.

Six line styles appear in ``dataset/evidence/logs/``. They differ only in how the
prefix fields are punctuated, so every style is reduced to one
:class:`LogRecord` - time, service, trace, event, key/value pairs - and the
presence logic is then shared:

1. ``TS [LEVEL] [service] [trace] event: k="v"``
2. ``TS requestId=.. level=.. function=.. message="escaped k=\\"v\\""``
3. ``TS LEVEL service=.. event=record_lookup seq=N resource_id=".." result=".."``
4. ``TS | LEVEL | service | trace | event | k="v"``
5. ``TS level=.. service=.. trace_id=.. event=.. k=v``
6. ``TS {"severity":..,"service":..,"event":..}``
7. ``TS <pri>1 host service trace - - level=.. k="v"`` (syslog)

Some styles put the lookup key only on a ``*_started`` line and the resource and
result only on the matching ``*_completed`` line, joined by ``trace_id`` or
``requestId``. Those lines are merged into one observation per trace and
resource, so the identity link and the state survive together.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from audit.evidence.context import ParseContext, ParseResult
from audit.evidence.fields import (
    first_timestamp,
    is_nullish,
    link_keys,
    presence_from_readback,
    present_from_attributes,
)
from audit.evidence.systems import TRACE_COPY_LABEL, system_for_resource
from audit.loaders import parse_timestamp
from audit.models import Observation

_LINE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\s+(?P<rest>.*)$"
)
_SYSLOG = re.compile(
    r"^<\d+>\d+\s+(?P<host>\S+)\s+(?P<service>\S+)\s+(?P<trace>\S+)\s+-\s+-\s+(?P<rest>.*)$"
)
_BRACKETED = re.compile(
    r"^\[(?P<level>[A-Z]+)\]\s+\[(?P<service>[^\]]+)\]\s+\[(?P<trace>[^\]]+)\]\s+"
    r"(?P<event>[\w.-]+):\s*(?P<rest>.*)$"
)
_BARE_LEVEL = re.compile(r"^(?P<level>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\s+(?P<rest>.*)$")

#: Field names that carry the trace identifier, in priority order.
_TRACE_FIELDS: tuple[str, ...] = ("trace_id", "requestId", "request_id", "correlation_id")

#: Field names that carry the emitting service, in priority order.
_SERVICE_FIELDS: tuple[str, ...] = ("service", "function", "logger", "system")


@dataclass(frozen=True, slots=True)
class LogRecord:
    """One normalised log line."""

    timestamp: datetime
    service: str
    trace: str
    event: str
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def resource_id(self) -> str:
        """The resource this line names, or an empty string."""
        return self.fields.get("resource_id", "").strip()


def parse_logfmt(text: str) -> tuple[dict[str, str], list[str]]:
    """Parse ``key=value`` pairs, honouring quotes, spaces and backslash escapes.

    Returns the pairs and any bare tokens that carried no ``=``, such as the
    leading ``INFO`` of style 3.
    """
    pairs: dict[str, str] = {}
    bare: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break
        start = index
        while index < length and text[index] not in "= \t":
            index += 1
        key = text[start:index]
        if index < length and text[index] == "=":
            index += 1
            if index < length and text[index] == '"':
                index += 1
                buffer: list[str] = []
                while index < length:
                    char = text[index]
                    if char == "\\" and index + 1 < length:
                        buffer.append(text[index + 1])
                        index += 2
                        continue
                    if char == '"':
                        index += 1
                        break
                    buffer.append(char)
                    index += 1
                pairs[key] = "".join(buffer)
            else:
                start = index
                while index < length and not text[index].isspace():
                    index += 1
                pairs[key] = text[start:index]
        elif key:
            bare.append(key)
    return pairs, bare


def _normalise(fields: dict[str, str], bare: list[str]) -> dict[str, str]:
    """Drop empty values and record a bare level token as ``level``."""
    cleaned = {key: value for key, value in fields.items() if not is_nullish(value)}
    for token in bare:
        if _BARE_LEVEL.match(f"{token} x"):
            cleaned.setdefault("level", token)
    return cleaned


def parse_line(line: str) -> LogRecord | None:
    """Normalise one log line, or return ``None`` if it is not a log record."""
    match = _LINE.match(line.strip())
    if match is None:
        return None
    timestamp = parse_timestamp(match.group("timestamp"))
    rest = match.group("rest").strip()

    service = ""
    trace = ""
    event = ""
    fields: dict[str, str] = {}

    if rest.startswith("{"):
        try:
            payload = json.loads(rest)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        fields = {
            str(key): str(value)
            for key, value in payload.items()
            if not isinstance(value, (dict, list)) and not is_nullish(value)
        }
    elif (syslog := _SYSLOG.match(rest)) is not None:
        service = syslog.group("service")
        trace = syslog.group("trace")
        fields = _normalise(*parse_logfmt(syslog.group("rest")))
    elif rest.startswith("|"):
        parts = [part.strip() for part in rest.split("|", 5)]
        if len(parts) < 6:
            return None
        _, level, service, trace, event, payload_text = parts
        fields = _normalise(*parse_logfmt(payload_text))
        fields.setdefault("level", level)
    elif (bracketed := _BRACKETED.match(rest)) is not None:
        service = bracketed.group("service")
        trace = bracketed.group("trace")
        event = bracketed.group("event")
        fields = _normalise(*parse_logfmt(bracketed.group("rest")))
        fields.setdefault("level", bracketed.group("level"))
    else:
        if (level_match := _BARE_LEVEL.match(rest)) is not None:
            fields = _normalise(*parse_logfmt(level_match.group("rest")))
            fields.setdefault("level", level_match.group("level"))
        else:
            fields = _normalise(*parse_logfmt(rest))
        if "message" in fields:
            # Style 2 wraps the real payload in an escaped ``message`` value.
            inner, inner_bare = parse_logfmt(fields.pop("message"))
            fields.update(_normalise(inner, inner_bare))

    trace = next(
        (fields[name] for name in _TRACE_FIELDS if fields.get(name)),
        trace,
    )
    service = next(
        (fields[name] for name in _SERVICE_FIELDS if fields.get(name)),
        service,
    )
    event = fields.get("event", event)
    cleaned_trace = trace.strip()
    return LogRecord(
        timestamp=timestamp,
        service=service.strip(),
        trace="" if cleaned_trace == "-" else cleaned_trace,
        event=event.strip(),
        fields=fields,
    )


def _merge(into: dict[str, str], extra: dict[str, str]) -> None:
    """Fill gaps in ``into`` from ``extra`` without overwriting real values."""
    for key, value in extra.items():
        if is_nullish(value):
            continue
        if is_nullish(into.get(key, "")):
            into[key] = value


def _observation(
    evidence_id: str,
    resource_id: str,
    attributes: dict[str, str],
    observed_at: datetime,
    fallback_service: str,
) -> Observation:
    """Build one observation from merged log fields.

    An explicit presence signal always wins; a completed read or scan is only
    consulted when the line gave none.
    """
    present = present_from_attributes(attributes)
    if present is None:
        present = presence_from_readback(attributes)
    return Observation(
        evidence_id=evidence_id,
        observed_at=observed_at,
        system_id=system_for_resource(resource_id, fallback_service),
        resource_id=resource_id,
        copy_label=TRACE_COPY_LABEL,
        present=present,
        attributes=dict(sorted(attributes.items())),
        link_keys=frozenset(link_keys(attributes)),
    )


def parse_log(context: ParseContext) -> ParseResult:
    """Parse a log file into observations.

    Lines carrying a trace are merged per trace and resource, so a ``*_started``
    line's lookup key reaches the ``*_completed`` line's resource and result.
    Lines with a resource but no trace stand alone. Lines with neither - pool
    samples, config reloads, webhook retries - are infrastructure noise and are
    dropped.

    A line's own timestamp is always kept: log lines routinely predate the file's
    ``captured_at`` by days, and the line time is the more specific fact.
    """
    evidence_id = context.evidence_id
    result = ParseResult()
    records: list[LogRecord] = []
    for number, line in enumerate(
        context.path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        if not line.strip():
            continue
        record = parse_line(line)
        if record is None:
            result.warnings.append(f"{context.path.name}:{number}: unparsed line")
            continue
        records.append(record)

    observations = result.observations
    traced: dict[str, list[LogRecord]] = defaultdict(list)
    for record in records:
        if record.trace:
            traced[record.trace].append(record)
        elif record.resource_id:
            attributes = dict(record.fields)
            attributes.setdefault("event", record.event)
            observations.append(
                _observation(
                    evidence_id,
                    record.resource_id,
                    attributes,
                    first_timestamp([attributes.get("recorded_at", "")]) or record.timestamp,
                    record.service,
                )
            )

    for group in traced.values():
        group.sort(key=lambda item: item.timestamp)
        shared: dict[str, str] = {}
        for record in group:
            if not record.resource_id:
                _merge(shared, record.fields)
        per_resource: dict[str, dict[str, str]] = {}
        events: dict[str, list[str]] = defaultdict(list)
        latest: dict[str, datetime] = {}
        service_of: dict[str, str] = {}
        for record in group:
            resource_id = record.resource_id
            if not resource_id:
                continue
            attributes = per_resource.setdefault(resource_id, {})
            for key, value in record.fields.items():
                if not is_nullish(value):
                    attributes[key] = value
            if record.event and record.event not in events[resource_id]:
                events[resource_id].append(record.event)
            latest[resource_id] = record.timestamp
            service_of[resource_id] = record.service or service_of.get(resource_id, "")
        for resource_id, attributes in per_resource.items():
            _merge(attributes, shared)
            # One trace can report several events about a resource. Keeping the
            # whole sequence makes presence inference independent of which line
            # happened to be last.
            sequence = events[resource_id] or (
                [attributes["event"]] if attributes.get("event") else []
            )
            if sequence:
                attributes["event"] = sequence[-1]
                attributes["events"] = ",".join(sequence)
            observations.append(
                _observation(
                    evidence_id,
                    resource_id,
                    attributes,
                    latest[resource_id],
                    service_of.get(resource_id, ""),
                )
            )
    return result
