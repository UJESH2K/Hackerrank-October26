"""Decide which resources belong to a request's subject, and why.

Attribution runs in four layers, strongest evidence first:

1. **evidence_link** - an observation's identifier matches the subject's
   identifier set. Exact matching only.
2. **catalog_link** - a ``LINK_RESOURCE_IDENTITY`` activity row names the
   resource, an email and a ``user_id`` together.
3. **workflow_run** - about half the accounts have been deleted, so their
   evidence carries no identifier at all. Those are linked through the deletion
   worker's own run in ``system_activity.csv``, scored on explainable features
   and assigned jointly across cases so two cases cannot claim one account.
4. **orphan_timing** - a record that is already anonymised has no identifier by
   construction. It is attributed by the timing signature of the worker events
   that touched it.

Every layer is scoped to a **deletion episode**: the same person can file two
requests months apart, and a link observed during one must not leak into the
other.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from audit.evidence.index import EvidenceIndex
from audit.identity import IdentityResolver, Subject
from audit.loaders import Dataset
from audit.models import Activity, Request

# --------------------------------------------------------------------------- #
# Tunable windows, all stated once
# --------------------------------------------------------------------------- #

#: How far before ``requested_at`` and after ``audit_at`` a linking observation
#: may sit and still belong to this deletion episode. The upper slack matters:
#: sample case_23's security audit record is only linked in a bundle captured two
#: days after ``audit_at``, and the gold answer still verifies it.
EPISODE_SLACK = timedelta(days=7)

#: How long after the workflow reported its status a deletion event may still
#: belong to that run.
RUN_TAIL = timedelta(minutes=30)

#: An account deletion this close to ``reported_at`` is the reported deletion.
REPORT_PROXIMITY = timedelta(minutes=15)

#: How long after ``reported_at`` the run's follow-up worker events may appear.
FOLLOW_UP_TAIL = timedelta(hours=4)

#: The period the timing signature is measured against.
SIGNATURE_PERIOD_SECONDS = 60

#: How wide a learned band is around a dense bin, in seconds. The observed bands
#: are three seconds wide: a base offset plus about a second of jitter.
BAND_RADIUS_SECONDS = 1

#: Slack added to the largest learned follow-up delay, so a run slightly longer
#: than anything in the learning set is not rejected outright.
OFFSET_HEADROOM = 0.1

#: Bucket width used when reporting learned follow-up delays.
OFFSET_BUCKET_SECONDS = 600

# --------------------------------------------------------------------------- #
# Feature weights for the workflow-run score
# --------------------------------------------------------------------------- #

#: The deletion event's result agrees with the status the workflow reported.
WEIGHT_RESULT_MATCH = 2.0

#: The account also appears in the case's evidence file.
WEIGHT_IN_CASE_EVIDENCE = 2.0

#: The deletion happened essentially at reporting time.
WEIGHT_NEAR_REPORT = 3.0

#: Scale applied to the timing-signature agreement count.
WEIGHT_SIGNATURE = 0.5

#: ``reported_status`` -> the ``reported_result`` a matching deletion event has.
REPORTED_RESULT_FOR_STATUS: dict[str, str] = {
    "COMPLETE": "SUCCESS",
    "PARTIALLY_COMPLETE": "PARTIAL",
    "NOT_STARTED": "FAILED",
}

#: Worker event types that form a deletion run's follow-up signature.
FOLLOW_UP_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "ANONYMIZE_RECORD",
        "REDACT_RECORD",
        "DELETE_RECORD",
        "RETAIN_RECORD",
        "CHECK_DATA_FLOW",
        "CHECK_RESTORE_ACCESS",
        "DISABLE_DATA_FLOW",
        "BLOCK_RESTORE",
    }
)

#: The system that performs deletions.
DELETION_WORKER = "deletion_worker"

#: The activity event type that states an explicit resource-to-person link.
LINK_EVENT_TYPE = "LINK_RESOURCE_IDENTITY"

# --------------------------------------------------------------------------- #
# Catalog link patterns, named and kept together
# --------------------------------------------------------------------------- #

#: ``Data catalog linked account_12 (account_12) to a.b@example.com and user_7.``
DATA_CATALOG_LINK = re.compile(
    r"Data catalog linked (?P<resource_id>\S+) \((?P<reference>[^)]*)\) to "
    r"(?P<email>[^\s]+) and (?P<user_id>user_\d+)"
)

#: ``Attachment catalog confirmed object_3 (x.pdf) belongs only to a@b.com and
#: user_7; no other subject is linked.``
ATTACHMENT_CATALOG_LINK = re.compile(
    r"Attachment catalog confirmed (?P<resource_id>\S+) \((?P<file>[^)]*)\) "
    r"belongs only to (?P<email>[^\s]+) and (?P<user_id>user_\d+)"
)

#: Both catalog patterns, tried in order.
CATALOG_PATTERNS: tuple[re.Pattern[str], ...] = (
    DATA_CATALOG_LINK,
    ATTACHMENT_CATALOG_LINK,
)


class LinkKind(StrEnum):
    """Why a resource was attributed to the subject."""

    EVIDENCE_LINK = "evidence_link"
    CATALOG_LINK = "catalog_link"
    WORKFLOW_RUN = "workflow_run"
    ORPHAN_TIMING = "orphan_timing"


@dataclass(frozen=True, slots=True)
class OwnerLink:
    """One observed link between a resource and a person.

    Attributes:
        user_id: the owner.
        resource_id: the linked resource.
        system_id: the resource's owning system.
        at: when the link was observed. Episode scoping uses this, not the
            evidence file's capture time.
        kind: which layer produced the link.
        supporting_id: the ``file_id``, ``activity_id`` or ``audit_id`` behind it.
    """

    user_id: str
    resource_id: str
    system_id: str
    at: datetime
    kind: LinkKind
    supporting_id: str


@dataclass(frozen=True, slots=True)
class AttributedResource:
    """A resource attributed to a case, with the reason and the proof."""

    system_id: str
    resource_id: str
    how: LinkKind
    confidence: float
    supporting_ids: tuple[str, ...] = ()

    @property
    def sort_key(self) -> tuple[str, str]:
        """Deterministic ordering, matching the step sort."""
        return (self.system_id, self.resource_id)


@dataclass(frozen=True, slots=True)
class CaseAttribution:
    """Everything attribution knows about one case."""

    case_id: str
    subject: Subject | None
    evidence_id: str
    resources: tuple[AttributedResource, ...] = ()
    account_resource_id: str = ""
    account_deleted_at: datetime | None = None
    notes: tuple[str, ...] = ()

    def resource_ids(self) -> frozenset[str]:
        """The attributed resource ids."""
        return frozenset(resource.resource_id for resource in self.resources)


@dataclass(frozen=True, slots=True)
class _AccountCandidate:
    """One possible account-deletion event for a case."""

    case_id: str
    resource_id: str
    activity_id: str
    at: datetime
    score: float
    features: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TimingSignature:
    """The learned shape of a deletion run's follow-up events.

    Two things are learned, both without labels:

    * **bands** - the seconds-within-the-minute at which follow-up events sit,
      relative to the account deletion. A run's steps are scheduled at a fixed
      cadence, so this is sharply clustered.
    * **max_offset_seconds** - how long after the account deletion a follow-up
      event can still appear. This is what rules out a candidate whose follow-up
      events would have to have happened a day later.

    Attributes:
        bands: accepted ``(event time - account deletion time) mod 60`` values.
        max_offset_seconds: the largest plausible follow-up delay.
        histogram: the full learned band histogram, for the decision log.
        offset_histogram: learned delays bucketed into ten-minute bins.
        sample_size: how many anchor events were learned from.
    """

    bands: frozenset[int]
    max_offset_seconds: float = 0.0
    histogram: Counter[int] = field(default_factory=Counter)
    offset_histogram: Counter[int] = field(default_factory=Counter)
    sample_size: int = 0

    def agrees(self, offset_seconds: float) -> bool:
        """Whether an offset matches the learned run shape.

        A follow-up event must sit *after* the account deletion, within the
        learned maximum delay, and on one of the learned cadence bands.
        """
        if offset_seconds < 0 or offset_seconds > self.max_offset_seconds:
            return False
        return int(offset_seconds) % SIGNATURE_PERIOD_SECONDS in self.bands


class Attributor:
    """Attributes resources to cases across all four layers.

    Account assignment is joint across cases, so the constructor takes the whole
    request set rather than one request at a time.
    """

    def __init__(
        self,
        dataset: Dataset,
        index: EvidenceIndex,
        resolver: IdentityResolver,
        learning_requests: tuple[Request, ...] | None = None,
    ) -> None:
        """Index the inputs and learn the timing signature.

        Args:
            dataset: the loaded dataset.
            index: the parsed evidence index.
            resolver: the identity resolver.
            learning_requests: the requests used to learn the timing signature.
                Defaults to ``dataset.requests``. The signature is learned
                without any labels, so a caller auditing a subset should pass the
                full request set to keep the learned bands stable.
        """
        self.dataset = dataset
        self.index = index
        self.resolver = resolver
        self.activities_by_resource: dict[str, tuple[Activity, ...]] = _group_activities(
            dataset
        )
        self.owner_links: dict[str, tuple[OwnerLink, ...]] = _build_owner_links(
            dataset, index, resolver
        )
        self.links_by_user: dict[str, tuple[OwnerLink, ...]] = _invert(self.owner_links)
        self.signature = self._learn_signature(
            learning_requests if learning_requests is not None else dataset.requests
        )
        self._attributions: dict[str, CaseAttribution] = {}

    # -- layer 1 and 2: linked resources ----------------------------------- #

    def episode_window(self, request: Request) -> tuple[datetime, datetime]:
        """The time window a link must fall inside to belong to this episode."""
        return (request.requested_at - EPISODE_SLACK, request.audit_at + EPISODE_SLACK)

    def linked_resources(
        self, subject: Subject, request: Request
    ) -> dict[str, list[OwnerLink]]:
        """Resources linked to the subject by evidence or catalog, in episode.

        A link whose observation falls outside the episode window is dropped: it
        belongs to a different deletion request by the same person.
        """
        start, end = self.episode_window(request)
        found: dict[str, list[OwnerLink]] = defaultdict(list)
        for link in self.links_by_user.get(subject.user_id, ()):
            if start <= link.at <= end:
                found[link.resource_id].append(link)
        return dict(found)

    def owners(self, resource_id: str) -> tuple[OwnerLink, ...]:
        """Every observed link for a resource, with its time."""
        return self.owner_links.get(resource_id, ())

    def is_owned_by_other(self, resource_id: str, user_id: str) -> bool:
        """Whether the resource is linked to a different person.

        A resource that belongs to someone else is never attributed here, however
        well it scores on timing.
        """
        return any(link.user_id != user_id for link in self.owners(resource_id))

    # -- layer 3: the case evidence file ----------------------------------- #

    def select_case_evidence(self, subject: Subject, request: Request) -> str:
        """The evidence file a generated case cites for every step.

        The most recent file captured at or before ``audit_at`` that holds at
        least one in-episode record linked to the subject. Ties break on
        ``file_id`` so the choice is deterministic.
        """
        linked = self.linked_resources(subject, request)
        if not linked:
            return ""
        best: tuple[datetime, str] | None = None
        for links in linked.values():
            for link in links:
                if link.kind is not LinkKind.EVIDENCE_LINK:
                    continue
                captured = self.index.captured_at.get(link.supporting_id)
                if captured is None or captured > request.audit_at:
                    continue
                marker = (captured, link.supporting_id)
                if best is None or marker > best:
                    best = marker
        return best[1] if best is not None else ""

    def resources_in_evidence(self, evidence_id: str) -> frozenset[str]:
        """Resource ids appearing in one evidence file."""
        return frozenset(
            observation.resource_id
            for observation in self.index.by_evidence.get(evidence_id, ())
        )

    # -- the timing signature ---------------------------------------------- #

    def _run_anchor_events(
        self, request: Request, resource_ids: frozenset[str]
    ) -> tuple[Activity, ...]:
        """Worker events on the case's other resources, inside the run window."""
        start = request.requested_at
        end = request.reported_at + FOLLOW_UP_TAIL
        anchors: list[Activity] = []
        for resource_id in resource_ids:
            for activity in self.activities_by_resource.get(resource_id, ()):
                if (
                    activity.source_system == DELETION_WORKER
                    and activity.event_type in FOLLOW_UP_EVENT_TYPES
                    and start <= activity.event_time <= end
                ):
                    anchors.append(activity)
        anchors.sort(key=lambda item: (item.event_time, item.activity_id))
        return tuple(anchors)

    def _account_deletion_events(self, request: Request) -> tuple[Activity, ...]:
        """Every account deletion the worker ran inside this case's run window."""
        start = request.requested_at
        end = request.reported_at + RUN_TAIL
        return tuple(
            activity
            for activity in self.dataset.activities
            if activity.event_type == "DELETE_RECORD"
            and activity.source_system == DELETION_WORKER
            and activity.target_system == "account_service"
            and start <= activity.event_time <= end
        )

    def _learn_signature(self, requests: tuple[Request, ...]) -> TimingSignature:
        """Learn the follow-up offset bands, without using any labels.

        Only cases whose account is already linked by evidence contribute: for
        those the account deletion time is known, so the offsets are measured
        against the truth. Bands are the bins whose density clearly exceeds
        uniform, widened by one second either side because the observed bands are
        three seconds wide.
        """
        histogram: Counter[int] = Counter()
        offsets: list[int] = []
        for request in requests:
            subject = self.resolver.resolve(request.email)
            if subject is None:
                continue
            linked = self.linked_resources(subject, request)
            accounts = [
                resource_id
                for resource_id in linked
                if resource_id.startswith("account_")
            ]
            if len(accounts) != 1:
                continue
            anchor_time = self._deletion_time_of(accounts[0], request)
            if anchor_time is None:
                continue
            others = frozenset(set(linked) - {accounts[0]})
            for activity in self._run_anchor_events(request, others):
                offset = int((activity.event_time - anchor_time).total_seconds())
                if offset < 0:
                    continue
                histogram[offset % SIGNATURE_PERIOD_SECONDS] += 1
                offsets.append(offset)

        total = sum(histogram.values())
        if total == 0:
            return TimingSignature(bands=frozenset(), histogram=histogram)
        uniform = total / SIGNATURE_PERIOD_SECONDS
        dense = {
            offset for offset, count in histogram.items() if count >= max(2.0, uniform)
        }
        bands = {
            (offset + shift) % SIGNATURE_PERIOD_SECONDS
            for offset in dense
            for shift in range(-BAND_RADIUS_SECONDS, BAND_RADIUS_SECONDS + 1)
        }
        return TimingSignature(
            bands=frozenset(bands),
            max_offset_seconds=max(offsets) * (1.0 + OFFSET_HEADROOM),
            histogram=histogram,
            offset_histogram=Counter(
                offset // OFFSET_BUCKET_SECONDS for offset in offsets
            ),
            sample_size=total,
        )

    def _deletion_time_of(self, resource_id: str, request: Request) -> datetime | None:
        """When the worker deleted this account inside the case's run window."""
        start = request.requested_at
        end = request.reported_at + RUN_TAIL
        times = [
            activity.event_time
            for activity in self.activities_by_resource.get(resource_id, ())
            if activity.event_type == "DELETE_RECORD"
            and activity.source_system == DELETION_WORKER
            and activity.target_system == "account_service"
            and start <= activity.event_time <= end
        ]
        return max(times) if times else None

    def run_cadence(
        self,
        request: Request,
        account_time: datetime,
        resource_ids: frozenset[str],
    ) -> frozenset[int]:
        """The offsets this particular run's own follow-up events sit at.

        The global bands say which offsets are plausible for *any* run. This says
        which ones *this* run actually used, measured from its own already-linked
        resources. It is what separates two runs whose offsets both look
        plausible: a deletion run schedules its steps at one cadence, so an event
        off that cadence is not part of it.

        Empty when the case has no other linked resource to measure, in which
        case it carries no information and must not be used to exclude anything.
        """
        offsets: set[int] = set()
        for activity in self._run_anchor_events(request, resource_ids):
            offset = (activity.event_time - account_time).total_seconds()
            if 0 <= offset <= self.signature.max_offset_seconds:
                offsets.add(int(offset) % SIGNATURE_PERIOD_SECONDS)
        return frozenset(offsets)

    @staticmethod
    def matches_cadence(offset_seconds: float, cadence: frozenset[int]) -> bool:
        """Whether an offset sits on a run's own cadence, within band jitter."""
        if not cadence:
            return True
        position = int(offset_seconds) % SIGNATURE_PERIOD_SECONDS
        return any(
            (position - value) % SIGNATURE_PERIOD_SECONDS
            in {shift % SIGNATURE_PERIOD_SECONDS for shift in range(
                -BAND_RADIUS_SECONDS, BAND_RADIUS_SECONDS + 1
            )}
            for value in cadence
        )

    def signature_score(
        self, request: Request, candidate_time: datetime, resource_ids: frozenset[str]
    ) -> tuple[float, int, int]:
        """Score how well a candidate time explains the run's follow-up events.

        Returns the weighted score, the number of agreeing anchors and the total
        number of anchors, so the features can be explained.
        """
        anchors = self._run_anchor_events(request, resource_ids)
        if not anchors or not self.signature.bands:
            return (0.0, 0, len(anchors))
        agreeing = sum(
            1
            for activity in anchors
            if self.signature.agrees(
                (activity.event_time - candidate_time).total_seconds()
            )
        )
        disagreeing = len(anchors) - agreeing
        return ((agreeing - disagreeing) * WEIGHT_SIGNATURE, agreeing, len(anchors))


    # -- layer 3: the deleted primary account ------------------------------ #

    def account_candidates(
        self, request: Request, subject: Subject
    ) -> tuple[_AccountCandidate, ...]:
        """Score every account the worker could have deleted for this case.

        Features, all explainable:

        * the event's ``reported_result`` agrees with the status the workflow
          reported for this case;
        * the account also appears in the case's evidence file;
        * the deletion happened within fifteen minutes of ``reported_at``;
        * the run's follow-up events sit at the learned offsets from this
          candidate's time.
        """
        evidence_id = self.select_case_evidence(subject, request)
        in_evidence = self.resources_in_evidence(evidence_id) if evidence_id else frozenset()
        linked = frozenset(self.linked_resources(subject, request))
        expected_result = REPORTED_RESULT_FOR_STATUS.get(str(request.reported_status), "")

        candidates: list[_AccountCandidate] = []
        for activity in self._account_deletion_events(request):
            resource_id = activity.resource_id
            if self.is_owned_by_other(resource_id, subject.user_id):
                continue
            features: list[str] = []
            score = 0.0
            if expected_result and activity.reported_result == expected_result:
                score += WEIGHT_RESULT_MATCH
                features.append(f"result={activity.reported_result} matches reported status")
            if resource_id in in_evidence:
                score += WEIGHT_IN_CASE_EVIDENCE
                features.append(f"present in case evidence {evidence_id}")
            if abs(activity.event_time - request.reported_at) <= REPORT_PROXIMITY:
                score += WEIGHT_NEAR_REPORT
                features.append("deleted within 15 min of reported_at")
            signature, agreeing, total = self.signature_score(
                request, activity.event_time, linked
            )
            if total:
                score += signature
                features.append(f"timing signature {agreeing}/{total} anchors agree")
            candidates.append(
                _AccountCandidate(
                    case_id=request.case_id,
                    resource_id=resource_id,
                    activity_id=activity.activity_id,
                    at=activity.event_time,
                    score=score,
                    features=tuple(features),
                )
            )
        candidates.sort(key=lambda item: (-item.score, item.at, item.resource_id))
        return tuple(candidates)

    def assign_accounts(
        self, requests: tuple[Request, ...], subjects: dict[str, Subject]
    ) -> tuple[dict[str, _AccountCandidate], dict[str, str]]:
        """Assign one account per case, jointly across all cases.

        An assignment is accepted only when its score is strictly higher than
        that case's other remaining candidates *and* strictly higher than any
        other unassigned case's score for the same account. Accepting one
        removes it and the loop repeats, so an unambiguous assignment can
        disambiguate a neighbour. Remaining ties are left unassigned and
        reported.

        Returns the accepted assignments and, for each unassigned case, why.
        """
        pools: dict[str, dict[str, _AccountCandidate]] = {}
        for request in requests:
            subject = subjects.get(request.case_id)
            if subject is None:
                continue
            linked = self.linked_resources(subject, request)
            if any(resource_id.startswith("account_") for resource_id in linked):
                # Already linked by evidence; that always wins.
                continue
            pools[request.case_id] = {
                candidate.resource_id: candidate
                for candidate in self.account_candidates(request, subject)
            }

        assigned: dict[str, _AccountCandidate] = {}
        taken: set[str] = set()
        changed = True
        while changed:
            changed = False
            pending = [case_id for case_id in pools if case_id not in assigned]
            for case_id in pending:
                available = {
                    resource_id: candidate
                    for resource_id, candidate in pools[case_id].items()
                    if resource_id not in taken
                }
                if not available:
                    continue
                best = max(available.values(), key=lambda item: item.score)
                rest = [
                    candidate.score
                    for resource_id, candidate in available.items()
                    if resource_id != best.resource_id
                ]
                if rest and max(rest) >= best.score:
                    continue
                rivals = [
                    pools[other][best.resource_id].score
                    for other in pending
                    if other != case_id and best.resource_id in pools[other]
                ]
                if rivals and max(rivals) >= best.score:
                    continue
                assigned[case_id] = best
                taken.add(best.resource_id)
                changed = True

        unassigned: dict[str, str] = {}
        for case_id, pool in pools.items():
            if case_id in assigned:
                continue
            available = {
                resource_id: candidate
                for resource_id, candidate in pool.items()
                if resource_id not in taken
            }
            if not available:
                unassigned[case_id] = "no remaining candidate"
                continue
            best = max(available.values(), key=lambda item: item.score)
            tied = [
                candidate.resource_id
                for candidate in available.values()
                if candidate.score >= best.score
            ]
            unassigned[case_id] = (
                f"tie at score {best.score:.1f} between {', '.join(sorted(tied))}"
                if len(tied) > 1
                else "contested by another case"
            )
        return assigned, unassigned

    # -- layer 4: identifier-free non-account resources -------------------- #

    def orphans_in(self, evidence_id: str) -> frozenset[str]:
        """Resources in an evidence file that are linked to nobody at all.

        An already-anonymised analytics profile has no identifier by
        construction, which is exactly why it is still a policy violation worth
        finding.
        """
        return frozenset(
            resource_id
            for resource_id in self.resources_in_evidence(evidence_id)
            if not self.owners(resource_id)
            and not resource_id.startswith("account_")
        )

    def attribute_orphans(
        self,
        requests: tuple[Request, ...],
        subjects: dict[str, Subject],
        accounts: dict[str, tuple[str, datetime | None]],
        evidence_ids: dict[str, str],
    ) -> dict[str, list[AttributedResource]]:
        """Attribute identifier-free resources by timing signature.

        Candidate cases for an orphan are the cases sharing its evidence file.
        Each is scored by how well the case's own account-deletion time explains
        the worker events that touched the orphan inside the case window. A case
        wins only when its score is positive and strictly unique, so an
        unexplained orphan stays unattributed rather than being guessed at.
        """
        by_evidence: dict[str, list[str]] = defaultdict(list)
        for case_id, evidence_id in evidence_ids.items():
            if evidence_id:
                by_evidence[evidence_id].append(case_id)
        requests_by_case = {request.case_id: request for request in requests}

        found: dict[str, list[AttributedResource]] = defaultdict(list)
        for evidence_id, case_ids in by_evidence.items():
            for resource_id in sorted(self.orphans_in(evidence_id)):
                scores: dict[str, float] = {}
                for case_id in case_ids:
                    request = requests_by_case.get(case_id)
                    subject = subjects.get(case_id)
                    if request is None or subject is None:
                        continue
                    account_id, deleted_at = accounts.get(case_id, ("", None))
                    if deleted_at is None:
                        continue
                    anchors = self._run_anchor_events(
                        request, frozenset({resource_id})
                    )
                    if not anchors:
                        continue
                    cadence = self.run_cadence(
                        request,
                        deleted_at,
                        frozenset(self.linked_resources(subject, request))
                        - {account_id},
                    )
                    off_cadence = any(
                        not self.matches_cadence(
                            (activity.event_time - deleted_at).total_seconds(), cadence
                        )
                        for activity in anchors
                    )
                    if off_cadence:
                        # This run used a different cadence, so the event is not
                        # part of it however plausible the offset looks.
                        continue
                    score, _, total = self.signature_score(
                        request, deleted_at, frozenset({resource_id})
                    )
                    if total:
                        scores[case_id] = score
                if not scores:
                    continue
                best_score = max(scores.values())
                winners = [case for case, score in scores.items() if score == best_score]
                if best_score <= 0 or len(winners) != 1:
                    continue
                from audit.evidence.systems import system_for_resource

                found[winners[0]].append(
                    AttributedResource(
                        system_id=system_for_resource(resource_id),
                        resource_id=resource_id,
                        how=LinkKind.ORPHAN_TIMING,
                        confidence=0.6,
                        supporting_ids=(evidence_id,),
                    )
                )
        return dict(found)

    # -- the public entry point -------------------------------------------- #

    def attribute_all(
        self, requests: tuple[Request, ...] | None = None, allow_fallback: bool = True
    ) -> dict[str, CaseAttribution]:
        """Attribute every case, running all four layers.

        Args:
            requests: the cases to attribute. Defaults to the dataset's requests.
            allow_fallback: when a case's account cannot be assigned
                unambiguously, take the best remaining candidate and mark it
                low-confidence. A plausible id beats an empty verification.
        """
        cases = requests if requests is not None else self.dataset.requests
        subjects: dict[str, Subject] = {}
        for request in cases:
            subject = self.resolver.resolve(request.email)
            if subject is not None:
                subjects[request.case_id] = subject

        evidence_ids = {
            request.case_id: self.select_case_evidence(subjects[request.case_id], request)
            for request in cases
            if request.case_id in subjects
        }
        assigned, unassigned = self.assign_accounts(cases, subjects)

        accounts: dict[str, tuple[str, datetime | None]] = {}
        resources: dict[str, list[AttributedResource]] = {}
        notes: dict[str, list[str]] = defaultdict(list)

        for request in cases:
            subject = subjects.get(request.case_id)
            if subject is None:
                resources[request.case_id] = []
                notes[request.case_id].append("email did not resolve to a user")
                continue
            attributed: list[AttributedResource] = []
            linked = self.linked_resources(subject, request)
            for resource_id, links in sorted(linked.items()):
                strongest = min(links, key=lambda link: _KIND_RANK[link.kind])
                attributed.append(
                    AttributedResource(
                        system_id=strongest.system_id,
                        resource_id=resource_id,
                        how=strongest.kind,
                        confidence=1.0,
                        supporting_ids=tuple(
                            dict.fromkeys(link.supporting_id for link in links)
                        ),
                    )
                )
            account_ids = [
                resource.resource_id
                for resource in attributed
                if resource.resource_id.startswith("account_")
            ]
            if account_ids:
                account_id = account_ids[0]
                accounts[request.case_id] = (
                    account_id,
                    self._deletion_time_of(account_id, request),
                )
            elif request.case_id in assigned:
                candidate = assigned[request.case_id]
                attributed.append(
                    AttributedResource(
                        system_id="account_service",
                        resource_id=candidate.resource_id,
                        how=LinkKind.WORKFLOW_RUN,
                        confidence=0.8,
                        supporting_ids=(candidate.activity_id,),
                    )
                )
                accounts[request.case_id] = (candidate.resource_id, candidate.at)
                notes[request.case_id].extend(candidate.features)
            else:
                notes[request.case_id].append(
                    f"account unassigned: {unassigned.get(request.case_id, 'no candidate')}"
                )
            resources[request.case_id] = attributed

        if allow_fallback:
            taken = {
                resource.resource_id
                for case_resources in resources.values()
                for resource in case_resources
                if resource.resource_id.startswith("account_")
            }
            for request in cases:
                subject = subjects.get(request.case_id)
                if subject is None or request.case_id in accounts:
                    continue
                remaining = [
                    candidate
                    for candidate in self.account_candidates(request, subject)
                    if candidate.resource_id not in taken
                ]
                if not remaining:
                    continue
                candidate = remaining[0]
                resources[request.case_id].append(
                    AttributedResource(
                        system_id="account_service",
                        resource_id=candidate.resource_id,
                        how=LinkKind.WORKFLOW_RUN,
                        confidence=0.3,
                        supporting_ids=(candidate.activity_id,),
                    )
                )
                accounts[request.case_id] = (candidate.resource_id, candidate.at)
                taken.add(candidate.resource_id)
                notes[request.case_id].append("low-confidence fallback account")

        orphans = self.attribute_orphans(cases, subjects, accounts, evidence_ids)
        for case_id, extra in orphans.items():
            resources[case_id].extend(extra)

        self._attributions = {
            request.case_id: CaseAttribution(
                case_id=request.case_id,
                subject=subjects.get(request.case_id),
                evidence_id=evidence_ids.get(request.case_id, ""),
                resources=tuple(
                    sorted(resources[request.case_id], key=lambda item: item.sort_key)
                ),
                account_resource_id=accounts.get(request.case_id, ("", None))[0],
                account_deleted_at=accounts.get(request.case_id, ("", None))[1],
                notes=tuple(notes.get(request.case_id, ())),
            )
            for request in cases
        }
        return self._attributions

    def attribute(self, case_id: str) -> tuple[AttributedResource, ...]:
        """The resources attributed to one case.

        ``attribute_all`` must have run first, because account assignment is
        joint across cases and cannot be decided for one case in isolation.
        """
        if not self._attributions:
            self.attribute_all()
        attribution = self._attributions.get(case_id)
        return attribution.resources if attribution is not None else ()


