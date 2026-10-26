"""Map a resource to its data category, and a category to the steps it needs.

The required action for each category is read from ``dataset/deletion_rules.csv``
at runtime and never hardcoded: if the policy changes, the behaviour changes with
it.

Category resolution has three tiers, strongest first:

1. **stated in the evidence** - the handover record's category column and the
   ``record_type`` field of several JSON collections carry the category outright,
   in the same vocabulary as the rules file;
2. **record-kind signals** - a support record is an attachment, a legacy contact
   copy or a conversation depending on what the evidence shows about it;
3. **the resource prefix** - a last resort that is right for the unambiguous
   systems.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from audit.evidence.fields import is_nullish
from audit.loaders import Dataset
from audit.models import Operation, RequiredAction, Rule
from audit.state import ResourceState

#: ``resource_id`` prefix -> data category, for prefixes that admit one reading.
PREFIX_CATEGORY: dict[str, str] = {
    "account": "USER_PROFILE",
    "search": "SEARCH_INDEX_ENTRY",
    "cache": "CACHE_ENTRY",
    "export": "USER_EXPORT",
    "marketing": "MARKETING_PROFILE",
    "audit_record": "SECURITY_AUDIT_LOG",
    "legal_hold": "LEGAL_HOLD_RECORD",
    "backup": "BACKUP_ARCHIVE",
    "tombstone": "ACCOUNT_TOMBSTONE",
    "email_delivery": "EMAIL_DELIVERY_LOG",
    "object": "SUPPORT_ATTACHMENT",
}

#: The pseudo-category for a data flow. ``deletion_rules.csv`` has no row for it
#: because a flow is a process, not a record: it is disabled, not deleted.
DATA_FLOW_CATEGORY = "DATA_FLOW"

#: Collections that hold support attachments rather than conversations.
ATTACHMENT_COLLECTIONS: frozenset[str] = frozenset({"ticket_attachments"})

#: Storage classes that mark an attachment.
ATTACHMENT_STORAGE_CLASSES: frozenset[str] = frozenset({"ticket_attachment"})

#: Events that scan an attachment rather than a message body.
ATTACHMENT_EVENTS: frozenset[str] = frozenset({"attachment_pii_scan", "thumbnail_failed"})

#: Attributes that only an attachment record carries.
ATTACHMENT_FIELDS: tuple[str, ...] = ("file_name", "file", "ticket_id", "uploaded_by")

#: The legacy index whose support rows are a secondary copy of the contact
#: details rather than a conversation.
LEGACY_CONTACT_SOURCE = "legacy_contact_index"

#: Events that read the legacy contact copy.
CONTACT_QUERY_EVENTS: frozenset[str] = frozenset(
    {"contact_query_started", "contact_query_completed", "contact_lookup_completed"}
)

#: Collections and attributes that mark a support conversation.
MESSAGE_COLLECTIONS: frozenset[str] = frozenset(
    {"support_conversations", "tickets", "support_records"}
)
MESSAGE_EVENTS: frozenset[str] = frozenset(
    {"message_pii_scan", "message_read", "support_ticket_read_completed"}
)
MESSAGE_FIELDS: tuple[str, ...] = ("message_excerpt", "message_text")

#: Events marking an analytics record keyed only by a hashed identifier, which is
#: a secondary identifier copy rather than an analytics profile.
CONTACT_LOOKUP_EVENTS: frozenset[str] = frozenset(
    {"contact_lookup_completed", "contact_lookup_started"}
)


class Treatment(StrEnum):
    """The shape of the steps a resource needs.

    Derived from the rules file's action, with two shapes that an action alone
    does not determine: a backup archive is *retained* while its restore path is
    blocked, and a data flow is disabled rather than treated as a record.
    """

    DELETE = "DELETE"
    REDACT = "REDACT"
    ANONYMIZE = "ANONYMIZE"
    RETAIN = "RETAIN"
    DISABLE_FLOW = "DISABLE_FLOW"
    BLOCK_RESTORE = "BLOCK_RESTORE"


#: Which operations each treatment emits, by phase. The finding and remediation
#: operations are used only when the state says they are warranted.
TREATMENT_OPERATIONS: dict[Treatment, tuple[Operation, Operation, Operation]] = {
    Treatment.DELETE: (
        Operation.REPORT_REMAINING_RECORD,
        Operation.DELETE_RECORD,
        Operation.VERIFY_RECORD_ABSENT,
    ),
    Treatment.REDACT: (
        Operation.REPORT_EXPOSED_PII,
        Operation.REDACT_RECORD,
        Operation.VERIFY_RECORD_REDACTED,
    ),
    Treatment.ANONYMIZE: (
        Operation.REPORT_EXPOSED_PII,
        Operation.ANONYMIZE_RECORD,
        Operation.VERIFY_RECORD_ANONYMIZED,
    ),
    Treatment.DISABLE_FLOW: (
        Operation.REPORT_ACTIVE_DATA_FLOW,
        Operation.DISABLE_DATA_FLOW,
        Operation.VERIFY_DATA_FLOW_DISABLED,
    ),
    Treatment.BLOCK_RESTORE: (
        Operation.REPORT_RESTORE_RISK,
        Operation.BLOCK_RESTORE,
        Operation.VERIFY_RESTORE_BLOCKED,
    ),
    Treatment.RETAIN: (
        Operation.VERIFY_RECORD_PRESENT,
        Operation.VERIFY_RECORD_PRESENT,
        Operation.VERIFY_RECORD_PRESENT,
    ),
}


@dataclass(frozen=True, slots=True)
class Classification:
    """A resource's category, the rule behind it, and the steps it needs."""

    category: str
    treatment: Treatment
    rule_id: str
    reason: str

    @property
    def operations(self) -> tuple[Operation, Operation, Operation]:
        """The finding, remediation and verification operations for this shape."""
        return TREATMENT_OPERATIONS[self.treatment]


