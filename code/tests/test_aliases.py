"""Tests for the declared-alias map built from the data-catalog rows."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from audit.aliases import ALIAS_DECLARATION, build_alias_map, normalise_alias, resolve_alias
from audit.loaders import Dataset, load_dataset
from audit.models import Activity

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def activity(
    activity_id: str, details: str, event_type: str = "LINK_RESOURCE_IDENTITY"
) -> Activity:
    """One activity row fixture."""
    return Activity(
        activity_id=activity_id,
        event_time=datetime(2026, 9, 1, tzinfo=UTC),
        event_type=event_type,
        source_system="data_catalog",
        target_system="export_service",
        resource_id="export_1842",
        reported_result="SUCCESS",
        details=details,
    )


def dataset_with(*rows: Activity) -> Dataset:
    """A dataset holding only activity rows."""
    return Dataset(
        dataset_dir=Path("dataset"),
        requests=(),
        identities=(),
        audit_log=(),
        activities=rows,
        rules=(),
        evidence_files=(),
    )


def test_the_declaration_pattern_reads_resource_and_alias() -> None:
    match = ALIAS_DECLARATION.search(
        "Data catalog linked export_1842 (NST-2026-0718-1842) to a@b.com and user_18."
    )
    assert match is not None
    assert match.group("resource_id") == "export_1842"
    assert match.group("alias") == "NST-2026-0718-1842"


def test_a_business_reference_maps_to_its_resource_id() -> None:
    aliases = build_alias_map(
        dataset_with(
            activity(
                "activity_1",
                "Data catalog linked export_1842 (NST-2026-0718-1842) to a@b.com "
                "and user_18.",
            )
        )
    )
    assert resolve_alias("NST-2026-0718-1842", aliases) == "export_1842"


def test_an_attachment_file_name_maps_to_its_object() -> None:
    aliases = build_alias_map(
        dataset_with(
            activity(
                "activity_2",
                "Attachment catalog confirmed object_19 "
                "(delivery_confirmation_19.pdf) belongs only to a@b.com and user_9; "
                "no other subject is linked.",
                event_type="LINK_RESOURCE_IDENTITY",
            )
        )
    )
    assert resolve_alias("delivery_confirmation_19.pdf", aliases) == "object_19"


def test_a_user_reference_maps_to_its_tombstone() -> None:
    aliases = build_alias_map(
        dataset_with(
            activity(
                "activity_3",
                "Data catalog linked tombstone_17 (user_17) to a@b.com and user_17.",
            )
        )
    )
    assert resolve_alias("user_17", aliases) == "tombstone_17"


def test_a_self_alias_carries_no_information() -> None:
    """``account_5762 (account_5762)`` tells us nothing, so it is skipped."""
    aliases = build_alias_map(
        dataset_with(
            activity(
                "activity_4",
                "Data catalog linked account_5762 (account_5762) to a@b.com and user_1.",
            )
        )
    )
    assert aliases == {}


def test_a_row_that_is_not_a_catalog_row_declares_nothing() -> None:
    aliases = build_alias_map(
        dataset_with(
            activity(
                "activity_5",
                "Deletion worker submitted primary profile account_9 (account_9) to "
                "account_service.",
                event_type="DELETE_RECORD",
            )
        )
    )
    assert aliases == {}


def test_an_unknown_prefix_is_not_accepted_as_a_resource() -> None:
    aliases = build_alias_map(
        dataset_with(
            activity(
                "activity_6",
                "Data catalog linked widget_5 (WID-5) to a@b.com and user_1.",
            )
        )
    )
    assert aliases == {}


def test_an_undeclared_reference_resolves_to_nothing() -> None:
    """An order number with no declared alias must stay dropped."""
    assert resolve_alias("ORD-20264118", {"user_17": "tombstone_17"}) is None


def test_matching_ignores_case_and_surrounding_space() -> None:
    aliases = {"delivery_confirmation_19.pdf": "object_19"}
    assert resolve_alias("  Delivery_Confirmation_19.PDF ", aliases) == "object_19"
    assert normalise_alias("  A  B ") == "a b"


def test_the_real_dataset_declares_the_three_useful_aliases() -> None:
    """Measured: twelve declarations, of which three are not self-aliases."""
    aliases = build_alias_map(load_dataset(REPO_ROOT / "dataset"))
    assert aliases == {
        "nst-2026-0718-1842": "export_1842",
        "user_17": "tombstone_17",
        "delivery_confirmation_19.pdf": "object_19",
    }
