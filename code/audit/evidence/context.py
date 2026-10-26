"""The contract every evidence parser implements.

A parser takes a :class:`ParseContext` - one row of ``evidence_files.csv`` plus
the resolved path - and returns a :class:`ParseResult`. Keeping the signature
uniform is what lets the registry dispatch on file extension alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from audit.models import CoverageFact, Observation


@dataclass(frozen=True, slots=True)
class ParseContext:
    """One evidence artefact to parse.

    Attributes:
        path: the resolved path on disk.
        evidence_id: the manifest ``file_id``. Any step grounded in this file
            cites this value, so it travels with every observation.
        captured_at: when the artefact was captured. Used as the least specific
            fallback time, and used by the index to decide admissibility: a file
            captured after a case's ``audit_at`` cannot establish state for it.
        origin: the manifest ``origin`` field, kept for diagnostics.
    """

    path: Path
    evidence_id: str
    captured_at: datetime
    origin: str = ""


@dataclass(slots=True)
class ParseResult:
    """What one parser extracted from one artefact.

    Attributes:
        observations: facts about specific resources.
        coverage: statements that a system copy was fully enumerated at a time,
            which is what makes a resource's absence provable.
        warnings: anything the parser could not read. A non-empty list on a text
            format is a defect, not an acceptable outcome.
    """

    observations: list[Observation] = field(default_factory=list)
    coverage: list[CoverageFact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def extend(self, other: ParseResult) -> None:
        """Absorb another result."""
        self.observations.extend(other.observations)
        self.coverage.extend(other.coverage)
        self.warnings.extend(other.warnings)