class PolicyBook:
    """The deletion policy, loaded from ``deletion_rules.csv``."""

    def __init__(self, dataset: Dataset) -> None:
        """Index the rules by category."""
        self.rules: dict[str, Rule] = {
            rule.data_category: rule for rule in dataset.rules
        }

    def action_for(self, category: str) -> tuple[RequiredAction | None, str]:
        """The required action for a category, and the rule id that states it."""
        rule = self.rules.get(category)
        if rule is None:
            return (None, "")
        return (rule.required_action, rule.rule_id)

    def classify(self, state: ResourceState) -> Classification:
        """Resolve a resource's category and the treatment it needs."""
        category, reason = resolve_category(state)
        if category == DATA_FLOW_CATEGORY:
            return Classification(
                category=category,
                treatment=Treatment.DISABLE_FLOW,
                rule_id="",
                reason=reason,
            )
        action, rule_id = self.action_for(category)
        return Classification(
            category=category,
            treatment=_treatment_for(category, action),
            rule_id=rule_id,
            reason=reason,
        )


def _treatment_for(category: str, action: RequiredAction | None) -> Treatment:
    """Turn a policy action into a step shape.

    ``RETAIN`` means two different things depending on the category. For a
    security audit log or a legal hold it means keep the record and prove it is
    still there. For a backup archive the rule's own condition is "restore access
    can be blocked", so the archive is kept while its restore path is closed.
    """
    if action is RequiredAction.RETAIN and category == "BACKUP_ARCHIVE":
        return Treatment.BLOCK_RESTORE
    if action is RequiredAction.DELETE:
        return Treatment.DELETE
    if action is RequiredAction.REDACT:
        return Treatment.REDACT
    if action is RequiredAction.ANONYMIZE:
        return Treatment.ANONYMIZE
    if action is RequiredAction.RETAIN:
        return Treatment.RETAIN
    # No rule covers the category: deleting is the default a deletion request
    # asks for, and is what every unconditional rule in the file requires.
    return Treatment.DELETE


def resolve_category(state: ResourceState) -> tuple[str, str]:
    """The data category of a resource, and how it was decided."""
    stated = _stated_category(state)
    if stated is not None:
        return (stated, "stated in evidence")

    prefix = state.resource_id.rpartition("_")[0] or state.resource_id
    if prefix == "support":
        return _support_category(state)
    if prefix == "analytics":
        return _analytics_category(state)
    if prefix == "data_flow":
        return (DATA_FLOW_CATEGORY, "data flow resource")
    if prefix in PREFIX_CATEGORY:
        return (PREFIX_CATEGORY[prefix], "resource prefix")
    return ("", "unclassified")


