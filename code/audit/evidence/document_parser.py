"""Read PDFs and application screenshots into observations.

These are ordinary business artefacts - invoices, retention registers, support
consoles, admin screens - with no machine-readable record structure. This is the
one place in the pipeline where a model is genuinely needed, because there is no
rule that can read a screenshot.

The deterministic half still does as much as possible: the PDF text layer is
extracted locally and sent as extra context, the model's output is validated
against the known resource-id vocabulary before anything is believed, and every
result is cached by file content hash so a re-run costs nothing.

Post-validation is deliberately strict. A model that invents, completes or
"corrects" a resource id would attribute a record to the wrong person, so an id
that does not match a known prefix is dropped rather than repaired.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from audit.aliases import resolve_alias
from audit.evidence.context import ParseContext, ParseResult
from audit.evidence.fields import (
    flatten,
    is_nullish,
    link_keys,
    present_from_attributes,
)
from audit.evidence.systems import (
    DEFAULT_COPY_LABEL,
    RESOURCE_PREFIX_SYSTEM,
    looks_like_resource_id,
    resource_prefix,
    system_for_resource,
)
from audit.llm import PURPOSE_EXTRACT, LlmGateway, load_prompt, prompt_fingerprint
from audit.loaders import parse_timestamp
from audit.models import Observation

#: Extensions this parser claims.
PDF_SUFFIXES: frozenset[str] = frozenset({".pdf"})
IMAGE_SUFFIXES: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp"})

#: Media types for the image blocks the API accepts.
IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

#: The prompt that drives extraction.
PROMPT_FILE = "evidence_extraction.md"

#: Attribute holding the artefact's own capture time, when it prints one.
ARTEFACT_CAPTURED_AT = "artefact_captured_at"

#: Attribute holding a date printed inside the record - submitted, ordered,
#: received. Deliberately not a name the time resolver reads.
RECORD_DATED_AT = "record_dated_at"

#: Attribute holding a per-record capture time, when a screen shows one ("last
#: seen", "as of"). More specific than the artefact's own capture time.
RECORD_CAPTURED_AT = "record_captured_at"

#: How much locally extracted PDF text to include as context.
MAX_TEXT_CONTEXT = 6000

#: Flag names the model reports, mapped to the attribute the state layer reads.
FLAG_ATTRIBUTES: dict[str, str] = {
    "enabled": "enabled",
    "restore_allowed": "restore_allowed",
    "download_available": "download_available",
    "retain_until": "retention_until",
    "legal_hold": "legal_hold",
}

#: Identifier names the model reports, mapped to the attribute names ingestion
#: already understands.
IDENTIFIER_ATTRIBUTES: dict[str, str] = {
    "name": "full_name",
    "email": "email",
    "phone": "phone",
    "user_reference": "subject_reference",
}


def content_hash(path: Path) -> str:
    """SHA-256 of a file's bytes, used as the cache key.

    Hashing content rather than the path means a re-run after an unrelated file
    move still hits the cache, and a changed file never serves a stale entry.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(131072), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_pdf_text(path: Path) -> str:
    """Extract the text layer of a PDF.

    Tries ``pdfplumber`` first because it preserves line structure better, then
    falls back to ``pypdf``. An unreadable or malformed PDF yields an empty
    string rather than raising: the text layer is a hint for the model, so
    losing it must not stop the artefact being read as an image.
    """
    try:
        import pdfplumber
    except ImportError:
        pdfplumber = None  # type: ignore[assignment]
    if pdfplumber is not None:
        try:
            with pdfplumber.open(path) as document:
                return "\n".join(
                    (page.extract_text() or "") for page in document.pages
                )
        except Exception:  # noqa: BLE001 - a bad PDF must not stop ingestion
            pass
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:  # noqa: BLE001 - same reason
        return ""


