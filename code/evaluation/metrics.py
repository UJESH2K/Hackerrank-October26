"""Scoring metrics for deletion-audit predictions.

Kept apart from the harness so each measure can be tested on its own. The
metrics mirror what the problem statement says scoring considers: the deletion
status, the resources discovered, the steps and their fields, and the order of
the array.

A step's **identity** is ``(phase, operation, system_id, resource_id)`` - what
the step claims about which resource. ``state``, ``evidence_id`` and ``step_no``
are scored separately as **fields**, on the steps whose identity already
matches, so a citation error is never confused with a missing finding.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: A step reduced to what it claims, ignoring its fields.
StepIdentity = tuple[str, str, str, str]

#: The three statuses, in a fixed order so a matrix is comparable across runs.
STATUSES: tuple[str, ...] = ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")

#: Causes a case can mismatch for, most fundamental first. The order matters:
#: a case is attributed to the first cause that applies, because an attribution
#: failure explains any step difference that follows from it.
CAUSES: tuple[str, ...] = (
    "unrecoverable",
    "attribution",
    "status",
    "state",
    "policy",
    "ordering",
    "citation",
)


def step_identity(step: Mapping[str, Any]) -> StepIdentity:
    """What a step claims: its phase, operation, system and resource."""
    return (
        str(step["phase"]),
        str(step["operation"]),
        str(step["system_id"]),
        str(step["resource_id"]),
    )


def resources_of(steps: Iterable[Mapping[str, Any]]) -> set[str]:
    """The resource ids a step list mentions."""
    return {str(step["resource_id"]) for step in steps}


def edit_distance(first: Sequence[Any], second: Sequence[Any]) -> int:
    """Levenshtein distance between two sequences."""
    if not first:
        return len(second)
    if not second:
        return len(first)
    previous = list(range(len(second) + 1))
    for index, left in enumerate(first, start=1):
        current = [index]
        for position, right in enumerate(second, start=1):
            current.append(
                min(
                    previous[position] + 1,
                    current[position - 1] + 1,
                    previous[position - 1] + (left != right),
                )
            )
        previous = current
    return previous[-1]


def normalised_edit_distance(first: Sequence[Any], second: Sequence[Any]) -> float:
    """Edit distance scaled to 0..1, where 0 is identical."""
    longest = max(len(first), len(second))
    return 0.0 if longest == 0 else edit_distance(first, second) / longest


@dataclass
class Counts:
    """True positives, false positives and false negatives."""

    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0

    @property
    def precision(self) -> float:
        """Share of predictions that were right."""
        total = self.true_positive + self.false_positive
        return self.true_positive / total if total else 0.0

    @property
    def recall(self) -> float:
        """Share of the gold that was found."""
        total = self.true_positive + self.false_negative
        return self.true_positive / total if total else 0.0

    @property
    def f1(self) -> float:
        """Harmonic mean of precision and recall."""
        if not (self.precision + self.recall):
            return 0.0
        return 2 * self.precision * self.recall / (self.precision + self.recall)

    def as_dict(self) -> dict[str, float | int]:
        """Serialise for the JSON report."""
        return {
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }


@dataclass
class CaseScore:
    """Everything measured about one case."""

    case_id: str
    gold_status: str
    predicted_status: str
    gold_steps: int
    predicted_steps: int
    matched_steps: int
    gold_resources: int
    found_resources: int
    exact: bool
    sequence_exact: bool
    sequence_distance: float
    cause: str = ""
    missing_resources: tuple[str, ...] = ()
    extra_resources: tuple[str, ...] = ()
    field_errors: tuple[str, ...] = ()

    @property
    def status_correct(self) -> bool:
        """Whether the deletion status matched."""
        return self.gold_status == self.predicted_status


@dataclass
class SuiteScore:
    """Aggregate scores for one set of cases."""

    name: str
    cases: list[CaseScore] = field(default_factory=list)
    steps: Counts = field(default_factory=Counts)
    resources: Counts = field(default_factory=Counts)
    field_hits: dict[str, int] = field(default_factory=dict)
    field_total: int = 0
    identity_resolved: int = 0
    identity_correct: int = 0

    @property
    def total(self) -> int:
        """How many cases were scored."""
        return len(self.cases)

    @property
    def status_correct(self) -> int:
        """How many statuses matched."""
        return sum(1 for case in self.cases if case.status_correct)

    @property
    def exact(self) -> int:
        """How many cases matched gold entirely."""
        return sum(1 for case in self.cases if case.exact)

    @property
    def sequence_exact(self) -> int:
        """How many step arrays matched in order."""
        return sum(1 for case in self.cases if case.sequence_exact)

    @property
    def mean_sequence_distance(self) -> float:
        """Mean normalised edit distance across cases."""
        if not self.cases:
            return 0.0
        return sum(case.sequence_distance for case in self.cases) / len(self.cases)

    def confusion(self) -> dict[str, dict[str, int]]:
        """Gold status -> predicted status -> count."""
        matrix = {gold: dict.fromkeys(STATUSES, 0) for gold in STATUSES}
        for case in self.cases:
            row = matrix.setdefault(case.gold_status, dict.fromkeys(STATUSES, 0))
            row[case.predicted_status] = row.get(case.predicted_status, 0) + 1
        return matrix

    def causes(self) -> dict[str, int]:
        """How many cases failed for each cause."""
        tally = dict.fromkeys(CAUSES, 0)
        for case in self.cases:
            if case.cause:
                tally[case.cause] = tally.get(case.cause, 0) + 1
        return {cause: count for cause, count in tally.items() if count}

    def field_accuracy(self, name: str) -> float:
        """Share of matched steps whose field agreed."""
        if not self.field_total:
            return 0.0
        return self.field_hits.get(name, 0) / self.field_total

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the JSON report."""
        return {
            "suite": self.name,
            "cases": self.total,
            "status_correct": self.status_correct,
            "status_accuracy": round(self.status_correct / (self.total or 1), 4),
            "confusion": self.confusion(),
            "steps": self.steps.as_dict(),
            "resource_discovery": self.resources.as_dict(),
            "fields": {
                name: round(self.field_accuracy(name), 4)
                for name in ("state", "evidence_id", "step_no")
            },
            "matched_steps": self.field_total,
            "sequence_exact": self.sequence_exact,
            "sequence_accuracy": round(self.sequence_exact / (self.total or 1), 4),
            "mean_sequence_distance": round(self.mean_sequence_distance, 4),
            "exact_cases": self.exact,
            "exact_accuracy": round(self.exact / (self.total or 1), 4),
            "identity_resolved": self.identity_resolved,
            "identity_correct": self.identity_correct,
            "identity_accuracy": round(
                self.identity_correct / (self.total or 1), 4
            ),
            "causes": self.causes(),
            "per_case": [
                {
                    "case_id": case.case_id,
                    "gold_status": case.gold_status,
                    "predicted_status": case.predicted_status,
                    "gold_steps": case.gold_steps,
                    "predicted_steps": case.predicted_steps,
                    "matched_steps": case.matched_steps,
                    "exact": case.exact,
                    "sequence_distance": round(case.sequence_distance, 4),
                    "cause": case.cause,
                    "missing_resources": list(case.missing_resources),
                    "extra_resources": list(case.extra_resources),
                    "field_errors": list(case.field_errors),
                }
                for case in self.cases
            ],
        }


