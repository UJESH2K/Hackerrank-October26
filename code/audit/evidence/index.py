"""Queryable index over every parsed observation.

The index enforces the one asymmetry that matters for correctness:

* **State** may only be established from an artefact captured at or before the
  case's ``audit_at``. Evidence from after the cutoff cannot change the answer.
* **Identity linkage** may use any artefact. Which person a resource belongs to
  is not a property that changes over time, so a later capture that names the
  owner of ``account_005`` is still telling the truth about who owned it.

Keeping both rules in one object means no caller has to remember them.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from audit.evidence.fields import normalise_link_key
from audit.loaders import Dataset
from audit.models import CoverageFact, Observation


@dataclass(frozen=True, slots=True)
class EvidenceIndex:
    """Lookups over parsed evidence.

    Attributes:
        observations: every parsed observation.
        coverage: every coverage fact.
        captured_at: ``evidence_id -> capture time``, which decides admissibility.
        by_resource: ``resource_id -> observations``.
        by_link_key: normalised identifier -> observations mentioning it.
        by_evidence: ``evidence_id -> observations``.
    """

    observations: tuple[Observation, ...]
    coverage: tuple[CoverageFact, ...]
    captured_at: dict[str, datetime]
    by_resource: dict[str, tuple[Observation, ...]]
    by_link_key: dict[str, tuple[Observation, ...]]
    by_evidence: dict[str, tuple[Observation, ...]]

    # -- admissibility ----------------------------------------------------- #

    def is_admissible(self, evidence_id: str, at: datetime | None) -> bool:
        """Whether an artefact may establish state as of ``at``.

        An artefact with no manifest capture time is treated as inadmissible for
        state: without a capture time there is nothing to compare to the cutoff.
        """
        if at is None:
            return True
        captured = self.captured_at.get(evidence_id)
        return captured is not None and captured <= at

    def _admissible(
        self, observations: Iterable[Observation], at: datetime | None
    ) -> tuple[Observation, ...]:
        """Filter observations to those admissible for state as of ``at``."""
        return tuple(
            observation
            for observation in observations
            if self.is_admissible(observation.evidence_id, at)
        )

    # -- state lookups ----------------------------------------------------- #

    def state_for_resource(
        self, resource_id: str, at: datetime | None = None
    ) -> tuple[Observation, ...]:
        """Observations of a resource that may establish its state at ``at``.

        Also drops observations whose own ``observed_at`` is after the cutoff: a
        file captured in time can still contain a later record.
        """
        candidates = self._admissible(self.by_resource.get(resource_id, ()), at)
        if at is None:
            return candidates
        return tuple(
            observation for observation in candidates if observation.observed_at <= at
        )

    def latest_state(
        self, resource_id: str, copy_label: str | None = None, at: datetime | None = None
    ) -> Observation | None:
        """The most recent admissible observation of a resource copy.

        Ties on ``observed_at`` are broken by ``evidence_id`` so the result is
        deterministic.
        """
        candidates = [
            observation
            for observation in self.state_for_resource(resource_id, at)
            if copy_label is None or observation.copy_label == copy_label
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.observed_at, item.evidence_id))

    def copies_of(
        self, resource_id: str, at: datetime | None = None
    ) -> dict[str, Observation]:
        """The latest admissible observation per copy label.

        Replication drift is visible directly in the result: two copy labels of
        one resource disagreeing on ``present``.
        """
        latest: dict[str, Observation] = {}
        for observation in self.state_for_resource(resource_id, at):
            current = latest.get(observation.copy_label)
            if current is None or (observation.observed_at, observation.evidence_id) > (
                current.observed_at,
                current.evidence_id,
            ):
                latest[observation.copy_label] = observation
        return latest

    # -- identity lookups -------------------------------------------------- #

    def for_link_key(self, value: str, at: datetime | None = None) -> tuple[Observation, ...]:
        """Observations mentioning an identifier.

        ``at`` is optional and defaults to no cutoff, because identity linkage is
        time-independent. Pass it only when the caller needs state as well.
        """
        key = normalise_link_key(value)
        found = self.by_link_key.get(key, ())
        return found if at is None else self._admissible(found, at)

    def resources_for_link_keys(self, values: Iterable[str]) -> dict[str, set[str]]:
        """Map each identifier to the resource ids it appears with.

        No time filter: this answers "which resources belong to this person",
        which later evidence may legitimately reveal.
        """
        found: dict[str, set[str]] = {}
        for value in values:
            key = normalise_link_key(value)
            if not key:
                continue
            resources = {
                observation.resource_id for observation in self.by_link_key.get(key, ())
            }
            if resources:
                found[key] = resources
        return found

    # -- coverage ---------------------------------------------------------- #

    def coverage_for(
        self,
        system_id: str,
        at: datetime | None = None,
        copy_label: str | None = None,
    ) -> tuple[CoverageFact, ...]:
        """Coverage facts for a system, admissible as of ``at``."""
        return tuple(
            fact
            for fact in self.coverage
            if fact.system_id == system_id
            and (copy_label is None or fact.copy_label == copy_label)
            and self.is_admissible(fact.evidence_id, at)
            and (at is None or fact.observed_at <= at)
        )

    def was_enumerated(
        self, system_id: str, at: datetime | None = None, copy_label: str | None = None
    ) -> bool:
        """Whether any admissible snapshot enumerated this system copy."""
        return bool(self.coverage_for(system_id, at, copy_label))

    def absent_by_coverage(
        self, resource_id: str, system_id: str, at: datetime | None = None
    ) -> tuple[CoverageFact, ...]:
        """Coverage facts that prove a resource was absent.

        A fact qualifies when it enumerated the resource's system and no
        observation from that same artefact and copy mentions the resource.
        """
        seen = {
            (observation.evidence_id, observation.copy_label)
            for observation in self.state_for_resource(resource_id, at)
        }
        return tuple(
            fact
            for fact in self.coverage_for(system_id, at)
            if (fact.evidence_id, fact.copy_label) not in seen
        )


def build_index(
    dataset: Dataset,
    observations: Sequence[Observation],
    coverage: Sequence[CoverageFact] = (),
) -> EvidenceIndex:
    """Build the index, taking capture times from the manifest."""
    by_resource: dict[str, list[Observation]] = defaultdict(list)
    by_link_key: dict[str, list[Observation]] = defaultdict(list)
    by_evidence: dict[str, list[Observation]] = defaultdict(list)
    for observation in observations:
        by_resource[observation.resource_id].append(observation)
        by_evidence[observation.evidence_id].append(observation)
        for key in observation.link_keys:
            by_link_key[key].append(observation)
    return EvidenceIndex(
        observations=tuple(observations),
        coverage=tuple(coverage),
        captured_at={
            evidence.file_id: evidence.captured_at for evidence in dataset.evidence_files
        },
        by_resource={key: tuple(value) for key, value in by_resource.items()},
        by_link_key={key: tuple(value) for key, value in by_link_key.items()},
        by_evidence={key: tuple(value) for key, value in by_evidence.items()},
    )
