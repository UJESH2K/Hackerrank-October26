"""Tests for category resolution and treatment selection."""

from __future__ import annotations

from pathlib import Path

import pytest

from audit.loaders import Dataset, load_rules
from audit.models import RequiredAction
from audit.policy import PolicyBook, Treatment, resolve_category
from audit.state import ResourceState

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def book() -> PolicyBook:
    """A policy book over the real rules file, which is participant-facing."""
    rules = load_rules(REPO_ROOT / "dataset" / "deletion_rules.csv")
    dataset = Dataset(
        dataset_dir=REPO_ROOT / "dataset",
        requests=(),
        identities=(),
        audit_log=(),
        activities=(),
        rules=rules,
        evidence_files=(),
    )
    return PolicyBook(dataset)


def state(resource_id: str, **attributes: str) -> ResourceState:
    """A resource state carrying only the attributes under test."""
    return ResourceState(
        resource_id=resource_id,
        system_id="",
        attributes=dict(attributes),
    )


# -- the rules file is the source of the action ---------------------------- #


def test_the_action_comes_from_the_rules_file() -> None:
    policy = book()
    assert policy.action_for("USER_PROFILE") == (RequiredAction.DELETE, "rule_01")
    assert policy.action_for("SUPPORT_MESSAGE") == (RequiredAction.REDACT, "rule_05")
    assert policy.action_for("ANALYTICS_PROFILE") == (RequiredAction.ANONYMIZE, "rule_07")
    assert policy.action_for("SECURITY_AUDIT_LOG") == (RequiredAction.RETAIN, "rule_12")


def test_an_unknown_category_has_no_rule() -> None:
    assert book().action_for("NOT_A_CATEGORY") == (None, "")


# -- category resolution --------------------------------------------------- #


@pytest.mark.parametrize(
    ("resource_id", "category"),
    [
        ("account_5", "USER_PROFILE"),
        ("search_5", "SEARCH_INDEX_ENTRY"),
        ("cache_5", "CACHE_ENTRY"),
        ("export_5", "USER_EXPORT"),
        ("marketing_5", "MARKETING_PROFILE"),
        ("audit_record_5", "SECURITY_AUDIT_LOG"),
        ("legal_hold_5", "LEGAL_HOLD_RECORD"),
        ("backup_5", "BACKUP_ARCHIVE"),
        ("tombstone_5", "ACCOUNT_TOMBSTONE"),
        ("email_delivery_5", "EMAIL_DELIVERY_LOG"),
        ("data_flow_5", "DATA_FLOW"),
    ],
)
def test_prefix_resolves_the_unambiguous_systems(resource_id: str, category: str) -> None:
    assert resolve_category(state(resource_id))[0] == category


def test_a_category_stated_in_the_evidence_wins_over_the_prefix() -> None:
    """The handover record and several JSON collections name the category."""
    resolved, reason = resolve_category(
        state("support_5", record_type="SUPPORT_ATTACHMENT")
    )
    assert resolved == "SUPPORT_ATTACHMENT"
    assert reason == "stated in evidence"


def test_an_unknown_stated_category_is_ignored() -> None:
    assert resolve_category(state("account_5", record_type="SOMETHING_ELSE"))[0] == (
        "USER_PROFILE"
    )


# -- support disambiguation ------------------------------------------------ #


@pytest.mark.parametrize(
    "attributes",
    [
        {"collection": "ticket_attachments"},
        {"storage_class": "ticket_attachment"},
        {"event": "attachment_pii_scan", "events": "attachment_pii_scan"},
        {"file_name": "delivery_confirmation_16.pdf"},
        {"uploaded_by": "a@b.com"},
    ],
)
def test_support_attachment_signals(attributes: dict[str, str]) -> None:
    """An attachment belonging only to the subject is deleted, not redacted."""
    assert resolve_category(state("support_5", **attributes))[0] == "SUPPORT_ATTACHMENT"


def test_a_legacy_contact_copy_is_a_secondary_identifier() -> None:
    """The support replica's legacy index is a copy of the contact details."""
    resolved, _ = resolve_category(
        state("support_5", source="legacy_contact_index", event="contact_query_completed")
    )
    assert resolved == "SECONDARY_IDENTIFIER"


@pytest.mark.parametrize(
    "attributes",
    [
        {"collection": "support_conversations"},
        {"collection": "tickets"},
        {"event": "message_pii_scan", "events": "message_pii_scan"},
        {"message_excerpt": "Customer asked about an order."},
        {},
    ],
)
def test_support_message_signals(attributes: dict[str, str]) -> None:
    """A conversation carries shared order context, so it is redacted."""
    assert resolve_category(state("support_5", **attributes))[0] == "SUPPORT_MESSAGE"


# -- analytics disambiguation ---------------------------------------------- #


def test_an_analytics_contact_lookup_is_a_secondary_identifier() -> None:
    """Keyed on an email hash, it is an identifier copy, not a profile."""
    resolved, _ = resolve_category(
        state(
            "analytics_9914",
            event="contact_lookup_completed",
            events="contact_lookup_completed",
        )
    )
    assert resolved == "SECONDARY_IDENTIFIER"


def test_an_analytics_profile_is_anonymised() -> None:
    policy = book()
    classification = policy.classify(state("analytics_5", audience_cohort="returning"))
    assert classification.category == "ANALYTICS_PROFILE"
    assert classification.treatment is Treatment.ANONYMIZE


# -- treatment selection --------------------------------------------------- #


@pytest.mark.parametrize(
    ("resource_id", "treatment"),
    [
        ("account_5", Treatment.DELETE),
        ("search_5", Treatment.DELETE),
        ("cache_5", Treatment.DELETE),
        ("export_5", Treatment.DELETE),
        ("marketing_5", Treatment.DELETE),
        ("tombstone_5", Treatment.REDACT),
        ("audit_record_5", Treatment.RETAIN),
        ("legal_hold_5", Treatment.RETAIN),
        ("backup_5", Treatment.BLOCK_RESTORE),
        ("data_flow_5", Treatment.DISABLE_FLOW),
    ],
)
def test_treatment_per_prefix(resource_id: str, treatment: Treatment) -> None:
    assert book().classify(state(resource_id)).treatment is treatment


def test_a_backup_is_retained_but_its_restore_path_is_blocked() -> None:
    """``rule_14`` says RETAIN with the condition "restore access can be blocked"."""
    classification = book().classify(state("backup_5"))
    assert classification.rule_id == "rule_14"
    assert classification.treatment is Treatment.BLOCK_RESTORE


def test_a_security_audit_log_is_retained_outright() -> None:
    classification = book().classify(state("audit_record_5"))
    assert classification.rule_id == "rule_12"
    assert classification.treatment is Treatment.RETAIN


def test_a_data_flow_has_no_rule_because_it_is_a_process() -> None:
    classification = book().classify(state("data_flow_5"))
    assert classification.rule_id == ""
    assert classification.treatment is Treatment.DISABLE_FLOW
