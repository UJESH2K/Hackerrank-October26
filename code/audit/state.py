"""Reconstruct the true state of each resource at a case's ``audit_at``.

Three rules govern everything here:

* **Only admissible evidence.** A file captured after ``audit_at``, or a record
  observed after it, cannot establish state for that case. Identity linkage is
  exempt (decision D17); state is not.
* **Presence needs positive evidence.** A remaining record is reported only when
  something was actually observed. Absence comes either from an explicit negative
  (``404``, ``rows=0``, ``NOT_FOUND``, ``result=miss``, ``available=false``,
  ``profile: null``) or from a coverage fact. A resource with no admissible
  observation at all gets no finding - only its verification step.
* **Activity rows are claims.** A ``SUCCESS`` never proves absence. What activity
  rows *can* show is that data came back, which is surfaced through the flow,
  backup or replica that did the recreating.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from audit.evidence.fields import is_nullish
from audit.evidence.index import EvidenceIndex
from audit.loaders import Dataset
from audit.models import Activity, CoverageFact, Observation

#: Attributes whose presence on a record means the person is still identifiable.
#: A tombstone's stable internal reference is deliberately excluded here and
#: handled by the policy layer, which knows that keeping it is permitted.
DIRECT_IDENTIFIER_FIELDS: tuple[str, ...] = (
    "email",
    "customer_email",
    "owner_email",
    "requester_email",
    "uploaded_by",
    "phone",
    "customer_phone",
    "owner_phone",
    "requester_phone",
    "email_hash",
    "name",
    "full_name",
    "display_name",
    "requester_name",
)

#: Attributes that reference the person without naming them directly.
SUBJECT_REFERENCE_FIELDS: tuple[str, ...] = (
    "user_id",
    "customer_reference",
    "subject_reference",
    "subject_ref",
)

#: Status labels that say identifiers have been removed.
CLEARED_LABELS: frozenset[str] = frozenset({"anonymized", "anonymised", "redacted"})

#: Status labels that say identifiers are still there.
EXPOSED_LABELS: frozenset[str] = frozenset({"identifiable", "exposed"})

#: Attributes that answer "is this data flow running?".
FLOW_ENABLED_FIELDS: tuple[str, ...] = ("enabled", "scheduler_registered")

#: Status labels for a data flow.
FLOW_ACTIVE_LABELS: frozenset[str] = frozenset({"active", "enabled"})
FLOW_INACTIVE_LABELS: frozenset[str] = frozenset({"disabled", "inactive", "stopped"})

#: Status labels for a backup archive.
RESTORABLE_LABELS: frozenset[str] = frozenset({"active", "restorable"})
BLOCKED_LABELS: frozenset[str] = frozenset({"blocked"})

#: Truthy and falsy spellings used across the formats.
TRUE_VALUES: frozenset[str] = frozenset({"true", "1", "yes", "allowed"})
FALSE_VALUES: frozenset[str] = frozenset({"false", "0", "no", "denied"})

#: Activity details stating that data was put back after deletion.
RECREATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Active connector repopulated downstream resource (?P<resource_id>\S+?)\."),
    re.compile(r"Restore job repopulated downstream resource (?P<resource_id>\S+?)\."),
)

#: Event types that copy a record from one store to another.
REPLICATION_EVENT_TYPES: frozenset[str] = frozenset({"SYNC_RECORD", "RESTORE_BACKUP"})


def _truthy(value: str) -> bool | None:
    """Interpret a flag written as ``true``/``1``/``yes`` or its negation."""
    text = value.strip().casefold()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return None


@dataclass(frozen=True, slots=True)
class Recreation:
    """Evidence that a resource's data can come back after deletion."""

    resource_id: str
    activity_id: str
    at: datetime
    source_system: str
    target_system: str
    detail: str