def _stated_category(state: ResourceState) -> str | None:
    """A category named outright by the evidence, if it is one the rules know."""
    raw = state.attributes.get("record_type", "").strip().upper()
    if not raw or is_nullish(raw):
        return None
    if raw == DATA_FLOW_CATEGORY:
        return DATA_FLOW_CATEGORY
    return raw if raw in _KNOWN_CATEGORIES else None


def _support_category(state: ResourceState) -> tuple[str, str]:
    """Separate attachments and legacy contact copies from conversations.

    The three readings need different treatments: an attachment belonging only to
    the subject is deleted, a legacy contact copy is a secondary identifier and is
    deleted, and a conversation carrying shared order context is redacted.
    """
    attributes = state.attributes
    collection = attributes.get("collection", "").strip().casefold()
    kind = attributes.get("record_kind", "").strip().casefold()
    events = _events(state)

    if "attachment" in kind:
        return ("SUPPORT_ATTACHMENT", f"record kind {kind!r}")
    if collection in ATTACHMENT_COLLECTIONS:
        return ("SUPPORT_ATTACHMENT", f"collection {collection}")
    if attributes.get("storage_class", "").strip().casefold() in ATTACHMENT_STORAGE_CLASSES:
        return ("SUPPORT_ATTACHMENT", "storage_class ticket_attachment")
    if events & ATTACHMENT_EVENTS:
        return ("SUPPORT_ATTACHMENT", f"event {sorted(events & ATTACHMENT_EVENTS)[0]}")
    if any(not is_nullish(attributes.get(name, "")) for name in ATTACHMENT_FIELDS):
        return ("SUPPORT_ATTACHMENT", "attachment file attributes")

    if attributes.get("source", "").strip().casefold() == LEGACY_CONTACT_SOURCE:
        return ("SECONDARY_IDENTIFIER", "legacy contact index copy")
    if events & CONTACT_QUERY_EVENTS and "support_replica" in attributes.get("service", ""):
        return ("SECONDARY_IDENTIFIER", "support replica contact query")

    if collection in MESSAGE_COLLECTIONS or events & MESSAGE_EVENTS:
        return ("SUPPORT_MESSAGE", "support conversation record")
    if any(not is_nullish(attributes.get(name, "")) for name in MESSAGE_FIELDS):
        return ("SUPPORT_MESSAGE", "message body present")
    return ("SUPPORT_MESSAGE", "support record default")


def _analytics_category(state: ResourceState) -> tuple[str, str]:
    """Separate a hashed contact lookup from an analytics profile.

    An analytics record reached by ``contact_lookup_*`` and keyed on an email
    hash is a secondary copy of the identifier, not a behavioural profile, so it
    is deleted rather than anonymised.
    """
    if _events(state) & CONTACT_LOOKUP_EVENTS:
        return ("SECONDARY_IDENTIFIER", "analytics contact lookup by hash")
    return ("ANALYTICS_PROFILE", "analytics profile record")


def _events(state: ResourceState) -> frozenset[str]:
    """Every event name recorded against the resource."""
    raw = state.attributes.get("events", state.attributes.get("event", ""))
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


#: Categories the rules file defines, plus the data-flow pseudo-category.
_KNOWN_CATEGORIES: frozenset[str] = frozenset(
    {
        "USER_PROFILE",
        "SECONDARY_IDENTIFIER",
        "SEARCH_INDEX_ENTRY",
        "CACHE_ENTRY",
        "SUPPORT_MESSAGE",
        "SUPPORT_ATTACHMENT",
        "ANALYTICS_PROFILE",
        "PRODUCT_TELEMETRY",
        "USER_EXPORT",
        "MARKETING_PROFILE",
        "EMAIL_DELIVERY_LOG",
        "SECURITY_AUDIT_LOG",
        "LEGAL_HOLD_RECORD",
        "BACKUP_ARCHIVE",
        "ACCOUNT_TOMBSTONE",
    }
)
