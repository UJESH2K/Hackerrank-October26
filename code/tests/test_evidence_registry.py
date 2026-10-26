"""Tests for parser dispatch and the document extraction seam."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from audit.evidence.context import ParseContext
from audit.evidence.document_parser import (
    content_hash,
    document_cache_path,
    extract_records,
    parse_document,
)
from audit.evidence.registry import parse_evidence_file
from audit.llm import LlmGateway, LlmSettings
from audit.models import EvidenceFile

CAPTURED_AT = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def enabled_gateway(tmp_path: Path) -> LlmGateway:
    """A gateway with the model layer switched on but no client.

    ``parse_document`` reads its cache only when the layer is enabled, so these
    cache tests need one; with ``--no-llm`` no model-derived record is used at
    all (decision D39).
    """
    settings = LlmSettings(
        classifier_model="model-cheap", vision_model="model-vision", prices={}
    )
    return LlmGateway(settings, cache_dir=tmp_path / "llm", enabled=True)


def manifest_row(file_path: str, file_id: str = "evidence_test") -> EvidenceFile:
    """One manifest row for a fixture."""
    return EvidenceFile(
        file_id=file_id,
        file_path=file_path,
        origin="fixture",
        captured_at=CAPTURED_AT,
    )


def test_dispatch_picks_the_parser_by_extension(tmp_path: Path) -> None:
    path = tmp_path / "records.json"
    path.write_text(
        json.dumps(
            {
                "captured_at": "2026-07-18T10:00:00Z",
                "data_store": "account_service",
                "collection": "customer_profiles",
                "records": [{"resource_id": "account_7", "email": "a@b.com"}],
            }
        ),
        encoding="utf-8",
    )
    result = parse_evidence_file(manifest_row("records.json"), path)
    assert [item.resource_id for item in result.observations] == ["account_7"]
    assert result.warnings == []


def test_an_unknown_extension_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "mystery.bin"
    path.write_bytes(b"\x00\x01")
    result = parse_evidence_file(manifest_row("mystery.bin"), path)
    assert result.observations == []
    assert "no parser for extension" in result.warnings[0]


def test_a_missing_file_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "absent.log"
    result = parse_evidence_file(manifest_row("absent.log"), path)
    assert result.observations == []
    assert "file missing on disk" in result.warnings[0]


def test_content_hash_is_stable_and_content_dependent(tmp_path: Path) -> None:
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("same", encoding="utf-8")
    second.write_text("same", encoding="utf-8")
    assert content_hash(first) == content_hash(second)
    second.write_text("different", encoding="utf-8")
    assert content_hash(first) != content_hash(second)


def test_document_extraction_is_a_stub_for_now() -> None:
    """With no gateway the seam yields nothing, so documents add no guesses."""
    assert extract_records("Record Customer Ingrid Dahl", None) == []


def test_document_results_are_cached_by_content_hash(tmp_path: Path) -> None:
    source = tmp_path / "screenshot.png"
    source.write_bytes(b"not really a png")
    cache = tmp_path / "cache"
    context = ParseContext(
        path=source, evidence_id="evidence_test", captured_at=CAPTURED_AT
    )

    gateway = enabled_gateway(tmp_path)
    first = parse_document(context, cache, gateway)
    assert first.observations == []
    cached_files = list((cache / "documents").rglob("*.json"))
    assert len(cached_files) == 1
    assert cached_files[0] == document_cache_path(cache, source)

    # A cached payload is used as-is, so an injected record shows up without
    # re-running extraction.
    cached_files[0].write_text(
        json.dumps(
            {
                "text": "Account admin console",
                "records": [
                    {
                        "resource_id": "account_005",
                        "email": "leila.haddad@yahoo.com",
                        "result": "FOUND",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    second = parse_document(context, cache, gateway)
    assert len(second.observations) == 1
    observation = second.observations[0]
    assert observation.resource_id == "account_005"
    assert observation.system_id == "account_service"
    assert observation.present is True
    assert observation.link_keys == {"leila.haddad@yahoo.com"}
    assert observation.observed_at == CAPTURED_AT


def test_a_pdf_without_a_text_layer_is_reported(tmp_path: Path) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF-1.4 not a real pdf")
    cache = tmp_path / "cache"
    entry = document_cache_path(cache, source)
    entry.parent.mkdir(parents=True)
    entry.write_text(json.dumps({"text": "", "records": []}), encoding="utf-8")
    context = ParseContext(
        path=source, evidence_id="evidence_test", captured_at=CAPTURED_AT
    )
    result = parse_document(context, cache, enabled_gateway(tmp_path))
    assert "no extractable text layer" in result.warnings[0]