def classify_cause(
    gold: Sequence[Mapping[str, Any]],
    predicted: Sequence[Mapping[str, Any]],
    gold_status: str,
    predicted_status: str,
    unrecoverable: set[str],
) -> str:
    """Name the single most fundamental reason a case did not match.

    Order matters. An attribution failure explains every step difference that
    follows from it, so it is reported instead of the symptoms. ``unrecoverable``
    is checked first: a resource the dataset simply does not contain is not a
    defect in any module (decision D22).
    """
    gold_resources = resources_of(gold)
    predicted_resources = resources_of(predicted)
    missing = gold_resources - predicted_resources

    if missing and missing <= unrecoverable:
        return "unrecoverable"
    if gold_resources != predicted_resources:
        return "attribution"
    if gold_status != predicted_status:
        return "status"

    gold_by_resource = _operations_by_resource(gold)
    predicted_by_resource = _operations_by_resource(predicted)
    for resource_id, wanted in gold_by_resource.items():
        got = predicted_by_resource.get(resource_id, frozenset())
        if wanted == got:
            continue
        # A difference in which verification is asked for is a policy
        # disagreement; a difference only in whether a finding exists is a state
        # disagreement.
        if _verifications(wanted) != _verifications(got):
            return "policy"
        return "state"

    gold_identities = [step_identity(step) for step in gold]
    predicted_identities = [step_identity(step) for step in predicted]
    if gold_identities != predicted_identities:
        return "ordering"
    return "citation"