#: Link kinds ranked by strength, for choosing which one to report.
_KIND_RANK: dict[LinkKind, int] = {
    LinkKind.CATALOG_LINK: 0,
    LinkKind.EVIDENCE_LINK: 1,
    LinkKind.WORKFLOW_RUN: 2,
    LinkKind.ORPHAN_TIMING: 3,
}


def _group_activities(dataset: Dataset) -> dict[str, tuple[Activity, ...]]:
    """Group the activity stream by resource id."""
    grouped: dict[str, list[Activity]] = defaultdict(list)
    for activity in dataset.activities:
        grouped[activity.resource_id].append(activity)
    return {key: tuple(value) for key, value in grouped.items()}


def _invert(
    owner_links: dict[str, tuple[OwnerLink, ...]],
) -> dict[str, tuple[OwnerLink, ...]]:
    """Index owner links by user instead of by resource."""
    by_user: dict[str, list[OwnerLink]] = defaultdict(list)
    for links in owner_links.values():
        for link in links:
            by_user[link.user_id].append(link)
    return {key: tuple(value) for key, value in by_user.items()}


def _evidence_links(
    index: EvidenceIndex, resolver: IdentityResolver
) -> list[OwnerLink]:
    """Links implied by an observation's identifiers matching a user's."""
    links: list[OwnerLink] = []
    for observation in index.observations:
        for key in observation.link_keys:
            holders = resolver.owner_of_key.get(key, set())
            if len(holders) != 1:
                # Unknown identifier, or one held by two people: not a link.
                continue
            links.append(
                OwnerLink(
                    user_id=next(iter(holders)),
                    resource_id=observation.resource_id,
                    system_id=observation.system_id,
                    at=observation.observed_at,
                    kind=LinkKind.EVIDENCE_LINK,
                    supporting_id=observation.evidence_id,
                )
            )
    return links


