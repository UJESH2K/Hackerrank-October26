"""Parse JSON evidence into observations.

The JSON artefacts share no single schema, so the parser walks the tree instead
of matching shapes. The rule is simple: **any object carrying a string
``resource_id`` is a record.** Context accumulates on the way down - the data
store, the collection, and the most specific capture time seen so far - and
applies to every record below it.

Four shapes need explicit handling beyond the walk:

* ``profile_lookups``: ``{"profile": null, "status_code": 404}`` proves absence,
  while a ``profile`` object both proves presence and carries the identifiers.
* ``index_queries``: the query's lookup key is the identity link, and
  ``hits.total`` of zero proves the document is gone.
* ``operational_record_history``: several timed observations of the same
  resource. All are kept; the state reconstructor picks per ``audit_at``.
* a snapshot with an empty ``records`` list: no resource id appears, so it is
  recorded as coverage rather than as an observation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from audit.evidence.context import ParseContext, ParseResult
from audit.evidence.fields import (
    first_timestamp,
    flatten,
    is_nullish,
    link_keys,
    most_specific_time,
    present_from_attributes,
)
from audit.evidence.systems import (
    DEFAULT_COPY_LABEL,
    EXPORT_COPY_LABEL,
    system_for_resource,
)
from audit.models import CoverageFact, Observation

#: Keys naming the system a record belongs to.
_SYSTEM_KEYS: tuple[str, ...] = ("data_store", "system", "origin_system", "service")

#: Keys naming the collection a record sits in.
_COLLECTION_KEYS: tuple[str, ...] = ("collection", "collection_name", "record_type")

#: Keys holding the time a container was captured.
_CONTEXT_TIME_KEYS: tuple[str, ...] = ("captured_at", "exported_at", "snapshot_at")

#: Collections that are read out of an export artefact rather than a live store.
_EXPORT_COLLECTIONS: frozenset[str] = frozenset(
    {"customer_export_jobs", "customer_exports", "exports"}
)


@dataclass(frozen=True, slots=True)
class _Context:
    """Context inherited by records further down the tree."""

    system: str = ""
    collection: str = ""
    copy_label: str = DEFAULT_COPY_LABEL
    time: datetime | None = None


def _context_from(node: Mapping[str, Any], parent: _Context) -> _Context:
    """Refine the inherited context with whatever this node declares."""
    system = next(
        (str(node[key]) for key in _SYSTEM_KEYS if isinstance(node.get(key), str)),
        parent.system,
    )
    collection = next(
        (str(node[key]) for key in _COLLECTION_KEYS if isinstance(node.get(key), str)),
        parent.collection,
    )
    time = first_timestamp(
        str(node[key]) for key in _CONTEXT_TIME_KEYS if isinstance(node.get(key), str)
    )
    copy_label = (
        EXPORT_COPY_LABEL if collection in _EXPORT_COLLECTIONS else parent.copy_label
    )
    return _Context(
        system=system,
        collection=collection,
        copy_label=copy_label,
        time=time or parent.time,
    )


def _is_record(node: Any) -> bool:
    """True when a node is a resource record rather than a container."""
    return (
        isinstance(node, Mapping)
        and isinstance(node.get("resource_id"), str)
        and bool(node["resource_id"].strip())
    )


def _record_observation(
    node: Mapping[str, Any],
    context: _Context,
    parse_context: ParseContext,
) -> Observation:
    """Turn one record object into an observation."""
    attributes = flatten(node)
    if context.collection and "collection" not in attributes:
        attributes["collection"] = context.collection

    present = present_from_attributes(attributes)
    if present is None and "profile" in node:
        # ``"profile": null`` is how an account lookup reports a 404.
        present = node["profile"] is not None
    if present is None:
        # The record was found in a stored copy, so it exists in that copy.
        present = True

    resource_id = str(node["resource_id"]).strip()
    observed_at = most_specific_time(attributes, context.time, parse_context.captured_at)
    return Observation(
        evidence_id=parse_context.evidence_id,
        observed_at=observed_at,
        system_id=system_for_resource(resource_id, context.system),
        resource_id=resource_id,
        copy_label=context.copy_label,
        present=present,
        attributes=dict(sorted(attributes.items())),
        link_keys=frozenset(link_keys(attributes)),
    )


def _coverage(
    node: Mapping[str, Any],
    context: _Context,
    parse_context: ParseContext,
    record_count: int,
) -> CoverageFact | None:
    """Record that a snapshot enumerated a system copy at a point in time.

    Only emitted for a container that declares both the store it covers and its
    capture time; without those, an absent resource id proves nothing.
    """
    if not context.system or context.time is None:
        return None
    collection = next(
        (str(node[key]) for key in _COLLECTION_KEYS if isinstance(node.get(key), str)),
        context.collection,
    )
    return CoverageFact(
        evidence_id=parse_context.evidence_id,
        observed_at=context.time,
        system_id=context.system,
        copy_label=context.copy_label,
        collection=collection,
        record_count=record_count,
    )


def _walk(
    node: Any,
    context: _Context,
    parse_context: ParseContext,
    result: ParseResult,
) -> None:
    """Walk the tree, emitting an observation for every record found."""
    if isinstance(node, Mapping):
        local = _context_from(node, context)
        if _is_record(node):
            observation = _record_observation(node, local, parse_context)
            result.observations.append(observation)
            # Children of a record describe the same moment as the record, so
            # they inherit its resolved time and merge with it afterwards.
            local = replace(local, time=observation.observed_at)
        records = node.get("records")
        if isinstance(records, Sequence) and not isinstance(records, (str, bytes)):
            fact = _coverage(node, local, parse_context, len(records))
            if fact is not None:
                result.coverage.append(fact)
        for key, value in node.items():
            child = replace(local, system=_child_system(key, local))
            if isinstance(value, Mapping):
                _walk(value, child, parse_context, result)
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                for item in value:
                    _walk(item, child, parse_context, result)
    elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
        for item in node:
            _walk(item, context, parse_context, result)


def _child_system(key: str, context: _Context) -> str:
    """Treat a mapping keyed by system name as declaring that system.

    ``service_exports`` is ``{"account_service": {...}, "search_service": {...}}``,
    so the key itself is the context the children inherit.
    """
    if key.endswith(("_service", "_platform", "_store", "_storage", "_cluster")):
        return key
    return context.system


def parse_json(context: ParseContext) -> ParseResult:
    """Parse a JSON evidence file into observations and coverage facts."""
    result = ParseResult()
    try:
        payload = json.loads(context.path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        result.warnings.append(f"{context.path.name}: unreadable JSON ({error})")
        return result
    _walk(payload, _Context(time=context.captured_at), context, result)
    return result


def merge_duplicates(observations: Sequence[Observation]) -> list[Observation]:
    """Merge observations that describe the same fact by different paths.

    A search hit document is reachable both directly and under its query, and a
    SQL payload can repeat its own ``resource_id``. Both routes describe one
    fact, so they are merged rather than double-counted: attributes and link keys
    union, and a definite ``present`` wins over ``None``.
    """
    merged: dict[tuple[str, str, str, str, datetime], Observation] = {}
    for observation in observations:
        key = observation.identity_key
        existing = merged.get(key)
        if existing is None:
            merged[key] = observation
            continue
        attributes = dict(existing.attributes)
        for name, value in observation.attributes.items():
            if is_nullish(attributes.get(name, "")) and not is_nullish(value):
                attributes[name] = value
        present = existing.present if existing.present is not None else observation.present
        merged[key] = Observation(
            evidence_id=existing.evidence_id,
            observed_at=existing.observed_at,
            system_id=existing.system_id or observation.system_id,
            resource_id=existing.resource_id,
            copy_label=existing.copy_label,
            present=present,
            attributes=dict(sorted(attributes.items())),
            link_keys=existing.link_keys | observation.link_keys,
        )
    return list(merged.values())