@dataclass(frozen=True)
class ResourceState:
    """What is known about one resource at one case's ``audit_at``.

    Attributes:
        resource_id: the resource.
        system_id: its owning system.
        present: whether the record exists. ``None`` when no admissible evidence
            speaks to it, which is different from "absent".
        identifiers_exposed: whether direct identifiers are still readable.
        subject_reference_only: whether the only remaining link is the stable
            internal reference, which a tombstone is allowed to keep.
        flow_enabled: for a data flow, whether it is running.
        restorable: for a backup archive, whether it can still be restored.
        observed_at: the time the state was observed.
        proof: the observation establishing presence, if there is one.
        coverage: the coverage fact establishing absence, if that is the proof.
        copies: the latest observation per copy label.
        recreation: activity rows showing data was put back.
        attributes: the merged attributes of the deciding observations.
    """

    resource_id: str
    system_id: str
    present: bool | None = None
    identifiers_exposed: bool | None = None
    subject_reference_only: bool = False
    flow_enabled: bool | None = None
    restorable: bool | None = None
    observed_at: datetime | None = None
    proof: Observation | None = None
    coverage: CoverageFact | None = None
    copies: tuple[Observation, ...] = ()
    recreation: tuple[Recreation, ...] = ()
    attributes: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Default the attribute map without making the dataclass mutable."""
        if self.attributes is None:
            object.__setattr__(self, "attributes", {})

    @property
    def evidence_id(self) -> str:
        """The participant-visible id that proves this state."""
        if self.proof is not None:
            return self.proof.evidence_id
        if self.coverage is not None:
            return self.coverage.evidence_id
        return ""

    @property
    def drifted(self) -> bool:
        """Whether two copies disagree about the record existing."""
        states = {copy.present for copy in self.copies if copy.present is not None}
        return len(states) > 1


class StateResolver:
    """Resolves resource state as of a case's ``audit_at``."""

    def __init__(self, dataset: Dataset, index: EvidenceIndex) -> None:
        """Index the activity stream for recreation lookups."""
        self.dataset = dataset
        self.index = index
        self.recreation_by_resource: dict[str, tuple[Recreation, ...]] = (
            _index_recreation(dataset)
        )

    def resolve(
        self,
        resource_id: str,
        system_id: str,
        audit_at: datetime,
        case_evidence_id: str = "",
    ) -> ResourceState:
        """Reconstruct one resource's state.

        Args:
            resource_id: the resource to resolve.
            system_id: its owning system.
            audit_at: the cutoff. Nothing later may influence the answer.
            case_evidence_id: the case's evidence file. When the resource appears
                in it, that file is the source of truth, which is how a generated
                case is meant to be read. Otherwise the latest admissible
                observation in any file is used, which is how a hand-built case
                with per-resource citations works.
        """
        admissible = self.index.state_for_resource(resource_id, audit_at)
        chosen = tuple(
            observation
            for observation in admissible
            if observation.evidence_id == case_evidence_id
        )
        if not chosen:
            chosen = admissible

        recreation = tuple(
            event
            for event in self.recreation_by_resource.get(resource_id, ())
            if event.at <= audit_at
        )

        if not chosen:
            coverage = self.index.absent_by_coverage(resource_id, system_id, audit_at)
            latest_coverage = (
                max(coverage, key=lambda fact: (fact.observed_at, fact.evidence_id))
                if coverage
                else None
            )
            return ResourceState(
                resource_id=resource_id,
                system_id=system_id,
                present=False if latest_coverage is not None else None,
                observed_at=latest_coverage.observed_at if latest_coverage else None,
                coverage=latest_coverage,
                recreation=recreation,
            )

        latest_time = max(observation.observed_at for observation in chosen)
        at_latest = tuple(
            observation for observation in chosen if observation.observed_at == latest_time
        )
        copies = _latest_per_copy(chosen)

        # Replication drift: a record still served by any copy is still present.
        present: bool | None = None
        proof: Observation | None = None
        for observation in at_latest:
            if observation.present is True:
                present = True
                proof = observation
                break
        if present is None:
            if any(observation.present is False for observation in at_latest):
                present = False
            proof = at_latest[0]

        merged = _merge_attributes(chosen)
        return ResourceState(
            resource_id=resource_id,
            system_id=system_id,
            present=present,
            identifiers_exposed=_identifiers_exposed(chosen),
            subject_reference_only=_subject_reference_only(chosen),
            flow_enabled=self._flow_enabled(chosen, recreation),
            restorable=self._restorable(chosen, recreation),
            observed_at=latest_time,
            proof=proof,
            copies=copies,
            recreation=recreation,
            attributes=merged,
        )

    def _flow_enabled(
        self, observations: tuple[Observation, ...], recreation: tuple[Recreation, ...]
    ) -> bool | None:
        """Whether a data flow is running.

        Evidence decides. Only when evidence is silent does a repopulation
        activity count, and then only to say the flow is active - never to say it
        has stopped.
        """
        decided = _flow_enabled(observations)
        if decided is not None:
            return decided
        return True if recreation else None

    def _restorable(
        self, observations: tuple[Observation, ...], recreation: tuple[Recreation, ...]
    ) -> bool | None:
        """Whether a backup can still restore the person's data."""
        decided = _restorable(observations)
        if decided is not None:
            return decided
        return True if recreation else None

    def recreation_of(
        self, resource_ids: frozenset[str], audit_at: datetime
    ) -> tuple[Recreation, ...]:
        """Recreation events affecting any of these resources, before the cutoff."""
        found: list[Recreation] = []
        for resource_id in sorted(resource_ids):
            found.extend(
                event
                for event in self.recreation_by_resource.get(resource_id, ())
                if event.at <= audit_at
            )
        return tuple(found)


def _latest_per_copy(observations: tuple[Observation, ...]) -> tuple[Observation, ...]:
    """The newest observation of each copy label."""
    latest: dict[str, Observation] = {}
    for observation in observations:
        current = latest.get(observation.copy_label)
        if current is None or (observation.observed_at, observation.evidence_id) > (
            current.observed_at,
            current.evidence_id,
        ):
            latest[observation.copy_label] = observation
    return tuple(latest[key] for key in sorted(latest))