def catalog_links(dataset: Dataset) -> list[OwnerLink]:
    """Links stated outright by a ``LINK_RESOURCE_IDENTITY`` activity row.

    The data catalog and the attachment catalog both name a resource, an email
    and a ``user_id`` in one sentence. That is the strongest link available, and
    it is timed at the activity's own ``event_time``.
    """
    from audit.evidence.systems import system_for_resource  # local: avoids a cycle

    links: list[OwnerLink] = []
    for activity in dataset.activities:
        if activity.event_type != LINK_EVENT_TYPE and "catalog" not in activity.details:
            continue
        for pattern in CATALOG_PATTERNS:
            match = pattern.search(activity.details)
            if match is None:
                continue
            resource_id = match.group("resource_id")
            links.append(
                OwnerLink(
                    user_id=match.group("user_id"),
                    resource_id=resource_id,
                    system_id=system_for_resource(resource_id, activity.target_system),
                    at=activity.event_time,
                    kind=LinkKind.CATALOG_LINK,
                    supporting_id=activity.activity_id,
                )
            )
            break
    return links


def _build_owner_links(
    dataset: Dataset, index: EvidenceIndex, resolver: IdentityResolver
) -> dict[str, tuple[OwnerLink, ...]]:
    """Build the resource ownership index from evidence and catalog links."""
    grouped: dict[str, list[OwnerLink]] = defaultdict(list)
    for link in _evidence_links(index, resolver) + catalog_links(dataset):
        grouped[link.resource_id].append(link)
    return {
        key: tuple(sorted(value, key=lambda item: (item.at, item.supporting_id)))
        for key, value in grouped.items()
    }
