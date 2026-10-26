"""Compare attributed resources with the resources in ``sample_output.csv``.

Reports precision and recall overall and per attribution kind, and lists every
miss with a reason. Run from the repository root::

    python code/evaluation/check_attribution.py

This reads ``sample_output.csv``, so it lives in ``evaluation/`` and is never
imported by the pipeline (decision D6).
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

from audit.attribution import Attributor, LinkKind  # noqa: E402
from audit.evidence.index import build_index  # noqa: E402
from audit.evidence.registry import parse_all  # noqa: E402
from audit.identity import build_resolver  # noqa: E402
from audit.loaders import (  # noqa: E402
    REQUESTS_FILE,
    SAMPLE_REQUESTS_FILE,
    load_dataset,
    load_requests,
)

DATASET_DIR = REPO_ROOT / "dataset"
CACHE_DIR = CODE_DIR / ".cache"

#: Gold resources that appear in no evidence file and no activity row, confirmed
#: by grepping the whole dataset. Listed, never chased. See decision D22.
GREP_CONFIRMED_ABSENT = {
    ("case_18", "audit_record_6645"),
    ("case_20", "audit_record_5569"),
    ("case_04", "account_004"),
    ("case_07", "support_001"),
}


def miss_reason(
    case_id: str,
    resource_id: str,
    seen_in_evidence: set[str],
    seen_in_activity: set[str],
    has_account_time: bool,
) -> str:
    """Explain a miss from what the dataset does and does not contain."""
    in_evidence = resource_id in seen_in_evidence
    in_activity = resource_id in seen_in_activity
    if not in_evidence and not in_activity:
        confirmed = " (grep-confirmed)" if (case_id, resource_id) in GREP_CONFIRMED_ABSENT else ""
        return f"absent from all structured data{confirmed}"
    if not in_evidence:
        return "named only in the activity stream, which is not an evidence source"
    if not has_account_time:
        return "no identifier, and the case has no account time to time it against"
    return "present in evidence but not linked to this subject"


def load_gold() -> dict[str, set[str]]:
    """Gold resource ids per sample case."""
    path = DATASET_DIR / "sample_output.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {
        row["case_id"]: {
            step["resource_id"] for step in json.loads(row["steps_json"])
        }
        for row in rows
    }


def main() -> int:
    """Run attribution over the sample cases and score it."""
    dataset = load_dataset(DATASET_DIR, DATASET_DIR / SAMPLE_REQUESTS_FILE)
    # The timing signature is learned, unsupervised, from every request. Using
    # the full set keeps the learned bands identical between a sample run and a
    # full run.
    learning_requests = load_requests(DATASET_DIR / REQUESTS_FILE)

    parsed = parse_all(dataset, CACHE_DIR)
    index = build_index(dataset, parsed.observations, parsed.coverage)
    resolver = build_resolver(dataset)
    attributor = Attributor(dataset, index, resolver, learning_requests)

    signature = attributor.signature
    bands = sorted(signature.bands)
    print("learned timing signature")
    print(f"  anchors            {signature.sample_size}")
    print(f"  bands (s mod 60)   {bands}")
    dense = sorted(
        (offset, count) for offset, count in signature.histogram.items() if count
    )
    print(f"  histogram          {dense}")
    print(f"  max follow-up      {signature.max_offset_seconds / 3600:.2f} h")
    print(
        "  delay buckets      "
        f"{sorted(signature.offset_histogram.items())}"
    )
    print()

    attributions = attributor.attribute_all()
    gold = load_gold()
    seen_in_evidence = {observation.resource_id for observation in parsed.observations}
    seen_in_activity = {activity.resource_id for activity in dataset.activities}

    true_positive = 0
    false_positive = 0
    false_negative = 0
    per_kind: dict[str, Counter[str]] = defaultdict(Counter)
    misses: list[tuple[str, str, str]] = []
    extras: list[tuple[str, str, str]] = []

    account_correct = 0
    account_wrong = 0
    account_missing = 0
    low_confidence = 0

    for case_id, attribution in sorted(attributions.items()):
        wanted = gold.get(case_id, set())
        predicted = {resource.resource_id: resource for resource in attribution.resources}
        for resource_id, resource in sorted(predicted.items()):
            kind = str(resource.how)
            if resource_id in wanted:
                true_positive += 1
                per_kind[kind]["tp"] += 1
            else:
                false_positive += 1
                per_kind[kind]["fp"] += 1
                extras.append((case_id, resource_id, kind))
        for resource_id in sorted(wanted - set(predicted)):
            false_negative += 1
            misses.append(
                (
                    case_id,
                    resource_id,
                    miss_reason(
                        case_id,
                        resource_id,
                        seen_in_evidence,
                        seen_in_activity,
                        attribution.account_deleted_at is not None,
                    ),
                )
            )

        gold_accounts = {item for item in wanted if item.startswith("account_")}
        predicted_accounts = {
            item for item in predicted if item.startswith("account_")
        }
        if predicted_accounts and predicted_accounts == gold_accounts:
            account_correct += 1
        elif predicted_accounts:
            account_wrong += 1
        else:
            account_missing += 1
        low_confidence += sum(
            1
            for resource in attribution.resources
            if resource.how is LinkKind.WORKFLOW_RUN and resource.confidence < 0.5
        )

    precision = true_positive / (true_positive + false_positive or 1)
    recall = true_positive / (true_positive + false_negative or 1)

    print("resource attribution over 32 sample cases")
    print(f"  gold resources     {true_positive + false_negative}")
    print(f"  attributed         {true_positive + false_positive}")
    print(f"  true positives     {true_positive}")
    print(f"  false positives    {false_positive}")
    print(f"  false negatives    {false_negative}")
    print(f"  precision          {precision:.1%}")
    print(f"  recall             {recall:.1%}")
    print()

    print("per attribution kind")
    print(f"  {'kind':16} {'tp':>4} {'fp':>4} {'precision':>10}")
    for kind in sorted(per_kind):
        counts = per_kind[kind]
        total = counts["tp"] + counts["fp"]
        print(
            f"  {kind:16} {counts['tp']:>4} {counts['fp']:>4} "
            f"{counts['tp'] / (total or 1):>9.1%}"
        )
    print()

    print("primary account assignment")
    print(f"  correct            {account_correct} / 32")
    print(f"  wrong              {account_wrong}")
    print(f"  none assigned      {account_missing}")
    print(f"  low-confidence     {low_confidence}")
    print()

    if misses:
        print(f"misses ({len(misses)})")
        for case_id, resource_id, reason in misses:
            print(f"  {case_id:9} {resource_id:22} {reason}")
        print()
    if extras:
        print(f"attributed but not in gold ({len(extras)})")
        for case_id, resource_id, kind in extras:
            print(f"  {case_id:9} {resource_id:22} {kind}")
        print()

    notes = [
        (case_id, note)
        for case_id, attribution in sorted(attributions.items())
        for note in attribution.notes
        if "unassigned" in note or "fallback" in note
    ]
    if notes:
        print(f"assignment notes ({len(notes)})")
        for case_id, note in notes:
            print(f"  {case_id:9} {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
