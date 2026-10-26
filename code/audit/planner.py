"""Turn attributed resources and their state into the ordered ``steps_json``.

For each attributed resource the planner asks three questions in order: what is
its state at ``audit_at`` (``state.py``), what does policy require of it
(``policy.py``), and does the current state already satisfy that requirement. A
requirement already met contributes a verification step only; one not met
contributes a finding, a remediation and a verification.

The case's status follows from the steps rather than the other way round: any
finding at all means ``INCOMPLETE``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from audit.attribution import AttributedResource, CaseAttribution
from audit.evidence.index import EvidenceIndex
from audit.models import (
    OPERATION_PHASE,
    OPERATION_STATE,
    CaseResult,
    DeletionStatus,
    Operation,
    Phase,
    Request,
    Step,
)
from audit.policy import PolicyBook, Treatment
from audit.state import ResourceState, StateResolver


@dataclass(frozen=True, slots=True)
class ResourcePlan:
    """The steps one resource contributes, with the reasoning behind them."""

    resource: AttributedResource
    state: ResourceState
    category: str
    treatment: Treatment
    rule_id: str
    reason: str
    needs_action: bool
    steps: tuple[Step, ...]


@dataclass(frozen=True, slots=True)
class CasePlan:
    """A case's full plan: its result row and the per-resource reasoning."""

    case_id: str
    status: DeletionStatus
    steps: tuple[Step, ...]
    plans: tuple[ResourcePlan, ...] = ()

    def result(self) -> CaseResult:
        """The row written to ``output.csv``."""
        return CaseResult(
            case_id=self.case_id, deletion_status=self.status, steps=self.steps
        )


def order_steps(steps: tuple[Step, ...]) -> tuple[Step, ...]:
    """Sort steps into the required output order and number them from 1.

    Phase, then the operation's position in the problem statement's table, then
    ``system_id``, then ``resource_id``, all as plain string sorts. This is the
    only ordering rule; see decision D3.
    """
    ordered = sorted(steps, key=lambda step: step.sort_key)
    return tuple(step.renumbered(number) for number, step in enumerate(ordered, start=1))


def needs_action(treatment: Treatment, state: ResourceState) -> bool:
    """Whether the resource's current state falls short of what policy requires.

    ``RETAIN`` never needs action: the record is meant to be there. For the other
    treatments the question is whether the thing policy objects to is still true,
    and an unknown state is never reported as a violation - a finding requires
    positive evidence (decision D24).
    """
    if treatment is Treatment.RETAIN:
        return False
    if treatment is Treatment.DELETE:
        return state.present is True
    if treatment is Treatment.DISABLE_FLOW:
        return state.flow_enabled is True
    if treatment is Treatment.BLOCK_RESTORE:
        return state.restorable is True
    if treatment is Treatment.REDACT:
        # A tombstone is allowed to keep its stable internal reference; only a
        # direct identifier counts as exposure.
        if state.present is False:
            return False
        return state.identifiers_exposed is True
    if treatment is Treatment.ANONYMIZE:
        if state.present is False:
            return False
        return state.identifiers_exposed is True
    return False


class Planner:
    """Builds a case's steps from its attribution."""

    def __init__(
        self,
        index: EvidenceIndex,
        states: StateResolver,
        policy: PolicyBook,
    ) -> None:
        """Hold the collaborators the planner needs."""
        self.index = index
        self.states = states
        self.policy = policy

    def plan(self, request: Request, attribution: CaseAttribution) -> CasePlan:
        """Plan one case."""
        plans: list[ResourcePlan] = []
        for resource in attribution.resources:
            plans.append(
                self._plan_resource(resource, request.audit_at, attribution.evidence_id)
            )
        steps = order_steps(tuple(step for plan in plans for step in plan.steps))
        status = (
            DeletionStatus.INCOMPLETE
            if any(step.phase is Phase.FINDING for step in steps)
            else DeletionStatus.COMPLETE
        )
        steps = self._apply_evidence_rules(steps, status)
        return CasePlan(
            case_id=request.case_id,
            status=status,
            steps=steps,
            plans=tuple(plans),
        )

    def not_applicable(self, request: Request) -> CasePlan:
        """The plan for a request that does not authorise deletion."""
        return CasePlan(
            case_id=request.case_id,
            status=DeletionStatus.NOT_APPLICABLE,
            steps=(),
        )

    def _plan_resource(
        self,
        resource: AttributedResource,
        audit_at: datetime,
        case_evidence_id: str,
    ) -> ResourcePlan:
        """Plan the steps for one resource."""
        state = self.states.resolve(
            resource.resource_id, resource.system_id, audit_at, case_evidence_id
        )
        classification = self.policy.classify(state)
        finding, remediation, verification = classification.operations
        act = needs_action(classification.treatment, state)
        evidence_id = self._citation(resource, state, case_evidence_id)

        steps: list[Step] = []
        if act:
            steps.append(_step(finding, resource, evidence_id))
            steps.append(_step(remediation, resource, ""))
        steps.append(_step(verification, resource, evidence_id))
        return ResourcePlan(
            resource=resource,
            state=state,
            category=classification.category,
            treatment=classification.treatment,
            rule_id=classification.rule_id,
            reason=classification.reason,
            needs_action=act,
            steps=tuple(steps),
        )

    def _citation(
        self,
        resource: AttributedResource,
        state: ResourceState,
        case_evidence_id: str,
    ) -> str:
        """The evidence a step about this resource should cite.

        A generated case cites its case-level evidence file for every step, which
        is what the sample answers do when that file holds the resource. A
        hand-built case cites the file that actually proves the resource's state,
        including a coverage snapshot that no longer lists it.
        """
        if case_evidence_id and resource.resource_id in {
            observation.resource_id
            for observation in self.index.by_evidence.get(case_evidence_id, ())
        }:
            return case_evidence_id
        if state.evidence_id:
            return state.evidence_id
        for supporting in resource.supporting_ids:
            if supporting.startswith("evidence_"):
                return supporting
        if case_evidence_id:
            return case_evidence_id
        # Some resources appear in no evidence file at all: the twelve
        # ``LINK_RESOURCE_IDENTITY`` catalog rows are the only participant-visible
        # proof that they belong to the subject. The contract permits citing an
        # ``activity_id`` or ``audit_id``, and that row is the proof.
        return resource.supporting_ids[0] if resource.supporting_ids else ""

    @staticmethod
    def _apply_evidence_rules(
        steps: tuple[Step, ...], status: DeletionStatus
    ) -> tuple[Step, ...]:
        """Enforce the contract's citation rules on a finished step list.

        For a ``COMPLETE`` case every verification step cites the evidence proving
        the compliant state. For an ``INCOMPLETE`` case findings cite evidence
        while remediation and verification describe future work and carry ``""``.
        """
        adjusted: list[Step] = []
        for step in steps:
            if status is DeletionStatus.INCOMPLETE and step.phase is not Phase.FINDING:
                adjusted.append(
                    Step(
                        phase=step.phase,
                        system_id=step.system_id,
                        resource_id=step.resource_id,
                        operation=step.operation,
                        state=step.state,
                        evidence_id="",
                        step_no=step.step_no,
                    )
                )
            else:
                adjusted.append(step)
        return tuple(adjusted)


def _step(operation: Operation, resource: AttributedResource, evidence_id: str) -> Step:
    """Build one step, deriving its phase and state from the operation."""
    return Step(
        phase=OPERATION_PHASE[operation],
        system_id=resource.system_id,
        resource_id=resource.resource_id,
        operation=operation,
        state=OPERATION_STATE[operation],
        evidence_id=evidence_id,
    )
