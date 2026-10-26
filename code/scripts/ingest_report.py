"""Run evidence ingestion over the real dataset and report what it produced.

Read-only diagnostics, not imported by the pipeline. Writes
``code/evaluation/ingestion_facts.md`` and prints a summary.

The last section is the one that matters: for every evidence id cited by
``dataset/sample_output.csv``, it checks that ingestion actually produced the
resource that the sample answer cites it for. A cited resource that ingestion
never saw is a hole in a parser, not a judgement call.

Run from the repository root::

    python code/scripts/ingest_report.py
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CODE_DIR.parent

if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from audit.evidence.index import build_index  # noqa: E402
from audit.evidence.registry import parse_all  # noqa: E402
from audit.loaders import load_dataset  # noqa: E402

OUTPUT_FILE = CODE_DIR / "evaluation" / "ingestion_facts.md"
CACHE_DIR = CODE_DIR / ".cache"


def table(headers: tuple[str, ...], rows: list[tuple[object, ...]]) -> list[str]:
    """Format a markdown table."""
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    lines.extend("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
    return lines


def main() -> int:
    """Ingest everything, write the report, and fail loudly on a parser hole."""
    dataset = load_dataset(REPO_ROOT / "dataset")
    result = parse_all(dataset, CACHE_DIR)
    index = build_index(dataset, result.observations, result.coverage)

    observations = result.observations
    by_evidence: dict[str, list] = defaultdict(list)
    for observation in observations:
        by_evidence[observation.evidence_id].append(observation)

    manifest = {evidence.file_id: evidence for evidence in dataset.evidence_files}
    lines = [
        "# Ingestion facts",
        "",
        "Produced by `python code/scripts/ingest_report.py` (read-only).",
        "",
        "## Totals",
        "",
        f"- evidence files in manifest: **{len(dataset.evidence_files)}**",
        f"- files that produced at least one observation: "
        f"**{sum(1 for ident in by_evidence if by_evidence[ident])}**",
        f"- observations: **{len(observations)}**",
        f"- coverage facts: **{len(result.coverage)}**",
        f"- distinct resource ids: **{len({o.resource_id for o in observations})}**",
        f"- observations with at least one link key: "
        f"**{sum(1 for o in observations if o.link_keys)}**",
        f"- distinct link keys: **{len(index.by_link_key)}**",
        f"- parse warnings: **{len(result.warnings)}**",
        "",
    ]

    lines.append("## Observations per system")
    lines.append("")
    per_system = Counter(observation.system_id for observation in observations)
    present_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for observation in observations:
        label = {True: "present", False: "absent", None: "unknown"}[observation.present]
        present_counts[observation.system_id][label] += 1
    lines.extend(
        table(
            ("system_id", "observations", "present", "absent", "unknown"),
            [
                (
                    system_id or "(unmapped)",
                    count,
                    present_counts[system_id]["present"],
                    present_counts[system_id]["absent"],
                    present_counts[system_id]["unknown"],
                )
                for system_id, count in per_system.most_common()
            ],
        )
    )
    lines.append("")

    lines.append("## Observations per copy label")
    lines.append("")
    per_copy = Counter(observation.copy_label for observation in observations)
    lines.extend(
        table(
            ("copy_label", "observations"),
            [(label, count) for label, count in per_copy.most_common()],
        )
    )
    lines.append("")

    lines.append("## Observations per evidence file")
    lines.append("")
    rows: list[tuple[object, ...]] = []
    for evidence in dataset.evidence_files:
        found = by_evidence.get(evidence.file_id, [])
        coverage = sum(1 for fact in result.coverage if fact.evidence_id == evidence.file_id)
        rows.append(
            (
                evidence.file_id,
                Path(evidence.file_path).suffix.lstrip("."),
                Path(evidence.file_path).name,
                len(found),
                len({item.resource_id for item in found}),
                sum(1 for item in found if item.link_keys),
                coverage,
            )
        )
    lines.extend(
        table(
            (
                "file_id",
                "type",
                "file",
                "observations",
                "resources",
                "with link key",
                "coverage facts",
            ),
            rows,
        )
    )
    lines.append("")

    empty = [
        evidence.file_id
        for evidence in dataset.evidence_files
        if not by_evidence.get(evidence.file_id)
    ]
    lines.append("## Files that produced no observations")
    lines.append("")
    if empty:
        for file_id in empty:
            evidence = manifest[file_id]
            lines.append(f"- `{file_id}` {Path(evidence.file_path).name} ({evidence.origin})")
    else:
        lines.append("None.")
    lines.append("")

    lines.append("## Parse warnings")
    lines.append("")
    if result.warnings:
        for warning in result.warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("None.")
    lines.append("")

    # -- the real check: can ingestion reach every resource the samples cite? --
    sample_path = REPO_ROOT / "dataset" / "sample_output.csv"
    with sample_path.open(encoding="utf-8", newline="") as handle:
        sample_rows = list(csv.DictReader(handle))

    cited: list[tuple[str, str, str, str, str]] = []
    for row in sample_rows:
        for step in json.loads(row["steps_json"]):
            if step["evidence_id"]:
                cited.append(
                    (
                        row["case_id"],
                        step["evidence_id"],
                        step["system_id"],
                        step["resource_id"],
                        step["operation"],
                    )
                )

    document_suffixes = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
    graded: list[tuple[str, str, str, str, str, str]] = []
    for case_id, evidence_id, system_id, resource_id, operation in cited:
        found = by_evidence.get(evidence_id, [])
        if resource_id in {item.resource_id for item in found}:
            verdict = "observation"
        elif any(
            fact.evidence_id == evidence_id and fact.system_id == system_id
            for fact in result.coverage
        ):
            # The file enumerated the resource's system and did not contain the
            # resource: that is how absence is proved, so there is nothing to
            # observe. Correct for a VERIFY_RECORD_ABSENT citation.
            verdict = "coverage_absence"
        elif (
            Path(manifest[evidence_id].file_path).suffix.lower() in document_suffixes
        ):
            verdict = "pending_document"
        else:
            verdict = "MISS"
        graded.append((case_id, evidence_id, system_id, resource_id, operation, verdict))

    verdicts = Counter(item[5] for item in graded)
    misses = [item for item in graded if item[5] == "MISS"]
    mismatched = [
        item
        for item in graded
        if item[5] == "coverage_absence" and item[4] != "VERIFY_RECORD_ABSENT"
    ]

    lines.append("## Reachability of resources cited by `sample_output.csv`")
    lines.append("")
    lines.append(
        f"{len(cited)} cited (case, evidence, resource) triples, graded by how "
        "ingestion reaches them:"
    )
    lines.append("")
    lines.extend(
        table(
            ("verdict", "citations", "meaning"),
            [
                (
                    "observation",
                    verdicts["observation"],
                    "the resource was parsed out of the cited file",
                ),
                (
                    "coverage_absence",
                    verdicts["coverage_absence"],
                    "the file enumerated the system without the resource, proving absence",
                ),
                (
                    "pending_document",
                    verdicts["pending_document"],
                    "a PDF or screenshot awaiting the model extraction step",
                ),
                ("MISS", verdicts["MISS"], "a hole in a parser"),
            ],
        )
    )
    lines.append("")
    if misses:
        lines.append("### Misses")
        lines.append("")
        lines.extend(
            table(
                ("case_id", "evidence_id", "system_id", "resource_id", "operation"),
                [item[:5] for item in misses],
            )
        )
        lines.append("")
    if mismatched:
        lines.append("### Coverage-absence citations whose operation is not an absence check")
        lines.append("")
        lines.append(
            "These need review: the file proves absence but the answer asserts "
            "something else."
        )
        lines.append("")
        lines.extend(
            table(
                ("case_id", "evidence_id", "system_id", "resource_id", "operation"),
                [item[:5] for item in mismatched],
            )
        )
        lines.append("")
    if verdicts["pending_document"]:
        lines.append("### Citations deferred to document extraction")
        lines.append("")
        lines.extend(
            table(
                ("case_id", "evidence_id", "resource_id", "operation", "file"),
                [
                    (
                        item[0],
                        item[1],
                        item[3],
                        item[4],
                        Path(manifest[item[1]].file_path).name,
                    )
                    for item in graded
                    if item[5] == "pending_document"
                ],
            )
        )
        lines.append("")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    print(f"observations        {len(observations)}")
    print(f"coverage facts      {len(result.coverage)}")
    print(f"distinct resources  {len({o.resource_id for o in observations})}")
    print(f"with link key       {sum(1 for o in observations if o.link_keys)}")
    print(f"distinct link keys  {len(index.by_link_key)}")
    print(f"warnings            {len(result.warnings)}")
    print(f"files with 0 obs    {len(empty)}  ({len(empty) - 1} documents + 1 empty snapshot)")
    print(f"sample citations    {len(cited)}")
    for verdict in ("observation", "coverage_absence", "pending_document", "MISS"):
        print(f"  {verdict:18} {verdicts[verdict]}")
    for miss in misses[:20]:
        print(f"  MISS {miss[:5]}")
    for item in mismatched[:20]:
        print(f"  REVIEW coverage-absence cited for {item[4]}: {item[:4]}")
    print(f"wrote {OUTPUT_FILE.relative_to(REPO_ROOT)}")
    return 1 if misses or result.warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
