"""End-to-end tests of the model layer against the real dataset.

A fake client stands in for the API, so these run with no key and no spend while
still exercising the whole pipeline: classification feeds the planner, and
document extraction feeds attribution, state and citation unchanged.

The two wins the model layer is meant to deliver are asserted here:

1. the ten non-deletion requests become ``NOT_APPLICABLE`` with ``[]``;
2. sample ``case_07`` becomes ``INCOMPLETE``, because ``support_001`` exists only
   in ``delivery_correction_attachment.pdf``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from fake_llm import FakeClient

from audit.llm import LlmGateway, LlmSettings, ModelPricing
from audit.loaders import (
    REQUESTS_FILE,
    SAMPLE_REQUESTS_FILE,
    load_dataset,
    load_requests,
)
from audit.models import DeletionStatus, Operation
from audit.pipeline import audit

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATASET_DIR = REPO_ROOT / "dataset"
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

SETTINGS = LlmSettings(
    classifier_model="model-cheap",
    vision_model="model-vision",
    prices={
        "model-cheap": ModelPricing(0.10, 0.50),
        "model-vision": ModelPricing(4.00, 20.00),
    },
    classifier_batch_size=20,
)

#: What the attachment PDF shows: Priya Raman's support record, still there.
#: ``priya.raman@gmail.com`` is user_07's address in ``user_identities.csv``, and
#: the attachment-catalog activity row names the same person.
ATTACHMENT_RECORDS = {
    "captured_at": "2026-09-12T07:30:00Z",
    "records": [
        {
            "resource_id": "support_001",
            "system_id": "support_platform",
            "record_kind": "support attachment",
            "status": "FOUND",
            "present": True,
            "identifiers": {
                "name": "Priya Raman",
                "email": "priya.raman@gmail.com",
                "phone": None,
                "user_reference": "user_07",
            },
            "flags": {
                "enabled": None,
                "restore_allowed": None,
                "download_available": None,
                "retain_until": None,
                "legal_hold": None,
            },
            "observed_at": "2026-09-12T07:00:00Z",
        }
    ],
}


#: Phrases that mark a genuinely non-deletion request. The stand-in model reads
#: the text rather than answering a fixed verdict, so a stage-1 gap that routes a
#: real deletion request to the model does not silently become a wrong answer.
NON_DELETION_MARKERS: tuple[str, ...] = (
    "not asking for deletion",
    "not a request to erase",
    "don't delete or change anything",
    "leave the account active",
    "keep the account as it is",
    "do not close the account",
    "without removing any of my records",
    "marketing lists only",
    "leave my profile and purchase history in place",
    "continue using the service",
)


def _classify(text: str) -> tuple[bool, str]:
    """How the stand-in model reads one request text."""
    lowered = text.casefold()
    if any(marker in lowered for marker in NON_DELETION_MARKERS):
        return (False, "ACCESS_REQUEST")
    return (True, "ACCOUNT_DELETION")


def responder(request: Mapping[str, Any]) -> str:
    """Answer a classification batch or a document extraction."""
    blocks = request["messages"][0]["content"]
    if any(block.get("type") in {"image", "document"} for block in blocks):
        text = next(
            (block["text"] for block in blocks if block.get("type") == "text"), ""
        )
        if "delivery_correction_attachment.pdf" in text:
            return json.dumps(ATTACHMENT_RECORDS)
        return json.dumps({"captured_at": None, "records": []})

    prompt = blocks[0]["text"]
    answers = []
    for chunk in prompt.split("id: ")[1:]:
        identifier, _, body = chunk.partition("\n")
        is_deletion, request_type = _classify(body)
        answers.append(
            {
                "id": identifier.strip(),
                "is_deletion_request": is_deletion,
                "request_type": request_type,
                "confidence": 0.95,
                "short_reason": "read by the stand-in model",
            }
        )
    return json.dumps(answers)


def run_with_model(tmp_path: Path, sample: bool = False) -> Any:
    """Audit with the fake model wired in."""
    requests_file = DATASET_DIR / (SAMPLE_REQUESTS_FILE if sample else REQUESTS_FILE)
    dataset = load_dataset(DATASET_DIR, requests_file)
    gateway = LlmGateway(
        SETTINGS,
        cache_dir=tmp_path / "llm",
        client=FakeClient.with_responder(responder),
    )
    return audit(
        dataset,
        cache_dir=tmp_path,
        learning_requests=load_requests(DATASET_DIR / REQUESTS_FILE),
        gateway=gateway,
        prompts_dir=PROMPTS_DIR,
    )


@pytest.fixture(scope="module")
def sample_run(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """One audit of the 32 sample cases, with the model layer active."""
    return run_with_model(tmp_path_factory.mktemp("sample"), sample=True)


@pytest.fixture(scope="module")
def full_run(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """One audit of all 518 cases, with the model layer active."""
    return run_with_model(tmp_path_factory.mktemp("full"))


# -- win 1: the non-deletion requests ------------------------------------- #


def test_the_classifier_marks_the_non_deletion_requests(full_run: Any) -> None:
    """Exactly the ten cases that carry a CLASSIFY_REQUEST row."""
    assert set(full_run.not_applicable()) == {
        f"case_{number}" for number in range(533, 543)
    }


def test_a_not_applicable_case_has_an_empty_step_array(full_run: Any) -> None:
    for result in full_run.results:
        if result.deletion_status is DeletionStatus.NOT_APPLICABLE:
            assert result.steps == ()
            assert result.steps_json() == "[]"


def test_case_542_is_no_longer_an_empty_complete(full_run: Any) -> None:
    """The validator warning from the deterministic baseline disappears."""
    result = next(item for item in full_run.results if item.case_id == "case_542")
    assert result.deletion_status is DeletionStatus.NOT_APPLICABLE


def test_no_deletion_case_is_left_unproven(full_run: Any) -> None:
    """Every case classified as a deletion has something to verify."""
    assert full_run.unproven_deletions == ()


def test_only_the_undecidable_requests_reach_the_model(full_run: Any) -> None:
    """Stage 1 decides the rest without spending a token."""
    from audit.classifier import Decider

    deciders = [decision.decided_by for decision in full_run.decisions.values()]
    assert deciders.count(Decider.MODEL) == 10
    assert deciders.count(Decider.RULES) == 508
    assert deciders.count(Decider.FALLBACK) == 0


# -- win 2: the document evidence ----------------------------------------- #


def test_the_attachment_pdf_yields_an_observation(sample_run: Any) -> None:
    observations = sample_run.index.by_evidence.get("evidence_58", ())
    assert [item.resource_id for item in observations] == ["support_001"]
    assert observations[0].present is True


def test_case_07_becomes_incomplete_with_the_support_record(sample_run: Any) -> None:
    """The headline win: a remaining record that only a PDF could reveal."""
    result = next(item for item in sample_run.results if item.case_id == "case_07")
    assert result.deletion_status is DeletionStatus.INCOMPLETE

    support_steps = [
        step for step in result.steps if step.resource_id == "support_001"
    ]
    assert [step.operation for step in support_steps] == [
        Operation.REPORT_REMAINING_RECORD,
        Operation.DELETE_RECORD,
        Operation.VERIFY_RECORD_ABSENT,
    ]


def test_the_case_07_finding_cites_the_attachment_pdf(sample_run: Any) -> None:
    result = next(item for item in sample_run.results if item.case_id == "case_07")
    finding = next(
        step
        for step in result.steps
        if step.operation is Operation.REPORT_REMAINING_RECORD
        and step.resource_id == "support_001"
    )
    assert finding.evidence_id == "evidence_58"


def test_document_observations_are_counted(sample_run: Any) -> None:
    assert sample_run.document_observations >= 1


# -- usage ---------------------------------------------------------------- #


def test_the_cold_run_spends_tokens_and_is_recorded(full_run: Any) -> None:
    usage = full_run.usage
    assert usage.is_cold
    assert usage.api_calls > 0
    assert usage.input_tokens > 0
    assert usage.cost(SETTINGS) > 0


def test_a_second_run_over_the_same_cache_spends_nothing(tmp_path: Path) -> None:
    """The warm-run guarantee: a repeat audit costs zero new tokens."""
    requests_file = DATASET_DIR / SAMPLE_REQUESTS_FILE
    dataset = load_dataset(DATASET_DIR, requests_file)
    learning = load_requests(DATASET_DIR / REQUESTS_FILE)

    cold_client = FakeClient.with_responder(responder)
    cold = audit(
        dataset,
        cache_dir=tmp_path,
        learning_requests=learning,
        gateway=LlmGateway(SETTINGS, cache_dir=tmp_path / "llm", client=cold_client),
        prompts_dir=PROMPTS_DIR,
    )
    assert cold.usage.is_cold
    calls_made = cold_client.calls
    assert calls_made > 0

    warm_client = FakeClient.with_responder(responder)
    warm = audit(
        dataset,
        cache_dir=tmp_path,
        learning_requests=learning,
        gateway=LlmGateway(SETTINGS, cache_dir=tmp_path / "llm", client=warm_client),
        prompts_dir=PROMPTS_DIR,
    )
    assert warm_client.calls == 0
    assert not warm.usage.is_cold
    assert warm.usage.input_tokens == 0
    assert warm.usage.output_tokens == 0
    assert warm.usage.cost(SETTINGS) == 0.0

    # And the answers are identical, not merely cheap.
    assert [
        (result.case_id, str(result.deletion_status), result.steps_json())
        for result in warm.results
    ] == [
        (result.case_id, str(result.deletion_status), result.steps_json())
        for result in cold.results
    ]
