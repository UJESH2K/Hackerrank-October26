"""Resolve a request email to the subject of the deletion request.

A request's email may be the user's current address or one they used years ago,
so resolution looks at ``user_identities.csv`` first and ``user_audit_log.csv``
second. The result is the subject's ``user_id`` plus every identifier value that
could appear in an older system: current email, phone and email hash, and both
sides of every historical change.

Matching is **exact**, after the same normalisation ingestion uses. There is no
fuzzy matching of names or look-alike addresses, because a look-alike address in
this dataset belongs to a different person (see decision D16).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from audit.evidence.fields import is_nullish, normalise_link_key
from audit.loaders import Dataset
from audit.models import Identity

#: Identity fields whose historical values are tracked in the audit log.
TRACKED_FIELDS: frozenset[str] = frozenset({"email", "phone", "email_hash"})


class MatchKind(StrEnum):
    """How a request email matched the subject."""

    CURRENT = "current"
    HISTORICAL = "historical"


@dataclass(frozen=True, slots=True)
class IdentifierSource:
    """Where one identifier value came from.

    Attributes:
        value: the normalised identifier.
        field_name: which identity field it is.
        origin: ``current`` for the live snapshot, ``historical`` for the audit log.
        supporting_id: the ``audit_id`` that introduced a historical value; empty
            for a current one.
        at: when the value was recorded, for ordering.
    """

    value: str
    field_name: str
    origin: MatchKind
    supporting_id: str = ""
    at: datetime | None = None


@dataclass(frozen=True)
class Subject:
    """The person a deletion request is about.

    Attributes:
        user_id: the stable internal reference. It survives an account tombstone,
            which is exactly why the policy permits keeping it.
        identity: the current identity snapshot.
        matched_on: the normalised value the request email matched.
        match_kind: whether that was the current or a historical address.
        keys: every identifier value that can link a record to this person,
            including ``user_id`` itself.
        sources: provenance for each key, so an attribution can cite the
            ``audit_id`` that justified using a historical identifier.
    """

    user_id: str
    identity: Identity
    matched_on: str
    match_kind: MatchKind
    keys: frozenset[str] = frozenset()
    sources: tuple[IdentifierSource, ...] = ()

    def supporting_ids_for(self, key: str) -> tuple[str, ...]:
        """The audit ids that introduced an identifier, if it is historical."""
        return tuple(
            source.supporting_id
            for source in self.sources
            if source.value == key and source.supporting_id
        )

    @property
    def historical_keys(self) -> frozenset[str]:
        """Identifiers the user no longer uses."""
        current = {
            source.value for source in self.sources if source.origin is MatchKind.CURRENT
        }
        return frozenset(self.keys - current)


@dataclass(frozen=True, slots=True)
class IdentityResolver:
    """Resolves request emails, and maps identifiers back to their owner.

    Attributes:
        identities: ``user_id`` -> current snapshot.
        current_emails: normalised current email -> ``user_id``.
        historical_emails: normalised historical email -> ``user_id`` values.
        owner_of_key: any normalised identifier -> the ``user_id`` values holding
            it. A key held by two users is ambiguous and is never used to
            attribute a resource.
    """

    identities: dict[str, Identity]
    current_emails: dict[str, str]
    historical_emails: dict[str, set[str]]
    owner_of_key: dict[str, set[str]]
    _changes: dict[str, tuple[IdentifierSource, ...]] = field(default_factory=dict)

    def resolve(self, email: str) -> Subject | None:
        """Resolve a request email to its subject, or ``None`` if unknown.

        The current snapshot wins over the audit log: if an address is somebody's
        live email, that is who filed the request.
        """
        key = normalise_link_key(email)
        if not key:
            return None
        user_id = self.current_emails.get(key)
        match_kind = MatchKind.CURRENT
        if user_id is None:
            owners = self.historical_emails.get(key, set())
            if len(owners) != 1:
                # Unknown, or ambiguous between two people: refuse to guess.
                return None
            user_id = next(iter(owners))
            match_kind = MatchKind.HISTORICAL
        identity = self.identities.get(user_id)
        if identity is None:
            return None
        sources = self._sources_for(user_id)
        return Subject(
            user_id=user_id,
            identity=identity,
            matched_on=key,
            match_kind=match_kind,
            keys=frozenset(source.value for source in sources),
            sources=sources,
        )

    def _sources_for(self, user_id: str) -> tuple[IdentifierSource, ...]:
        """Every identifier of a user, current and historical, with provenance."""
        identity = self.identities[user_id]
        sources: list[IdentifierSource] = [
            IdentifierSource(
                value=normalise_link_key(user_id),
                field_name="user_id",
                origin=MatchKind.CURRENT,
            )
        ]
        for field_name, raw in (
            ("email", identity.email),
            ("phone", identity.phone),
            ("email_hash", identity.email_hash),
        ):
            if is_nullish(raw):
                continue
            sources.append(
                IdentifierSource(
                    value=normalise_link_key(raw),
                    field_name=field_name,
                    origin=MatchKind.CURRENT,
                    at=identity.updated_at,
                )
            )
        sources.extend(self._changes.get(user_id, ()))
        seen: set[tuple[str, str]] = set()
        unique: list[IdentifierSource] = []
        for source in sources:
            if not source.value:
                continue
            marker = (source.value, source.supporting_id)
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(source)
        return tuple(unique)

    def owners(self, key: str) -> set[str]:
        """The users holding an identifier, normalising the lookup first."""
        return self.owner_of_key.get(normalise_link_key(key), set())

    def is_owned_by_other(self, keys: frozenset[str], user_id: str) -> bool:
        """Whether any identifier belongs unambiguously to a different user."""
        for key in keys:
            holders = self.owner_of_key.get(key, set())
            if holders and user_id not in holders:
                return True
        return False


def build_resolver(dataset: Dataset) -> IdentityResolver:
    """Index the identity snapshot and the audit log for resolution."""
    identities = {identity.user_id: identity for identity in dataset.identities}
    current_emails: dict[str, str] = {}
    owner_of_key: dict[str, set[str]] = defaultdict(set)

    for identity in dataset.identities:
        email_key = normalise_link_key(identity.email)
        if email_key:
            current_emails[email_key] = identity.user_id
        for raw in (identity.user_id, identity.email, identity.phone, identity.email_hash):
            if is_nullish(raw):
                continue
            owner_of_key[normalise_link_key(raw)].add(identity.user_id)

    historical_emails: dict[str, set[str]] = defaultdict(set)
    changes: dict[str, list[IdentifierSource]] = defaultdict(list)
    for entry in dataset.audit_log:
        if entry.field_name not in TRACKED_FIELDS:
            continue
        for raw in (entry.old_value, entry.new_value):
            if is_nullish(raw):
                continue
            key = normalise_link_key(raw)
            owner_of_key[key].add(entry.user_id)
            if entry.field_name == "email":
                historical_emails[key].add(entry.user_id)
            changes[entry.user_id].append(
                IdentifierSource(
                    value=key,
                    field_name=entry.field_name,
                    origin=MatchKind.HISTORICAL,
                    supporting_id=entry.audit_id,
                    at=entry.event_time,
                )
            )

    return IdentityResolver(
        identities=identities,
        current_emails=current_emails,
        historical_emails={key: set(value) for key, value in historical_emails.items()},
        owner_of_key={key: set(value) for key, value in owner_of_key.items()},
        _changes={user: tuple(value) for user, value in changes.items()},
    )
