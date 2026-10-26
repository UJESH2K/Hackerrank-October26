"""Dispatch each evidence artefact to the parser for its format."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from audit.evidence.context import ParseContext, ParseResult
from audit.evidence.document_parser import parse_document
from audit.evidence.json_parser import merge_duplicates, parse_json
from audit.evidence.log_parser import parse_log
from audit.evidence.sql_parser import parse_sql
from audit.evidence.text_parser import parse_text
from audit.llm import LlmGateway
from audit.loaders import Dataset
from audit.models import EvidenceFile

Parser = Callable[[ParseContext], ParseResult]

#: File extension -> parser. Documents are handled separately because they take
#: the cache directory as well.
PARSERS: dict[str, Parser] = {
    ".json": parse_json,
    ".sql": parse_sql,
    ".log": parse_log,
    ".txt": parse_text,
}

#: Extensions handled by the document parser.
DOCUMENT_SUFFIXES: frozenset[str] = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".webp"})


def parse_evidence_file(
    evidence_file: EvidenceFile,
    path: Path,
    cache_dir: Path | None = None,
    gateway: LlmGateway | None = None,
    prompts_dir: Path | None = None,
    aliases: Mapping[str, str] | None = None,
) -> ParseResult:
    """Parse one artefact, choosing the parser by file extension."""
    context = ParseContext(
        path=path,
        evidence_id=evidence_file.file_id,
        captured_at=evidence_file.captured_at,
        origin=evidence_file.origin,
    )
    suffix = path.suffix.lower()
    if suffix in DOCUMENT_SUFFIXES:
        return parse_document(context, cache_dir, gateway, prompts_dir, aliases)
    parser = PARSERS.get(suffix)
    if parser is None:
        result = ParseResult()
        result.warnings.append(f"{path.name}: no parser for extension {suffix!r}")
        return result
    if not path.is_file():
        result = ParseResult()
        result.warnings.append(f"{path.name}: file missing on disk")
        return result
    return parser(context)


def parse_all(
    dataset: Dataset,
    cache_dir: Path | None = None,
    evidence_files: Iterable[EvidenceFile] | None = None,
    gateway: LlmGateway | None = None,
    prompts_dir: Path | None = None,
    aliases: Mapping[str, str] | None = None,
) -> ParseResult:
    """Parse every artefact in the manifest into one result.

    Duplicate observations - the same fact reached by two paths through a file -
    are merged once at the end rather than per parser.
    """
    combined = ParseResult()
    for evidence_file in evidence_files if evidence_files is not None else dataset.evidence_files:
        combined.extend(
            parse_evidence_file(
                evidence_file,
                dataset.evidence_path(evidence_file),
                cache_dir,
                gateway,
                prompts_dir,
                aliases,
            )
        )
    combined.observations = merge_duplicates(combined.observations)
    return combined