def _newest_first(observations: tuple[Observation, ...]) -> tuple[Observation, ...]:
    """Observations ordered newest first, deterministically."""
    return tuple(
        sorted(
            observations,
            key=lambda item: (item.observed_at, item.evidence_id),
            reverse=True,
        )
    )


def _merge_attributes(observations: tuple[Observation, ...]) -> dict[str, str]:
    """Merge attributes, newest value winning, for category resolution."""
    merged: dict[str, str] = {}
    for observation in reversed(_newest_first(observations)):
        for key, value in observation.attributes.items():
            if not is_nullish(value) or key not in merged:
                merged[key] = value
    return merged


def _status_label(observation: Observation) -> str:
    """The record's status label, lower-cased."""
    return observation.attributes.get("result", "").strip().casefold()


def _identifiers_exposed(observations: tuple[Observation, ...]) -> bool | None:
    """Whether direct identifiers are still readable on the record.

    Checked newest first, so a later redaction overrides an earlier exposure.
    """
    for observation in _newest_first(observations):
        label = _status_label(observation)
        if label in CLEARED_LABELS:
            return False
        if label in EXPOSED_LABELS:
            return True

        scanned = observation.attributes.get("direct_identifiers", "").strip()
        if scanned.isdigit():
            return int(scanned) > 0
        matched = observation.attributes.get("matched_value", "")
        detector = observation.attributes.get("detector", "").casefold()
        if not is_nullish(matched) and detector == "direct_identifier":
            return True

        has_field = False
        for name in DIRECT_IDENTIFIER_FIELDS:
            if name not in observation.attributes:
                continue
            has_field = True
            if not is_nullish(observation.attributes[name]):
                return True
        if has_field:
            return False
    return None


def _subject_reference_only(observations: tuple[Observation, ...]) -> bool:
    """Whether the only remaining link is the stable internal reference."""
    for observation in _newest_first(observations):
        has_reference = any(
            name in observation.attributes
            and not is_nullish(observation.attributes[name])
            for name in SUBJECT_REFERENCE_FIELDS
        )
        if not has_reference:
            continue
        direct = any(
            name in observation.attributes
            and not is_nullish(observation.attributes[name])
            for name in DIRECT_IDENTIFIER_FIELDS
        )
        if not direct:
            return True
    return False


def _flow_enabled(observations: tuple[Observation, ...]) -> bool | None:
    """Whether a connector or sync job is running, from evidence alone."""
    for observation in _newest_first(observations):
        label = _status_label(observation)
        if label in FLOW_INACTIVE_LABELS:
            return False
        if label in FLOW_ACTIVE_LABELS:
            return True
        for name in FLOW_ENABLED_FIELDS:
            decided = _truthy(observation.attributes.get(name, ""))
            if decided is not None:
                return decided
    return None


def _restorable(observations: tuple[Observation, ...]) -> bool | None:
    """Whether a backup archive can still restore the person's data."""
    for observation in _newest_first(observations):
        label = _status_label(observation)
        if label in BLOCKED_LABELS:
            return False
        if label in RESTORABLE_LABELS:
            return True

        decision = observation.attributes.get("decision", "").strip().casefold()
        if decision == "deny":
            return False
        if decision == "allow":
            return True

        allowed = _truthy(observation.attributes.get("restore_allowed", ""))
        if allowed is not None:
            return allowed

        eligible = _truthy(observation.attributes.get("eligible", ""))
        blocked = _truthy(observation.attributes.get("deletion_block", ""))
        if eligible is not None:
            # A restore candidate is restorable when it is eligible and nothing
            # blocks the deletion. This distinction was worth one sample case.
            return eligible and blocked is not True
    return None


def _index_recreation(dataset: Dataset) -> dict[str, tuple[Recreation, ...]]:
    """Index activity rows that state data was put back after deletion."""
    found: dict[str, list[Recreation]] = defaultdict(list)
    for activity in dataset.activities:
        resource_id = _recreated_resource(activity)
        if resource_id is None:
            continue
        found[resource_id].append(
            Recreation(
                resource_id=resource_id,
                activity_id=activity.activity_id,
                at=activity.event_time,
                source_system=activity.source_system,
                target_system=activity.target_system,
                detail=activity.details,
            )
        )
    return {
        key: tuple(sorted(value, key=lambda item: (item.at, item.activity_id)))
        for key, value in found.items()
    }


def _recreated_resource(activity: Activity) -> str | None:
    """The resource an activity row says was repopulated, if any."""
    for pattern in RECREATION_PATTERNS:
        match = pattern.search(activity.details)
        if match is not None:
            return match.group("resource_id")
    if activity.event_type in REPLICATION_EVENT_TYPES and activity.target_system not in {
        "analytics_platform"
    }:
        # A replication or restore event copies the record onward. Analytics
        # synchronisation is the platform's own feed, not a recreation path.
        return activity.resource_id
    return None