def extract_text(path: Path) -> str:
    """Extract whatever text the artefact carries locally.

    Screenshots are read by the vision model directly; OCR is deliberately not
    attempted, because no OCR engine is installed and a missing one must not
    change the result.
    """
    if path.suffix.lower() in PDF_SUFFIXES:
        return extract_pdf_text(path)
    return ""


def content_block(path: Path) -> dict[str, Any] | None:
    """The API content block for an artefact: an image or a document."""
    suffix = path.suffix.lower()
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    if suffix in PDF_SUFFIXES:
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": data,
            },
        }
    media_type = IMAGE_MEDIA_TYPES.get(suffix)
    if media_type is None:
        return None
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def _validate_payload(payload: object) -> bool:
    """Whether the model returned the expected object with a records list."""
    if not isinstance(payload, Mapping):
        return False
    records = payload.get("records")
    return isinstance(records, list)


def extract_records(
    text: str,
    image_path: Path | None = None,
    gateway: LlmGateway | None = None,
    prompts_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Turn a document into structured records using the vision model.

    Returns an empty list when no model is available, so a run without a key
    behaves exactly as the deterministic pipeline did.

    Args:
        text: locally extracted text, sent as extra context when present.
        image_path: the artefact to send as an image or document block.
        gateway: the model gateway.
        prompts_dir: where ``evidence_extraction.md`` lives.
    """
    if gateway is None or not gateway.available or image_path is None:
        return []
    if prompts_dir is None:
        prompts_dir = Path(__file__).resolve().parent.parent.parent / "prompts"

    version, prompt = load_prompt(prompts_dir / PROMPT_FILE)
    block = content_block(image_path)
    if block is None:
        return []

    instruction = (
        f"Artefact file name: {image_path.name}\n\n"
        "Transcribe every record visible in this artefact."
    )
    if text.strip():
        instruction += (
            "\n\nText layer extracted locally from the same file, for reference. "
            "Where it disagrees with what you can see, trust what you can see:\n"
            f"{text.strip()[:MAX_TEXT_CONTEXT]}"
        )

    payload = gateway.call_json(
        purpose=PURPOSE_EXTRACT,
        prompt_version=f"{PROMPT_FILE}@{version}",
        system=prompt,
        blocks=[block, {"type": "text", "text": instruction}],
        validator=_validate_payload,
        # Hash the file's content, not its base64, so the key stays small.
        cache_payload={
            "file": image_path.name,
            "sha256": content_hash(image_path),
            "text_hint": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        },
    )
    if payload is None:
        return []
    records = [dict(item) for item in payload.get("records", []) if isinstance(item, Mapping)]
    captured_at = payload.get("captured_at")
    if captured_at and not is_nullish(str(captured_at)):
        for record in records:
            # The artefact's own capture time, kept under a name of its own so it
            # can never be confused with a date printed inside the record.
            record[ARTEFACT_CAPTURED_AT] = str(captured_at)
    return records


def to_attributes(
    record: Mapping[str, Any], aliases: Mapping[str, str] | None = None
) -> dict[str, str] | None:
    """Flatten one model record into observation attributes, or reject it.

    Rejection is the normal outcome for anything doubtful. A resource id is kept
    only when it matches the dataset's own id shape *and* a known prefix, so an
    invented or malformed id never reaches attribution.

    A reference that is not a resource id gets one chance first: if the data
    catalog has *declared* it an alias, it is translated (decision D42). A
    screenshot shows ``user_17`` or ``delivery_confirmation_19.pdf``; the catalog
    says those are ``tombstone_17`` and ``object_19``. A reference with no
    declared alias - an order number, a support case number - stays dropped.
    """
    resource_id = str(record.get("resource_id", "")).strip()
    if not resource_id or is_nullish(resource_id):
        return None

    alias_of = ""
    if not looks_like_resource_id(resource_id) or (
        resource_prefix(resource_id) not in RESOURCE_PREFIX_SYSTEM
    ):
        resolved = resolve_alias(resource_id, aliases or {})
        if resolved is None:
            return None
        alias_of, resource_id = resource_id, resolved

    attributes: dict[str, str] = {"resource_id": resource_id}
    if alias_of:
        attributes["declared_alias"] = alias_of
    for name in ("system_id", "record_kind"):
        value = record.get(name)
        if value is not None and not is_nullish(str(value)):
            attributes["system" if name == "system_id" else name] = str(value).strip()

    # The prompt asks for ``status``; a model sometimes calls it ``result``.
    # Accept either, and expose it as ``result``, which the state layer reads.
    status = record.get("status", record.get("result"))
    if status is not None and not is_nullish(str(status)):
        attributes["status"] = str(status).strip()
        attributes["result"] = attributes["status"]

    # Identifiers belong under ``identifiers``, but a model may put them at the
    # top level. Read both rather than losing a record over its shape.
    identifiers = record.get("identifiers")
    sources: list[Mapping[str, Any]] = [record]
    if isinstance(identifiers, Mapping):
        sources.insert(0, identifiers)
    for source_map in sources:
        for source, target in IDENTIFIER_ATTRIBUTES.items():
            if target in attributes:
                continue
            value = source_map.get(source)
            if value is not None and not is_nullish(str(value)):
                attributes[target] = str(value).strip()

    flags = record.get("flags")
    flag_sources: list[Mapping[str, Any]] = [record]
    if isinstance(flags, Mapping):
        flag_sources.insert(0, flags)
    for source_map in flag_sources:
        for source, target in FLAG_ATTRIBUTES.items():
            if target in attributes:
                continue
            value = source_map.get(source)
            if value is None or is_nullish(str(value)):
                continue
            attributes[target] = flatten({target: value})[target]

    # A date printed inside a document is a property of the record - when the
    # request was submitted, when the order was placed - not when we observed
    # it. It is kept, under a name the time resolver does not read, so it can
    # never become the observation time (decision D43).
    business_date = record.get("business_date")
    if business_date is not None and not is_nullish(str(business_date)):
        attributes[RECORD_DATED_AT] = str(business_date).strip()

    # A per-record capture time, if the screen shows one for the row.
    per_record = record.get("observed_at")
    if per_record is not None and not is_nullish(str(per_record)):
        attributes[RECORD_CAPTURED_AT] = str(per_record).strip()

    for name in (ARTEFACT_CAPTURED_AT, "captured_at"):
        value = record.get(name)
        if value is not None and not is_nullish(str(value)):
            attributes[ARTEFACT_CAPTURED_AT] = str(value).strip()
            break

    present = record.get("present")
    if isinstance(present, bool):
        attributes["document_present"] = "true" if present else "false"
    return attributes


def observation_time(attributes: Mapping[str, str], captured_at: datetime) -> datetime:
    """When a document record was observed.

    A capture time, most specific first: the record's own, then the artefact's,
    then the manifest's. Never a date printed *inside* the record, such as when a
    request was submitted or an order placed (decision D43).
    """
    for name in (RECORD_CAPTURED_AT, ARTEFACT_CAPTURED_AT):
        printed = attributes.get(name, "")
        if printed and not is_nullish(printed):
            try:
                return parse_timestamp(printed)
            except ValueError:
                continue
    return captured_at


def _present_of(attributes: Mapping[str, str]) -> bool:
    """Presence for a document record.

    A record that a document lists under its own resource id is **present**: the
    artefact is a picture of a system that still holds it. Only an explicit
    statement to the contrary - deleted, expired, unavailable, not found - makes
    it absent. So an unknown answer resolves to ``True`` here, unlike the
    structured formats where silence stays unknown (decision D44).
    """
    explicit = attributes.get("document_present", "")
    if explicit == "true":
        return True
    if explicit == "false":
        return False
    stated = present_from_attributes(attributes)
    return True if stated is None else stated


def _cache_path(cache_dir: Path, digest: str, prompt_token: str) -> Path:
    """Where a document's extraction result is cached.

    The extraction prompt's fingerprint is part of the path, so editing that
    prompt re-extracts while editing any *other* prompt does not (decision D41).
    """
    safe = re.sub(r"[^A-Za-z0-9._+@-]", "_", prompt_token)
    return cache_dir / "documents" / safe / f"{digest}.json"


def document_cache_path(
    cache_dir: Path, artefact: Path, prompts_dir: Path | None = None
) -> Path:
    """Where this artefact's extraction result is cached, for a given prompt.

    Public so a caller can inspect or seed the cache without reimplementing the
    naming scheme.
    """
    if prompts_dir is None:
        prompts_dir = Path(__file__).resolve().parent.parent.parent / "prompts"
    return _cache_path(
        cache_dir, content_hash(artefact), prompt_fingerprint(prompts_dir / PROMPT_FILE)
    )


def observations_from(
    records: list[dict[str, Any]],
    context: ParseContext,
    warnings: list[str],
    aliases: Mapping[str, str] | None = None,
) -> list[Observation]:
    """Turn validated model records into observations."""
    observations: list[Observation] = []
    for record in records:
        attributes = to_attributes(record, aliases)
        if attributes is None:
            warnings.append(
                f"{context.path.name}: dropped record with unusable resource_id "
                f"{record.get('resource_id')!r}"
            )
            continue
        resource_id = attributes["resource_id"]
        observed_at = observation_time(attributes, context.captured_at)
        observations.append(
            Observation(
                evidence_id=context.evidence_id,
                observed_at=observed_at,
                system_id=system_for_resource(
                    resource_id, attributes.get("system", "")
                ),
                resource_id=resource_id,
                copy_label=DEFAULT_COPY_LABEL,
                present=_present_of(attributes),
                attributes=dict(sorted(attributes.items())),
                link_keys=frozenset(link_keys(attributes, include_names=True)),
            )
        )
    return observations


def parse_document(
    context: ParseContext,
    cache_dir: Path | None = None,
    gateway: LlmGateway | None = None,
    prompts_dir: Path | None = None,
    aliases: Mapping[str, str] | None = None,
) -> ParseResult:
    """Read a PDF or screenshot into observations.

    The extracted text and the resulting records are cached by content hash, so
    a re-run neither re-reads the PDF nor re-calls the model.
    """
    result = ParseResult()
    # ``--no-llm`` means no model-derived record reaches the pipeline, including
    # one cached by an earlier live run (decision D39). The cached text layer is
    # deterministic, but the records are the model's reading of the artefact.
    if gateway is None or not gateway.enabled:
        return result

    if prompts_dir is None:
        prompts_dir = Path(__file__).resolve().parent.parent.parent / "prompts"
    digest = content_hash(context.path)
    token = prompt_fingerprint(prompts_dir / PROMPT_FILE)
    cached: dict[str, Any] | None = None
    cache_file = _cache_path(cache_dir, digest, token) if cache_dir is not None else None
    if cache_file is not None and cache_file.is_file():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cached = None

    # A cached entry with no records is re-attempted once a model is available:
    # the empty result came from running without one.
    stale = (
        cached is not None
        and not cached.get("records")
        and gateway is not None
        and gateway.available
    )
    if cached is None or stale:
        text = extract_text(context.path)
        records = extract_records(text, context.path, gateway, prompts_dir)
        cached = {"text": text, "records": records}
        if cache_file is not None:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps(cached, ensure_ascii=False, indent=1), encoding="utf-8"
            )

    if not cached.get("text") and context.path.suffix.lower() in PDF_SUFFIXES:
        result.warnings.append(f"{context.path.name}: no extractable text layer")

    result.observations.extend(
        observations_from(
            [item for item in cached.get("records", []) if isinstance(item, dict)],
            context,
            result.warnings,
            aliases,
        )
    )
    return result