def _operations_by_resource(
    steps: Sequence[Mapping[str, Any]],
) -> dict[str, frozenset[str]]:
    """Which operations each resource receives."""
    found: dict[str, set[str]] = {}
    for step in steps:
        found.setdefault(str(step["resource_id"]), set()).add(str(step["operation"]))
    return {key: frozenset(value) for key, value in found.items()}


def _verifications(operations: frozenset[str]) -> frozenset[str]:
    """Just the verification operations of a set."""
    return frozenset(name for name in operations if name.startswith("VERIFY_"))


def score_case(
    case_id: str,
    gold_status: str,
    gold_steps: Sequence[Mapping[str, Any]],
    predicted_status: str,
    predicted_steps: Sequence[Mapping[str, Any]],
    unrecoverable: set[str],
) -> tuple[CaseScore, Counts, Counts, dict[str, int], int]:
    """Score one case.

    Returns the case score, its step counts, its resource counts, the per-field
    hit counts and how many steps matched by identity.
    """
    gold_ids = [step_identity(step) for step in gold_steps]
    predicted_ids = [step_identity(step) for step in predicted_steps]

    remaining = list(predicted_steps)
    matched_pairs: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for gold_step in gold_steps:
        wanted = step_identity(gold_step)
        for index, candidate in enumerate(remaining):
            if step_identity(candidate) == wanted:
                matched_pairs.append((gold_step, candidate))
                remaining.pop(index)
                break

    steps = Counts(
        true_positive=len(matched_pairs),
        false_positive=len(predicted_steps) - len(matched_pairs),
        false_negative=len(gold_steps) - len(matched_pairs),
    )

    gold_resources = resources_of(gold_steps)
    predicted_resources = resources_of(predicted_steps)
    resources = Counts(
        true_positive=len(gold_resources & predicted_resources),
        false_positive=len(predicted_resources - gold_resources),
        false_negative=len(gold_resources - predicted_resources),
    )

    field_hits = {"state": 0, "evidence_id": 0, "step_no": 0}
    field_errors: list[str] = []
    for gold_step, predicted_step in matched_pairs:
        for name in field_hits:
            if str(gold_step.get(name)) == str(predicted_step.get(name)):
                field_hits[name] += 1
            else:
                field_errors.append(
                    f"{gold_step['resource_id']}/{gold_step['operation']}: "
                    f"{name} {predicted_step.get(name)!r} != {gold_step.get(name)!r}"
                )

    exact = (
        gold_status == predicted_status
        and len(gold_steps) == len(predicted_steps)
        and all(
            _full_step(a) == _full_step(b)
            for a, b in zip(gold_steps, predicted_steps, strict=False)
        )
    )
    cause = (
        ""
        if exact
        else classify_cause(
            gold_steps, predicted_steps, gold_status, predicted_status, unrecoverable
        )
    )

    score = CaseScore(
        case_id=case_id,
        gold_status=gold_status,
        predicted_status=predicted_status,
        gold_steps=len(gold_steps),
        predicted_steps=len(predicted_steps),
        matched_steps=len(matched_pairs),
        gold_resources=len(gold_resources),
        found_resources=len(gold_resources & predicted_resources),
        exact=exact,
        sequence_exact=gold_ids == predicted_ids,
        sequence_distance=normalised_edit_distance(gold_ids, predicted_ids),
        cause=cause,
        missing_resources=tuple(sorted(gold_resources - predicted_resources)),
        extra_resources=tuple(sorted(predicted_resources - gold_resources)),
        field_errors=tuple(field_errors[:6]),
    )
    return (score, steps, resources, field_hits, len(matched_pairs))


def _full_step(step: Mapping[str, Any]) -> str:
    """A step serialised for exact comparison, key order independent."""
    return json.dumps(
        {
            "phase": step["phase"],
            "operation": step["operation"],
            "system_id": step["system_id"],
            "resource_id": step["resource_id"],
            "state": step["state"],
            "evidence_id": step["evidence_id"],
            "step_no": step["step_no"],
        },
        sort_keys=True,
    )
