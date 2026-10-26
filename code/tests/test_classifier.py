"""Tests for request-intent classification.

Stage 1 is tested on examples written here, not on dataset rows, so the rules
are checked against the language rather than against the answers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fake_llm import FakeClient

from audit.classifier import (
    Decider,
    IntentClassifier,
    RequestType,
    classify_by_rules,
    needs_model,
    normalise_request,
)
from audit.llm import LlmGateway, LlmSettings, ModelPricing
from audit.models import ReportedStatus, Request

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

SETTINGS = LlmSettings(
    classifier_model="model-cheap",
    vision_model="model-vision",
    prices={"model-cheap": ModelPricing(0.10, 0.50)},
    classifier_batch_size=3,
)


def request(text: str, case_id: str = "case_1") -> Request:
    """A request carrying only the text under test."""
    moment = datetime(2026, 9, 1, tzinfo=UTC)
    return Request(
        case_id=case_id,
        email="person@example.com",
        requested_at=moment,
        reported_status=ReportedStatus.COMPLETE,
        reported_at=moment,
        audit_at=moment,
        request_text=text,
    )


# -- stage 1: clear deletions decided without a model ---------------------- #


@pytest.mark.parametrize(
    "text",
    [
        "Please permanently delete the account associated with this email address.",
        "I would like the account associated with this address to be erased.",
        "Please close my account and remove the personal details attached to it.",
        "I am exercising my right to be forgotten; please wipe my data.",
        # Spanish
        "Solicito la eliminación permanente de la cuenta asociada a este correo.",
        # Portuguese
        "Solicito a exclusão permanente da conta associada a este e-mail.",
        # French
        "Je demande la suppression définitive du compte associé à cette adresse.",
        # Japanese
        "このメールアドレスに関連するアカウントの完全な削除を依頼します。",
        # Korean
        "이 이메일 주소에 연결된 계정의 영구 삭제를 요청합니다.",
        # German
        "Bitte löschen Sie mein Konto und alle zugehörigen Daten.",
    ],
)
def test_a_clear_deletion_needs_no_model(text: str) -> None:
    decision = classify_by_rules(request(text))
    assert decision is not None
    assert decision.is_deletion_request
    assert decision.request_type is RequestType.ACCOUNT_DELETION
    assert decision.decided_by is Decider.RULES


# -- stage 2: every trap type goes to the model ---------------------------- #


@pytest.mark.parametrize(
    ("text", "expected_reason"),
    [
        # Negation trap: the deletion word is the thing being refused.
        (
            "Please stop the promotional emails. I still need the account, "
            "so I am not asking for deletion.",
            "retention statement",
        ),
        (
            "What personal information do you hold about me? This is an "
            "information request, not a request to erase it.",
            "retention statement",
        ),
        (
            "Send me a copy of my data. I still use it, so please don't delete "
            "or change anything.",
            "retention statement",
        ),
        # Scope trap: "only" narrows the request.
        ("Please delete only my saved addresses.", "scoping word"),
        ("Please remove me from marketing lists only.", "no recognised deletion"),
        # Account must survive.
        (
            "Please erase the old address but leave the account active.",
            "retention statement",
        ),
        (
            "Can you correct it without removing any of my records?",
            "no recognised deletion",
        ),
        # Other request types with no deletion cue at all.
        ("I'd like to download my account information in a portable format.", "no recognised"),
        ("My surname is misspelled on the account. Can you correct my name?", "no recognised"),
        ("I can't access my profile and need help resetting the login.", "no recognised"),
        ("I need a copy of last year's invoices for my records.", "no recognised"),
        ("Please change the email address used for this account.", "no recognised"),
        # A deletion request that also mentions an export: ambiguous, so ask.
        (
            "Please delete my account, and send me an export of my data first.",
            "other-request vocabulary",
        ),
    ],
)
def test_traps_and_other_requests_go_to_the_model(text: str, expected_reason: str) -> None:
    required, reason = needs_model(text)
    assert required
    assert expected_reason in reason
    assert classify_by_rules(request(text)) is None


def test_partial_retention_is_still_a_clear_deletion() -> None:
    """"Delete my account, keep anonymous stats" is a deletion request."""
    decision = classify_by_rules(
        request(
            "Please delete my account and personal data. You may keep anonymous "
            "statistics."
        )
    )
    assert decision is not None
    assert decision.is_deletion_request


def test_the_substring_trap_in_correct_is_not_a_rectification_cue() -> None:
    """"apply the correct treatment" is not a request to correct anything."""
    text = (
        "Please permanently delete the account associated with this email address. "
        "Please apply the correct treatment to records covered by the request."
    )
    required, _ = needs_model(text)
    assert not required


# -- normalisation --------------------------------------------------------- #


def test_normalisation_masks_emails_and_digits() -> None:
    first = normalise_request("Delete account for a.b@example.com, order 12345.")
    second = normalise_request("Delete account for c.d@other.org, order 98765.")
    assert first == second
    assert "<email>" in first and "#" in first


def test_normalisation_collapses_whitespace() -> None:
    assert normalise_request("Delete   my\n account") == "Delete my account"


# -- stage 2 wiring -------------------------------------------------------- #


def classifier(
    tmp_path: Path, client: FakeClient | None = None, enabled: bool = True
) -> IntentClassifier:
    """A classifier over a temporary cache."""
    gateway = LlmGateway(
        SETTINGS, cache_dir=tmp_path / "llm", client=client, enabled=enabled
    )
    return IntentClassifier(gateway, PROMPTS_DIR)


def reply_for(templates: int, deletion: bool = False) -> str:
    """A scripted batch reply covering ``templates`` ids."""
    return json.dumps(
        [
            {
                "id": f"r{index}",
                "is_deletion_request": deletion,
                "request_type": "ACCOUNT_DELETION" if deletion else "ACCESS_REQUEST",
                "confidence": 0.95,
                "short_reason": "scripted",
            }
            for index in range(templates)
        ]
    )


def test_the_model_decides_the_cases_rules_cannot(tmp_path: Path) -> None:
    client = FakeClient.with_replies(reply_for(1))
    instance = classifier(tmp_path, client)
    decisions = instance.classify(
        [
            request("Please permanently delete my account.", "case_1"),
            request("Send me a copy of my data; don't delete anything.", "case_2"),
        ]
    )
    assert decisions["case_1"].decided_by is Decider.RULES
    assert decisions["case_1"].is_deletion_request
    assert decisions["case_2"].decided_by is Decider.MODEL
    assert not decisions["case_2"].is_deletion_request
    assert decisions["case_2"].request_type is RequestType.ACCESS_REQUEST
    assert client.calls == 1


def test_identical_wording_is_classified_once(tmp_path: Path) -> None:
    """Two cases with the same text share one cached answer."""
    client = FakeClient.with_replies(reply_for(1))
    instance = classifier(tmp_path, client)
    text = "Send me a copy of my invoices; please leave my profile in place."
    decisions = instance.classify(
        [request(text, "case_1"), request(text, "case_2")]
    )
    assert not decisions["case_1"].is_deletion_request
    assert not decisions["case_2"].is_deletion_request
    assert client.calls == 1


def test_requests_are_batched(tmp_path: Path) -> None:
    """A batch size of three means four texts take two calls, not four."""
    client = FakeClient.with_responder(
        lambda request: reply_for(request["messages"][0]["content"][0]["text"].count("id: r"))
    )
    instance = classifier(tmp_path, client)
    texts = [
        "I need a copy of my invoices, leave the profile in place.",
        "Please change the email on the account.",
        "I cannot access my profile and need the login reset.",
        "Remove me from marketing lists only.",
    ]
    decisions = instance.classify(
        [request(text, f"case_{index}") for index, text in enumerate(texts)]
    )
    assert len(decisions) == 4
    assert client.calls == 2


def test_a_warm_classifier_makes_no_calls(tmp_path: Path) -> None:
    client = FakeClient.with_replies(reply_for(1))
    text = "Send me a copy of my data; don't delete anything."
    classifier(tmp_path, client).classify([request(text, "case_1")])
    assert client.calls == 1

    second = classifier(tmp_path, client)
    decisions = second.classify([request(text, "case_1")])
    assert client.calls == 1
    assert not decisions["case_1"].is_deletion_request
    assert second.gateway.tracker.cache_hits == 1
    assert second.gateway.tracker.input_tokens == 0


def test_no_model_falls_back_to_the_deterministic_default(tmp_path: Path) -> None:
    """Without a model the baseline behaviour is reproduced exactly."""
    instance = classifier(tmp_path, client=None, enabled=False)
    decisions = instance.classify(
        [request("Send me a copy of my data; don't delete anything.", "case_1")]
    )
    assert decisions["case_1"].decided_by is Decider.FALLBACK
    assert decisions["case_1"].is_deletion_request


def test_the_fallback_direction_is_configurable(tmp_path: Path) -> None:
    gateway = LlmGateway(SETTINGS, cache_dir=tmp_path / "llm", enabled=False)
    instance = IntentClassifier(gateway, PROMPTS_DIR, fallback_is_deletion=False)
    decisions = instance.classify(
        [request("Send me a copy of my data; don't delete anything.", "case_1")]
    )
    assert not decisions["case_1"].is_deletion_request


def test_an_unparseable_model_reply_falls_back(tmp_path: Path) -> None:
    client = FakeClient.with_replies("nonsense", "still nonsense")
    instance = classifier(tmp_path, client)
    decisions = instance.classify(
        [request("Send me a copy of my data; don't delete anything.", "case_1")]
    )
    assert decisions["case_1"].decided_by is Decider.FALLBACK


def test_the_type_and_the_boolean_are_forced_to_agree(tmp_path: Path) -> None:
    """A reply claiming ACCOUNT_DELETION with is_deletion_request false is fixed."""
    client = FakeClient.with_replies(
        json.dumps(
            [
                {
                    "id": "r0",
                    "is_deletion_request": False,
                    "request_type": "ACCOUNT_DELETION",
                    "confidence": 0.9,
                    "short_reason": "inconsistent",
                }
            ]
        )
    )
    instance = classifier(tmp_path, client)
    decisions = instance.classify(
        [request("Send me a copy of my data; don't delete anything.", "case_1")]
    )
    assert not decisions["case_1"].is_deletion_request
    assert decisions["case_1"].request_type is RequestType.OTHER


def test_an_unknown_request_type_is_normalised(tmp_path: Path) -> None:
    client = FakeClient.with_replies(
        json.dumps(
            [
                {
                    "id": "r0",
                    "is_deletion_request": False,
                    "request_type": "SOMETHING_NEW",
                    "confidence": 2.5,
                    "short_reason": "x",
                }
            ]
        )
    )
    instance = classifier(tmp_path, client)
    decisions = instance.classify(
        [request("Send me a copy of my data; don't delete anything.", "case_1")]
    )
    assert decisions["case_1"].request_type is RequestType.OTHER
    assert decisions["case_1"].confidence == 1.0


def test_a_missing_item_in_the_batch_reply_falls_back(tmp_path: Path) -> None:
    """A reply that answers one of two texts must not mislabel the other."""
    client = FakeClient.with_replies(reply_for(1))
    instance = classifier(tmp_path, client)
    decisions = instance.classify(
        [
            request("I need a copy of my invoices, leave the profile in place.", "case_1"),
            request("Please change the email on the account.", "case_2"),
        ]
    )
    sources = {decision.decided_by for decision in decisions.values()}
    assert Decider.FALLBACK in sources


def test_no_llm_ignores_a_cached_model_verdict(tmp_path: Path) -> None:
    """The deterministic baseline must not inherit a cached model answer."""
    text = "Send me a copy of my data; don't delete anything."
    client = FakeClient.with_replies(reply_for(1))
    warm = classifier(tmp_path, client)
    assert not warm.classify([request(text, "case_1")])["case_1"].is_deletion_request

    disabled = classifier(tmp_path, client=None, enabled=False)
    decision = disabled.classify([request(text, "case_1")])["case_1"]
    assert decision.decided_by is Decider.FALLBACK
    assert decision.is_deletion_request


# -- the asymmetric safety rule -------------------------------------------- #


def verdict(is_deletion: bool, confidence: float, request_type: str = "ACCESS_REQUEST") -> str:
    """A one-item batch reply at a given confidence."""
    return json.dumps(
        [
            {
                "id": "r0",
                "is_deletion_request": is_deletion,
                "request_type": request_type,
                "confidence": confidence,
                "short_reason": "scripted verdict",
            }
        ]
    )


NON_DELETION_TEXT = "Send me a copy of my data; don't delete anything."


@pytest.mark.parametrize("confidence", [0.0, 0.5, 0.6, 0.85, 0.89])
def test_an_unconfident_non_deletion_is_audited_as_a_deletion(
    tmp_path: Path, confidence: float
) -> None:
    """Discarding a real deletion case costs far more than one extra audit."""
    client = FakeClient.with_replies(verdict(False, confidence))
    decisions = classifier(tmp_path, client).classify(
        [request(NON_DELETION_TEXT, "case_1")]
    )
    decision = decisions["case_1"]
    assert decision.is_deletion_request
    assert decision.overridden
    assert decision.request_type is RequestType.ACCOUNT_DELETION
    assert "audited as a deletion" in decision.reason
    assert "scripted verdict" in decision.reason


@pytest.mark.parametrize("confidence", [0.9, 0.95, 1.0])
def test_a_confident_non_deletion_is_accepted(tmp_path: Path, confidence: float) -> None:
    client = FakeClient.with_replies(verdict(False, confidence))
    decisions = classifier(tmp_path, client).classify(
        [request(NON_DELETION_TEXT, "case_1")]
    )
    assert not decisions["case_1"].is_deletion_request
    assert not decisions["case_1"].overridden


@pytest.mark.parametrize("confidence", [0.0, 0.5, 0.95])
def test_a_deletion_verdict_is_never_overridden(tmp_path: Path, confidence: float) -> None:
    """The rule is one-sided: only non-deletion needs the confidence bar."""
    client = FakeClient.with_replies(verdict(True, confidence, "ACCOUNT_DELETION"))
    decisions = classifier(tmp_path, client).classify(
        [request(NON_DELETION_TEXT, "case_1")]
    )
    assert decisions["case_1"].is_deletion_request
    assert not decisions["case_1"].overridden


def test_the_confidence_bar_is_stated_once() -> None:
    from audit.classifier import MIN_NON_DELETION_CONFIDENCE

    assert MIN_NON_DELETION_CONFIDENCE == 0.9


def test_the_prompt_is_at_version_two() -> None:
    """Version 2 added the follow-up definition; v1 misclassified those."""
    from audit.llm import load_prompt

    version, body = load_prompt(PROMPTS_DIR / "request_intent_classifier.md")
    assert version == "2"
    assert "finish, verify or chase up an earlier deletion" in body
