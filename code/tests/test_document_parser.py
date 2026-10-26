"""Tests for document extraction and its post-validation.

Post-validation is the safety net around a model reading a screenshot. An
invented or malformed resource id would attribute a record to the wrong person,
so anything that does not match the dataset's own id vocabulary is dropped.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fake_llm import FakeClient

from audit.evidence.context import ParseContext
from audit.evidence.document_parser import (
    content_block,
    document_cache_path,
    extract_records,
    observations_from,
    parse_document,
    to_attributes,
)
from audit.llm import LlmGateway, LlmSettings, ModelPricing

CAPTURED_AT = datetime(2026, 9, 12, 7, 30, tzinfo=UTC)
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

SETTINGS = LlmSettings(
    classifier_model="model-cheap",
    vision_model="model-vision",
    prices={"model-vision": ModelPricing(4.0, 20.0)},
)


def context_for(tmp_path: Path, name: str = "screen.png") -> ParseContext:
    """A fixture artefact and the context that parses it."""
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake image bytes")
    return ParseContext(path=path, evidence_id="evidence_58", captured_at=CAPTURED_AT)


# -- post-validation ------------------------------------------------------- #


def test_a_well_formed_record_is_accepted() -> None:
    attributes = to_attributes(
        {
            "resource_id": "support_001",
            "system_id": "support_platform",
            "record_kind": "support ticket",
            "status": "FOUND",
            "present": True,
            "identifiers": {
                "name": "Priya Raman",
                "email": "priya.raman@gmail.com",
                "phone": None,
                "user_reference": "user_07",
            },
            "flags": {"enabled": None, "restore_allowed": None},
            "observed_at": "2026-09-12T07:00:00Z",
        }
    )
    assert attributes is not None
    assert attributes["resource_id"] == "support_001"
    assert attributes["email"] == "priya.raman@gmail.com"
    assert attributes["subject_reference"] == "user_07"
    assert attributes["result"] == "FOUND"
    assert "phone" not in attributes


@pytest.mark.parametrize(
    "resource_id",
    [
        "",
        "null",
        "account",
        "account_",
        "_005",
        "account-005",
        "ACCOUNT_005",
        "widget_7",
        "acc0unt_005",
        "account_005 (partially hidden)",
        "something entirely invented",
    ],
)
def test_an_unusable_resource_id_is_dropped(resource_id: str) -> None:
    """A malformed, unknown-prefix or invented id is never believed."""
    assert to_attributes({"resource_id": resource_id}) is None


def test_a_dropped_record_is_reported_as_a_warning(tmp_path: Path) -> None:
    context = context_for(tmp_path)
    warnings: list[str] = []
    observations = observations_from(
        [{"resource_id": "made_up_thing"}], context, warnings
    )
    assert observations == []
    assert len(warnings) == 1
    assert "unusable resource_id" in warnings[0]


def test_flags_become_the_attributes_the_state_layer_reads() -> None:
    attributes = to_attributes(
        {
            "resource_id": "backup_706",
            "status": "BLOCKED",
            "flags": {
                "restore_allowed": False,
                "retain_until": "2031-12-31T23:59:59Z",
                "legal_hold": True,
            },
        }
    )
    assert attributes is not None
    assert attributes["restore_allowed"] == "false"
    assert attributes["retention_until"] == "2031-12-31T23:59:59Z"
    assert attributes["legal_hold"] == "true"


def test_the_models_own_presence_answer_is_used() -> None:
    context_absent = to_attributes({"resource_id": "account_005", "present": False})
    assert context_absent is not None
    assert context_absent["document_present"] == "false"


# -- observations ---------------------------------------------------------- #


def test_an_observation_carries_the_manifest_evidence_id(tmp_path: Path) -> None:
    context = context_for(tmp_path)
    observations = observations_from(
        [
            {
                "resource_id": "support_001",
                "present": True,
                "identifiers": {"email": "priya.raman@gmail.com"},
            }
        ],
        context,
        [],
    )
    assert len(observations) == 1
    observation = observations[0]
    assert observation.evidence_id == "evidence_58"
    assert observation.system_id == "support_platform"
    assert observation.present is True
    assert observation.link_keys == {"priya.raman@gmail.com"}


def test_a_per_record_capture_time_is_the_most_specific(tmp_path: Path) -> None:
    """A screen row's own "as of" time beats the artefact's header time."""
    context = context_for(tmp_path)
    observations = observations_from(
        [{"resource_id": "account_005", "observed_at": "2026-09-01T10:00:00Z"}],
        context,
        [],
    )
    assert observations[0].observed_at == datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def test_the_manifest_time_is_used_when_the_record_has_none(tmp_path: Path) -> None:
    context = context_for(tmp_path)
    observations = observations_from([{"resource_id": "account_005"}], context, [])
    assert observations[0].observed_at == CAPTURED_AT


def test_an_unparseable_record_time_falls_back(tmp_path: Path) -> None:
    context = context_for(tmp_path)
    observations = observations_from(
        [{"resource_id": "account_005", "observed_at": "last Tuesday"}], context, []
    )
    assert observations[0].observed_at == CAPTURED_AT


# -- content blocks -------------------------------------------------------- #


def test_a_png_becomes_an_image_block(tmp_path: Path) -> None:
    block = content_block(context_for(tmp_path, "screen.png").path)
    assert block is not None
    assert block["type"] == "image"
    assert block["source"]["media_type"] == "image/png"


def test_a_pdf_becomes_a_document_block(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    block = content_block(path)
    assert block is not None
    assert block["type"] == "document"
    assert block["source"]["media_type"] == "application/pdf"


def test_an_unsupported_type_has_no_block(tmp_path: Path) -> None:
    path = tmp_path / "thing.bin"
    path.write_bytes(b"\x00")
    assert content_block(path) is None


# -- extraction ------------------------------------------------------------ #


def gateway(tmp_path: Path, client: FakeClient | None = None, enabled: bool = True) -> LlmGateway:
    """A gateway over a temporary cache."""
    return LlmGateway(
        SETTINGS, cache_dir=tmp_path / "llm", client=client, enabled=enabled
    )


def test_no_model_means_no_records(tmp_path: Path) -> None:
    """Without a key the parser behaves exactly as the deterministic one did."""
    assert extract_records("some text", context_for(tmp_path).path, None) == []
    assert (
        extract_records(
            "some text", context_for(tmp_path).path, gateway(tmp_path, enabled=False)
        )
        == []
    )


def test_extraction_returns_the_models_records(tmp_path: Path) -> None:
    payload = json.dumps(
        {
            "captured_at": "2026-09-12T07:30:00Z",
            "records": [
                {"resource_id": "support_001", "present": True, "status": "FOUND"}
            ],
        }
    )
    client = FakeClient.with_replies(payload)
    context = context_for(tmp_path)
    records = extract_records("", context.path, gateway(tmp_path, client), PROMPTS_DIR)
    assert [record["resource_id"] for record in records] == ["support_001"]
    assert records[0]["artefact_captured_at"] == "2026-09-12T07:30:00Z"


def test_the_artefact_is_sent_as_a_content_block(tmp_path: Path) -> None:
    client = FakeClient.with_replies('{"captured_at": null, "records": []}')
    context = context_for(tmp_path)
    extract_records("", context.path, gateway(tmp_path, client), PROMPTS_DIR)
    blocks = client.messages.requests[0]["messages"][0]["content"]
    assert blocks[0]["type"] == "image"
    assert blocks[1]["type"] == "text"
    assert client.messages.requests[0]["model"] == "model-vision"


def test_the_local_text_layer_is_sent_as_context(tmp_path: Path) -> None:
    client = FakeClient.with_replies('{"captured_at": null, "records": []}')
    context = context_for(tmp_path)
    extract_records(
        "Record Customer Ingrid Dahl", context.path, gateway(tmp_path, client), PROMPTS_DIR
    )
    text = client.messages.requests[0]["messages"][0]["content"][1]["text"]
    assert "Ingrid Dahl" in text
    assert "trust what you can see" in text


def test_extraction_is_cached_by_content_hash(tmp_path: Path) -> None:
    payload = json.dumps(
        {"captured_at": None, "records": [{"resource_id": "support_001"}]}
    )
    client = FakeClient.with_replies(payload)
    context = context_for(tmp_path)
    cache = tmp_path / "cache"

    first = parse_document(context, cache, gateway(tmp_path, client), PROMPTS_DIR)
    assert [item.resource_id for item in first.observations] == ["support_001"]
    assert client.calls == 1

    second = parse_document(context, cache, gateway(tmp_path, client), PROMPTS_DIR)
    assert [item.resource_id for item in second.observations] == ["support_001"]
    assert client.calls == 1

    cached = list((cache / "documents").rglob("*.json"))
    assert len(cached) == 1
    assert cached[0] == document_cache_path(cache, context.path, PROMPTS_DIR)


def test_an_empty_cached_result_is_retried_once_a_model_appears(tmp_path: Path) -> None:
    """A cache written without a key must not block extraction forever."""
    context = context_for(tmp_path)
    cache = tmp_path / "cache"
    first = parse_document(context, cache, gateway(tmp_path, enabled=False), PROMPTS_DIR)
    assert first.observations == []

    payload = json.dumps(
        {"captured_at": None, "records": [{"resource_id": "support_001"}]}
    )
    client = FakeClient.with_replies(payload)
    second = parse_document(context, cache, gateway(tmp_path, client), PROMPTS_DIR)
    assert [item.resource_id for item in second.observations] == ["support_001"]


def test_an_invented_id_from_the_model_never_reaches_an_observation(
    tmp_path: Path,
) -> None:
    payload = json.dumps(
        {
            "captured_at": None,
            "records": [
                {"resource_id": "account_005"},
                {"resource_id": "account_00S"},
                {"resource_id": "the account shown"},
                {"resource_id": "widget_12"},
            ],
        }
    )
    client = FakeClient.with_replies(payload)
    context = context_for(tmp_path)
    result = parse_document(
        context, tmp_path / "cache", gateway(tmp_path, client), PROMPTS_DIR
    )
    assert [item.resource_id for item in result.observations] == ["account_005"]
    assert len(result.warnings) == 3


def test_no_llm_ignores_a_cached_model_record(tmp_path: Path) -> None:
    """``--no-llm`` must not inherit verdicts from an earlier live run."""
    context = context_for(tmp_path)
    cache = tmp_path / "cache"
    entry = document_cache_path(cache, context.path, PROMPTS_DIR)
    entry.parent.mkdir(parents=True)
    entry.write_text(
        json.dumps(
            {"text": "", "records": [{"resource_id": "support_001", "present": True}]}
        ),
        encoding="utf-8",
    )
    disabled = parse_document(context, cache, gateway(tmp_path, enabled=False))
    assert disabled.observations == []

    enabled = parse_document(context, cache, gateway(tmp_path, enabled=True))
    assert [item.resource_id for item in enabled.observations] == ["support_001"]


# -- declared aliases ------------------------------------------------------ #

ALIASES = {
    "user_17": "tombstone_17",
    "delivery_confirmation_19.pdf": "object_19",
    "nst-2026-0718-1842": "export_1842",
}


def test_a_declared_alias_is_resolved_to_its_resource_id() -> None:
    attributes = to_attributes({"resource_id": "user_17"}, ALIASES)
    assert attributes is not None
    assert attributes["resource_id"] == "tombstone_17"
    assert attributes["declared_alias"] == "user_17"


def test_an_attachment_file_name_resolves(tmp_path: Path) -> None:
    context = context_for(tmp_path)
    observations = observations_from(
        [{"resource_id": "delivery_confirmation_19.pdf", "present": True}],
        context,
        [],
        ALIASES,
    )
    assert [item.resource_id for item in observations] == ["object_19"]
    assert observations[0].system_id == "object_storage"


def test_an_undeclared_reference_is_still_dropped(tmp_path: Path) -> None:
    """An order number with no declared alias must not enter the pipeline."""
    warnings: list[str] = []
    observations = observations_from(
        [{"resource_id": "ORD-20264118"}], context_for(tmp_path), warnings, ALIASES
    )
    assert observations == []
    assert len(warnings) == 1


def test_without_an_alias_map_nothing_is_resolved() -> None:
    assert to_attributes({"resource_id": "user_17"}) is None


def test_a_real_resource_id_is_never_rewritten_by_an_alias() -> None:
    attributes = to_attributes({"resource_id": "account_005"}, ALIASES)
    assert attributes is not None
    assert attributes["resource_id"] == "account_005"
    assert "declared_alias" not in attributes


# -- D43: a business date is never the observation time -------------------- #


def test_a_business_date_never_becomes_the_observation_time(tmp_path: Path) -> None:
    """A PDF's "submitted 18 Jun" is not when we observed the record."""
    context = context_for(tmp_path)
    observations = observations_from(
        [
            {
                "resource_id": "support_001",
                "business_date": "2026-06-18T10:24:00Z",
                "identifiers": {"email": "a@b.com"},
            }
        ],
        context,
        [],
    )
    assert observations[0].observed_at == CAPTURED_AT
    assert observations[0].attributes["record_dated_at"] == "2026-06-18T10:24:00Z"


