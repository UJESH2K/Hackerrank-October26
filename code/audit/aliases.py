"""Resolve the alternative names a resource is known by.

The data catalog declares aliases outright. Every ``LINK_RESOURCE_IDENTITY`` row
and the attachment-catalog row name a resource and, in parentheses, the reference
the business actually uses for it::

    Data catalog linked export_1842 (NST-2026-0718-1842) to ... and user_18.
    Data catalog linked tombstone_17 (user_17) to ... and user_17.
    Attachment catalog confirmed object_19 (delivery_confirmation_19.pdf) ...

This matters for document evidence. A screenshot or an invoice shows the
business reference, not the internal resource id: an object-storage browser
lists ``delivery_confirmation_19.pdf``, and a deleted-account screen shows
``user_17``. Post-validation would drop both, because neither is a resource id.
With the declared aliases it can translate them first.

The parsing is generic - any ``<resource_id> (<alias>)`` pair in a catalog row -
so no alias value is written into the code.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from audit.loaders import Dataset

#: ``<resource_id> (<alias>)`` as the catalog rows write it.
ALIAS_DECLARATION = re.compile(
    r"(?P<resource_id>[a-z][a-z_]*_\d+)\s*\((?P<alias>[^)]+)\)"
)

#: Rows that declare an alias say so in these words.
CATALOG_MARKERS: tuple[str, ...] = ("catalog",)


def normalise_alias(value: str) -> str:
    """The form an alias is matched on: trimmed and case-folded."""
    return " ".join(str(value).split()).casefold()


def build_alias_map(dataset: Dataset) -> dict[str, str]:
    """Build ``alias -> resource_id`` from the catalog activity rows.

    A declaration whose alias is the resource id itself is skipped: it carries no
    information and would only make the map look bigger than it is.
    """
    # Imported here rather than at module level: the evidence package imports
    # this module, so a top-level import would be circular.
    from audit.evidence.systems import RESOURCE_PREFIX_SYSTEM, resource_prefix

    aliases: dict[str, str] = {}
    for activity in dataset.activities:
        details = activity.details
        if not any(marker in details.casefold() for marker in CATALOG_MARKERS):
            continue
        for match in ALIAS_DECLARATION.finditer(details):
            resource_id = match.group("resource_id")
            if resource_prefix(resource_id) not in RESOURCE_PREFIX_SYSTEM:
                continue
            alias = normalise_alias(match.group("alias"))
            if not alias or alias == resource_id.casefold():
                continue
            aliases.setdefault(alias, resource_id)
    return aliases


def resolve_alias(value: str, aliases: Mapping[str, str]) -> str | None:
    """The resource id a reference is an alias for, or ``None``.

    Returns ``None`` for an unknown reference, which is what keeps an order
    number without a declared alias out of the pipeline.
    """
    return aliases.get(normalise_alias(value))
