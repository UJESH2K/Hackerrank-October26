"""The single source of truth for system, copy-label and category vocabularies.

Every parser derives ``system_id`` from the ``resource_id`` prefix through
:func:`system_for_resource`. Nothing derives it from an activity row's
``target_system``: the activity stream routes ``support_*`` resources through
``search_service`` and ``export_*`` through ``object_store``, while the answer
format never does (see decision D8).
"""

from __future__ import annotations

import re

#: ``resource_id`` prefix -> the canonical system that owns the resource.
RESOURCE_PREFIX_SYSTEM: dict[str, str] = {
    "account": "account_service",
    "search": "search_service",
    "analytics": "analytics_platform",
    "support": "support_platform",
    "export": "export_service",
    "data_flow": "integration_service",
    "backup": "backup_service",
    "audit_record": "security_service",
    "tombstone": "identity_service",
    "cache": "cache_service",
    "email_delivery": "email_delivery_service",
    "legal_hold": "legal_service",
    "marketing": "marketing_platform",
    "object": "object_storage",
}

#: Prefixes longest-first, so ``audit_record_1`` is not read as ``audit``.
_PREFIXES_BY_LENGTH: tuple[str, ...] = tuple(
    sorted(RESOURCE_PREFIX_SYSTEM, key=len, reverse=True)
)

#: SQL table base name -> system, for coverage facts. A table can be empty, so
#: its coverage cannot always be derived from the rows it holds.
TABLE_BASE_SYSTEM: dict[str, str] = {
    "customer_profiles": "account_service",
    "customer_search_documents": "search_service",
    "search_documents": "search_service",
    "customer_analytics_profiles": "analytics_platform",
    "support_records": "support_platform",
    "customer_exports": "export_service",
    "connector_subscriptions": "integration_service",
    "backup_restore_entries": "backup_service",
    "security_audit_records": "security_service",
}

#: Copy labels that appear as a table-name suffix. ``replica_<region>`` is
#: matched by pattern rather than listed.
_FIXED_COPY_LABELS: tuple[str, ...] = (
    "read_replica",
    "reporting_replica",
    "failover_replica",
    "primary",
)

_REGIONAL_REPLICA = re.compile(r"^replica_[a-z0-9_]+$")

#: The copy label used when a source names no copy: a snapshot of the live store.
DEFAULT_COPY_LABEL = "primary"

#: Copy label for an observation reconstructed from a log trace rather than a
#: stored copy. A trace proves what the service answered, not where it lives.
TRACE_COPY_LABEL = "trace"

#: Copy label for records read out of an export or manifest artefact.
EXPORT_COPY_LABEL = "export"

#: ``record_type`` values the evidence uses. They are the ``data_category``
#: values of ``deletion_rules.csv``, which no participant CSV links to a
#: resource; the evidence states them directly.
KNOWN_RECORD_TYPES: frozenset[str] = frozenset(
    {
        "USER_PROFILE",
        "SECONDARY_IDENTIFIER",
        "SEARCH_INDEX_ENTRY",
        "CACHE_ENTRY",
        "SUPPORT_MESSAGE",
        "SUPPORT_ATTACHMENT",
        "ANALYTICS_PROFILE",
        "PRODUCT_TELEMETRY",
        "USER_EXPORT",
        "MARKETING_PROFILE",
        "EMAIL_DELIVERY_LOG",
        "SECURITY_AUDIT_LOG",
        "LEGAL_HOLD_RECORD",
        "BACKUP_ARCHIVE",
        "ACCOUNT_TOMBSTONE",
        "DATA_FLOW",
    }
)

_RESOURCE_ID = re.compile(r"^(?P<prefix>[a-z][a-z_]*?)_(?P<number>\d+)$")


def resource_prefix(resource_id: str) -> str:
    """The prefix of a resource id, e.g. ``audit_record`` in ``audit_record_12``.

    Returns the whole id when it carries no numeric suffix.
    """
    match = _RESOURCE_ID.match(resource_id.strip())
    return match.group("prefix") if match else resource_id.strip()


def system_for_resource(resource_id: str, fallback: str = "") -> str:
    """The canonical system that owns ``resource_id``.

    Args:
        resource_id: the resource id to classify.
        fallback: returned when the prefix is unknown; typically the system the
            source file named.
    """
    cleaned = resource_id.strip()
    prefix = resource_prefix(cleaned)
    if prefix in RESOURCE_PREFIX_SYSTEM:
        return RESOURCE_PREFIX_SYSTEM[prefix]
    for candidate in _PREFIXES_BY_LENGTH:
        if cleaned.startswith(f"{candidate}_"):
            return RESOURCE_PREFIX_SYSTEM[candidate]
    return fallback


def looks_like_resource_id(value: str) -> bool:
    """True when ``value`` has the shape of a resource id this dataset uses."""
    return _RESOURCE_ID.match(value.strip()) is not None


def split_table_name(table: str) -> tuple[str, str]:
    """Split a snapshot table name into its base and its copy label.

    ``customer_profiles_failover_replica`` -> ``("customer_profiles",
    "failover_replica")``. A table naming no copy is treated as the primary
    store, which is how the inventory snapshots are written.
    """
    name = table.strip().strip('"')
    for label in _FIXED_COPY_LABELS:
        suffix = f"_{label}"
        if name.endswith(suffix):
            return name[: -len(suffix)], label
    base, separator, tail = name.rpartition("_replica")
    if separator and base:
        # ``customer_data_replica_eu_west`` -> base ``customer_data``.
        candidate = f"replica{tail}"
        if _REGIONAL_REPLICA.match(candidate):
            return base, candidate
    return name, DEFAULT_COPY_LABEL