def test_a_capture_time_printed_on_the_artefact_is_preferred(tmp_path: Path) -> None:
    """A screen that prints its own capture time is more specific than the manifest."""
    context = context_for(tmp_path)
    observations = observations_from(
        [
            {
                "resource_id": "support_001",
                "artefact_captured_at": "2026-09-12T07:30:00Z",
                "business_date": "2026-06-18T10:24:00Z",
            }
        ],
        context,
        [],
    )
    assert observations[0].observed_at == datetime(2026, 9, 12, 7, 30, tzinfo=UTC)


def test_an_unparseable_capture_time_falls_back_to_the_manifest(tmp_path: Path) -> None:
    context = context_for(tmp_path)
    observations = observations_from(
        [{"resource_id": "support_001", "artefact_captured_at": "last Tuesday"}],
        context,
        [],
    )
    assert observations[0].observed_at == CAPTURED_AT


def test_the_payload_capture_time_reaches_every_record(tmp_path: Path) -> None:
    payload = json.dumps(
        {
            "captured_at": "2026-09-12T07:30:00Z",
            "records": [
                {"resource_id": "support_001", "business_date": "2026-06-18T10:24:00Z"}
            ],
        }
    )
    client = FakeClient.with_replies(payload)
    context = context_for(tmp_path)
    records = extract_records("", context.path, gateway(tmp_path, client), PROMPTS_DIR)
    assert records[0]["artefact_captured_at"] == "2026-09-12T07:30:00Z"
    assert records[0]["business_date"] == "2026-06-18T10:24:00Z"


