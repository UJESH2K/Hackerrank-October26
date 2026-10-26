"""Tests for identity resolution, including the look-alike email trap."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from audit.identity import MatchKind, build_resolver
from audit.loaders import Dataset
from audit.models import AuditEntry, Identity


def at(day: int) -> datetime:
    """A 2026 timestamp for fixtures."""
    return datetime(2026, 1, day, 12, tzinfo=UTC)


SOFIA = Identity(
    user_id="user_28",
    name="Sofia Martinez",
    email="smartinez6461@gmail.com",
    phone="+34 744 502 860",
    email_hash="5951510123a31517eee05595926fe7c9784e0f7fc16bb3a913b5768375486ea1",
    updated_at=at(1),
)
#: A different person with a confusingly similar address. Both are real rows of
#: ``user_identities.csv``, and sample case_28's request text warns about exactly
#: this: "a search result with a similar email belongs to someone else".
OTHER_MARTINEZ = Identity(
    user_id="user_652",
    name="Mateo Martinez",
    email="mmartinez7917@gmail.com",
    phone="+34 744 502 861",
    email_hash="a" * 64,
    updated_at=at(1),
)
MOVED = Identity(
    user_id="user_10",
    name="Noah Fischer",
    email="noah.new@outlook.com",
    phone="+49 151 23456789",
    email_hash="b" * 64,
    updated_at=at(5),
)

EMAIL_CHANGE = AuditEntry(
    audit_id="audit_77",
    user_id="user_10",
    event_time=at(4),
    field_name="email",
    old_value="noah.old@outlook.com",
    new_value="noah.new@outlook.com",
)
HASH_CHANGE = AuditEntry(
    audit_id="audit_78",
    user_id="user_10",
    event_time=at(4),
    field_name="email_hash",
    old_value="c" * 64,
    new_value="b" * 64,
)
PHONE_CHANGE = AuditEntry(
    audit_id="audit_79",
    user_id="user_10",
    event_time=at(3),
    field_name="phone",
    old_value="+49 151 00000000",
    new_value="+49 151 23456789",
)


def dataset() -> Dataset:
    """A dataset holding only identities and the audit log."""
    return Dataset(
        dataset_dir=Path("dataset"),
        requests=(),
        identities=(SOFIA, OTHER_MARTINEZ, MOVED),
        audit_log=(PHONE_CHANGE, EMAIL_CHANGE, HASH_CHANGE),
        activities=(),
        rules=(),
        evidence_files=(),
    )


def test_current_email_resolves() -> None:
    subject = build_resolver(dataset()).resolve("smartinez6461@gmail.com")
    assert subject is not None
    assert subject.user_id == "user_28"
    assert subject.match_kind is MatchKind.CURRENT


def test_resolution_is_case_insensitive() -> None:
    subject = build_resolver(dataset()).resolve("SMartinez6461@GMAIL.com")
    assert subject is not None
    assert subject.user_id == "user_28"


def test_historical_email_resolves_to_its_former_owner() -> None:
    """The request email may be an address the user abandoned years ago."""
    subject = build_resolver(dataset()).resolve("noah.old@outlook.com")
    assert subject is not None
    assert subject.user_id == "user_10"
    assert subject.match_kind is MatchKind.HISTORICAL


def test_current_snapshot_wins_over_the_audit_log() -> None:
    """If an address is somebody's live email, that is who filed the request."""
    subject = build_resolver(dataset()).resolve("noah.new@outlook.com")
    assert subject is not None
    assert subject.match_kind is MatchKind.CURRENT


def test_a_look_alike_email_is_never_matched() -> None:
    """Exact matching only: no fuzzy matching of similar addresses."""
    resolver = build_resolver(dataset())
    subject = resolver.resolve("smartinez6461@gmail.com")
    assert subject is not None
    assert "mmartinez7917@gmail.com" not in subject.keys
    assert resolver.owners("mmartinez7917@gmail.com") == {"user_652"}


def test_a_near_miss_address_does_not_resolve_at_all() -> None:
    """One character out is a different person, or nobody."""
    resolver = build_resolver(dataset())
    assert resolver.resolve("smartinez6462@gmail.com") is None
    assert resolver.resolve("smartinez6461@gmai.com") is None


def test_names_are_never_used_to_resolve() -> None:
    """Two Martinezes exist; a name is not an identifier."""
    resolver = build_resolver(dataset())
    assert resolver.resolve("Sofia Martinez") is None


def test_an_unknown_email_resolves_to_nothing() -> None:
    assert build_resolver(dataset()).resolve("stranger@example.com") is None


def test_identifier_set_covers_current_and_historical_values() -> None:
    subject = build_resolver(dataset()).resolve("noah.old@outlook.com")
    assert subject is not None
    assert subject.keys == {
        "user_10",
        "noah.new@outlook.com",
        "noah.old@outlook.com",
        "+4915123456789",
        "+4915100000000",
        "b" * 64,
        "c" * 64,
    }


def test_phone_identifiers_are_digit_normalised() -> None:
    """Ingestion stores phones as digits, so the identifier set must match."""
    subject = build_resolver(dataset()).resolve("smartinez6461@gmail.com")
    assert subject is not None
    assert "+34744502860" in subject.keys


def test_historical_values_carry_the_audit_id_that_introduced_them() -> None:
    """A finding that relies on an old identifier can cite the audit row."""
    subject = build_resolver(dataset()).resolve("noah.old@outlook.com")
    assert subject is not None
    assert subject.supporting_ids_for("noah.old@outlook.com") == ("audit_77",)
    assert subject.supporting_ids_for("+4915100000000") == ("audit_79",)
    assert subject.supporting_ids_for("noah.new@outlook.com") == ("audit_77",)


def test_historical_keys_exclude_the_current_ones() -> None:
    subject = build_resolver(dataset()).resolve("noah.old@outlook.com")
    assert subject is not None
    assert "noah.old@outlook.com" in subject.historical_keys
    assert "noah.new@outlook.com" not in subject.historical_keys


def test_user_id_is_always_an_identifier() -> None:
    """It survives a tombstone, which is why the policy lets it stay."""
    subject = build_resolver(dataset()).resolve("smartinez6461@gmail.com")
    assert subject is not None
    assert "user_28" in subject.keys


def test_ownership_detects_a_key_belonging_to_someone_else() -> None:
    resolver = build_resolver(dataset())
    assert resolver.is_owned_by_other(frozenset({"mmartinez7917@gmail.com"}), "user_28")
    assert not resolver.is_owned_by_other(frozenset({"smartinez6461@gmail.com"}), "user_28")


def test_an_unknown_key_is_not_owned_by_anyone() -> None:
    resolver = build_resolver(dataset())
    assert not resolver.is_owned_by_other(frozenset({"nobody@example.com"}), "user_28")
