"""Assemble the audit: dataset in, ``output.csv`` rows out.

The order of the stages is the architecture:

``loaders`` -> ``evidence`` -> ``identity`` -> ``attribution`` -> ``state``
-> ``policy`` -> ``planner`` -> ``validate``

Every stage is deterministic. The only places a model is used are request-intent
classification and document extraction, both of which are optional and both of
which degrade to a documented default when switched off with ``--no-llm`` or when
no API key is configured.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from audit.aliases import build_alias_map
from audit.attribution import Attributor
from audit.classifier import Decider, IntentClassifier, IntentDecision
from audit.evidence.index import EvidenceIndex, build_index
from audit.evidence.registry import parse_all
from audit.identity import build_resolver
from audit.llm import LlmGateway, UsageTracker
from audit.loaders import Dataset, load_requests
from audit.models import CaseResult, DeletionStatus, Request
from audit.planner import CasePlan, Planner
from audit.policy import PolicyBook
from audit.state import StateResolver
from audit.validate import EXPECTED_HEADER


@dataclass(frozen=True, slots=True)
class AuditRun:
    """The result of auditing a set of requests."""

    results: tuple[CaseResult, ...]
    plans: tuple[CasePlan, ...]
    attributor: Attributor
    index: EvidenceIndex
    observations: int
    coverage: int
    warnings: tuple[str, ...]
    decisions: dict[str, IntentDecision] = field(default_factory=dict)
    usage: UsageTracker = field(default_factory=UsageTracker)
    document_observations: int = 0
    unproven_deletions: tuple[str, ...] = ()

    def rows(self) -> list[tuple[str, str, str]]:
        """The output rows, in the order they are written."""
        return [
            (result.case_id, str(result.deletion_status), result.steps_json())
            for result in self.results
        ]

    def not_applicable(self) -> tuple[str, ...]:
        """Cases ruled out as non-deletion requests."""
        return tuple(
            result.case_id
            for result in self.results
            if result.deletion_status is DeletionStatus.NOT_APPLICABLE
        )


def build_index_for(
    dataset: Dataset,
    cache_dir: Path | None,
    gateway: LlmGateway | None = None,
    prompts_dir: Path | None = None,
) -> tuple[EvidenceIndex, int, int, tuple[str, ...], int]:
    """Parse every evidence artefact and index the result."""
    parsed = parse_all(
        dataset,
        cache_dir,
        gateway=gateway,
        prompts_dir=prompts_dir,
        aliases=build_alias_map(dataset),
    )
    index = build_index(dataset, parsed.observations, parsed.coverage)
    document_sources = {
        evidence.file_id
        for evidence in dataset.evidence_files
        if Path(evidence.file_path).suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
    }
    from_documents = sum(
        1
        for observation in parsed.observations
        if observation.evidence_id in document_sources
    )
    return (
        index,
        len(parsed.observations),
        len(parsed.coverage),
        tuple(parsed.warnings),
        from_documents,
    )


def audit(
    dataset: Dataset,
    cache_dir: Path | None = None,
    learning_requests: tuple[Request, ...] | None = None,
    requests: tuple[Request, ...] | None = None,
    gateway: LlmGateway | None = None,
    prompts_dir: Path | None = None,
    fallback_is_deletion: bool = True,
    decisions: dict[str, IntentDecision] | None = None,
) -> AuditRun:
    """Audit every request in ``dataset`` (or the given subset).

    Args:
        dataset: the loaded dataset.
        cache_dir: where model results are cached.
        learning_requests: requests used to learn the timing signature. Pass the
            full request set when auditing a subset, so the learned bands do not
            depend on which cases were selected.
        requests: the subset to audit. Defaults to every request in the dataset.
        gateway: the model gateway. ``None`` means no model at all, which is the
            ``--no-llm`` behaviour.
        prompts_dir: where the prompt files live.
        fallback_is_deletion: what an unclassifiable request becomes when no
            model is available.
        decisions: pre-computed intent decisions, used instead of running the
            classifier. This is how an offline evaluation fixture supplies a
            recorded classifier answer so the suite stays free and reproducible;
            the audit itself never records or replays its own answers.
    """
    cases = requests if requests is not None else dataset.requests
    index, observation_count, coverage_count, warnings, from_documents = build_index_for(
        dataset, cache_dir, gateway, prompts_dir
    )
    resolver = build_resolver(dataset)
    attributor = Attributor(dataset, index, resolver, learning_requests)
    attributions = attributor.attribute_all(cases)
    planner = Planner(index, StateResolver(dataset, index), PolicyBook(dataset))

    if decisions is None:
        decisions = {}
        if gateway is not None and prompts_dir is not None:
            classifier = IntentClassifier(
                gateway, prompts_dir, fallback_is_deletion=fallback_is_deletion
            )
            decisions = classifier.classify(cases)

    plans: list[CasePlan] = []
    unproven: list[str] = []
    for request in cases:
        decision = decisions.get(request.case_id)
        if decision is not None and not decision.authorises_deletion:
            plans.append(planner.not_applicable(request))
            continue
        attribution = attributions[request.case_id]
        # A request classified as a deletion that yields nothing to verify is an
        # attribution failure, not a clean bill of health (decision D32). A
        # fallback decision does not count: nothing was actually classified.
        nothing_to_verify = attribution.subject is None or not attribution.resources
        if nothing_to_verify and (
            decision is None or decision.decided_by is not Decider.FALLBACK
        ):
            unproven.append(request.case_id)
        if attribution.subject is None:
            plans.append(
                CasePlan(
                    case_id=request.case_id,
                    status=DeletionStatus.COMPLETE,
                    steps=(),
                )
            )
            continue
        plans.append(planner.plan(request, attribution))

    return AuditRun(
        results=tuple(plan.result() for plan in plans),
        plans=tuple(plans),
        attributor=attributor,
        index=index,
        observations=observation_count,
        coverage=coverage_count,
        warnings=warnings,
        decisions=decisions,
        usage=gateway.tracker if gateway is not None else UsageTracker(),
        document_observations=from_documents,
        unproven_deletions=tuple(unproven),
    )


def write_rows(path: Path, rows: Sequence[tuple[str, str, str]]) -> None:
    r"""Write the output CSV deterministically.

    ``\n`` line endings and ``QUOTE_MINIMAL`` keep the file byte-identical across
    platforms and runs.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(EXPECTED_HEADER)
        writer.writerows(rows)


def load_learning_requests(dataset_dir: Path, requests_file: Path) -> tuple[Request, ...]:
    """The full evaluation request set, for unsupervised signature learning.

    Learning from every request rather than only the audited subset keeps the
    learned timing bands identical between a ``--cases`` run, a sample run and a
    full run. No labels are involved.
    """
    full = dataset_dir / "deletion_requests.csv"
    if full == requests_file or not full.is_file():
        return ()
    return load_requests(full)