# -- D44: a listed record is present unless the artefact says otherwise ---- #


def test_a_listed_record_with_no_stated_status_is_present(tmp_path: Path) -> None:
    """A document is a picture of a system that still holds the record."""
    observations = observations_from(
        [{"resource_id": "support_001", "present": None}], context_for(tmp_path), []
    )
    assert observations[0].present is True


def test_the_models_explicit_false_still_wins(tmp_path: Path) -> None:
    observations = observations_from(
        [{"resource_id": "support_001", "present": False}], context_for(tmp_path), []
    )
    assert observations[0].present is False


@pytest.mark.parametrize(
    "status",
    ["DELETED", "NOT_FOUND", "expired", "unavailable", "removed", "gone"],
)
def test_an_explicit_absence_status_makes_it_absent(tmp_path: Path, status: str) -> None:
    observations = observations_from(
        [{"resource_id": "support_001", "status": status}], context_for(tmp_path), []
    )
    assert observations[0].present is False


@pytest.mark.parametrize("status", ["ACTIVE", "FOUND", "Retained reference", "paid"])
def test_a_non_absence_status_leaves_it_present(tmp_path: Path, status: str) -> None:
    observations = observations_from(
        [{"resource_id": "support_001", "status": status}], context_for(tmp_path), []
    )
    assert observations[0].present is True
