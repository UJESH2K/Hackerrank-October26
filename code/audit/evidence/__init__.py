"""Evidence ingestion.

Every artefact under ``dataset/evidence/`` becomes
:class:`audit.models.Observation` records.

One parser per format, all behind :func:`parse_evidence_file`, which dispatches
on file extension. :func:`build_index` then makes the result queryable by
resource, by identifier and by coverage, enforcing that state may only come from
evidence captured at or before a case's ``audit_at``.
"""

from audit.evidence.context import ParseContext, ParseResult
from audit.evidence.index import EvidenceIndex, build_index
from audit.evidence.registry import PARSERS, parse_all, parse_evidence_file
from audit.evidence.systems import RESOURCE_PREFIX_SYSTEM, system_for_resource

__all__ = [
    "PARSERS",
    "RESOURCE_PREFIX_SYSTEM",
    "EvidenceIndex",
    "ParseContext",
    "ParseResult",
    "build_index",
    "parse_all",
    "parse_evidence_file",
    "system_for_resource",
]
