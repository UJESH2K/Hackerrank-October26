"""Decide whether a request authorises deletion.

Two stages, so the model is asked only what rules cannot answer:

1. **Rules.** A request with clear deletion phrasing and no negation or scoping
   word is a deletion request, decided without a model call. This is the large
   majority of the dataset.
2. **Model.** Everything else: anything with a negation, a scope word, or any of
   the other-request vocabulary, plus anything whose phrasing the cue lists do
   not recognise at all.

The split is justified by measurement, not taste. The dataset contains
**negation traps** - non-deletion requests that state a deletion keyword in order
to refuse it ("I am not asking for deletion"). A cue list decides those
backwards, so any text carrying both a deletion cue and a negation goes to the
model (decision D7).

Requests are normalised before hashing - emails and digits masked - so the 518
requests collapse to 375 distinct texts and the cache is keyed per text rather
than per case.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from audit.llm import (
    PURPOSE_CLASSIFY,
    CacheStore,
    CallRecord,
    LlmGateway,
    load_prompt,
    prompt_fingerprint,
)
from audit.models import Request

#: The prompt that drives stage 2.
PROMPT_FILE = "request_intent_classifier.md"

#: How confident the model must be before a non-deletion verdict is accepted.
#:
#: The two outcomes are not symmetric. A wrong ``DELETION`` costs a few steps on
#: one case; a wrong ``NOT_APPLICABLE`` discards the entire case - its status,
#: every finding, every remediation and every verification - and there is no way
#: to recover any of it. So a non-deletion verdict has to be held confidently,
#: and anything below the bar is audited as a deletion and logged (decision D40).
MIN_NON_DELETION_CONFIDENCE = 0.9


class RequestType(StrEnum):
    """What the person is actually asking for."""

    ACCOUNT_DELETION = "ACCOUNT_DELETION"
    MARKETING_OPT_OUT = "MARKETING_OPT_OUT"
    ACCESS_REQUEST = "ACCESS_REQUEST"
    PORTABILITY_EXPORT = "PORTABILITY_EXPORT"
    RECTIFICATION = "RECTIFICATION"
    ACCOUNT_SUPPORT = "ACCOUNT_SUPPORT"
    OTHER = "OTHER"


class Decider(StrEnum):
    """Which stage decided a request."""

    RULES = "rules"
    MODEL = "model"
    FALLBACK = "fallback"


#: Deletion cues, as stems so noun and verb forms both match. The languages
#: present in the dataset are English, Spanish, Portuguese, French, Japanese and
#: Korean; the others are kept because the request text is free-form.
DELETION_CUES: tuple[str, ...] = (
    # English
    "delet",
    "eras",
    "remove my account",
    "remove the account",
    "remove my profile",
    "remove the profile",
    "remove my personal",
    "remove the personal",
    "remove my data",
    "remove my details",
    "removal of my account",
    "close my account",
    "closure of my account",
    "closed",
    "wipe",
    "purge",
    "right to be forgotten",
    "forget me",
    # Spanish
    "elimina",
    "borra",
    "suprim",
    # Portuguese
    "exclus",
    "exclua",
    "excluir",
    "apagar",
    "apague",
    # French
    "supprim",
    "suppression",
    "effac",
    # German / Italian / Dutch / Turkish / Indonesian / Polish / Vietnamese
    "lösch",
    "cancell",
    "verwijder",
    "silin",
    "silme",
    "hapus",
    "usun",
    "xóa",
    # Russian / Japanese / Korean / Arabic
    "удал",
    "削除",
    "삭제",
    "حذف",
    "إزالة",
)

#: Statements that the account or its records must survive. Any of these sends
#: the request to the model, however clear the deletion cue looks.
RETENTION_CUES: tuple[str, ...] = (
    "not asking for deletion",
    "not a request to",
    "do not close",
    "don't close",
    "do not delete",
    "don't delete",
    "do not remove",
    "don't remove",
    "leave the account",
    "leave my profile",
    "leave my account",
    "keep the account",
    "keep my account",
    "still need the account",
    "still need my account",
    "still use it",
    "still use the account",
    "still want",
    "continue using",
    "without removing",
    "without deleting",
    "account active",
    "as it is",
    "in place",
    "no cierre",
    "sigo usando",
    "não exclua",
    "continuo usando",
    "ne supprimez pas",
    "削除しないで",
    "삭제하지",
)

#: Words that narrow a request to part of the account rather than all of it.
SCOPE_CUES: tuple[str, ...] = (
    " only",
    "only ",
    " just ",
    "solamente",
    "solo ",
    "apenas",
    "seulement",
    "uniquement",
    "のみ",
    "だけ",
    "만 ",
)

#: Vocabulary of the other request types. Their presence sends the text to the
#: model even when a deletion cue is also present.
OTHER_REQUEST_CUES: tuple[str, ...] = (
    "copy of my",
    "copy of the",
    "copy of everything",
    "access request",
    "subject access",
    "what personal information",
    "what information do you hold",
    "information request",
    "portable format",
    "portability",
    "export",
    "download my",
    "send me my",
    "send me a copy",
    "need a copy",
    "invoice",
    "receipt",
    # "correct" on its own is a substring trap: many deletion requests say
    # "apply the correct treatment to records covered by the request", which has
    # nothing to do with rectification. Match the rectification phrasings only.
    "correct my",
    "correct it",
    "correct the name",
    "correct the spelling",
    "correct the email",
    "correct the address",
    "corrected",
    "correction",
    "rectif",
    "misspell",
    "out of date",
    "replace it",
    "change the email",
    "change the address",
    "update my",
    "unsubscribe",
    "opt out",
    "opt-out",
    "marketing",
    "promotional",
    "newsletter",
    "mailing list",
    "stop sending",
    "reset the login",
    "resetting the login",
    "can't access",
    "cannot access",
    "password",
    "auskunft",
    "berichtigung",
    "acceso",
    "rectificar",
    "portabilidad",
    "rectifier",
)

_EMAILS = re.compile(r"[\w.+-]+@[\w.-]+")
_DIGITS = re.compile(r"\d+")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class IntentDecision:
    """The verdict for one request."""

    case_id: str
    is_deletion_request: bool
    request_type: RequestType
    confidence: float
    reason: str
    decided_by: Decider
    #: Set when a low-confidence non-deletion verdict was overridden for safety.
    overridden: bool = False

    @property
    def authorises_deletion(self) -> bool:
        """Whether the planner should audit this case as a deletion."""
        return self.is_deletion_request


def normalise_request(text: str) -> str:
    """Collapse a request to a cache-shareable template.

    Emails and digit runs are masked, so the 518 requests reduce to 375 distinct
    texts and two cases with the same wording share one cached answer.
    """
    masked = _EMAILS.sub("<email>", text)
    return _WHITESPACE.sub(" ", _DIGITS.sub("#", masked)).strip()


def _matches(text: str, cues: Sequence[str]) -> list[str]:
    """The cues present in a lower-cased text."""
    return [cue for cue in cues if cue in text]


def has_deletion_cue(text: str) -> bool:
    """Whether the text asks for deletion in any of the supported languages."""
    return bool(_matches(text.casefold(), DELETION_CUES))


def needs_model(text: str) -> tuple[bool, str]:
    """Whether a text must go to the model, and why.

    Rules may decide a request only when it has a clear deletion cue and carries
    no negation, no scoping word and none of the other-request vocabulary.
    """
    lowered = text.casefold()
    if not _matches(lowered, DELETION_CUES):
        return (True, "no recognised deletion phrasing")
    retention = _matches(lowered, RETENTION_CUES)
    if retention:
        return (True, f"retention statement: {retention[0]!r}")
    scope = _matches(lowered, SCOPE_CUES)
    if scope:
        return (True, f"scoping word: {scope[0].strip()!r}")
    other = _matches(lowered, OTHER_REQUEST_CUES)
    if other:
        return (True, f"other-request vocabulary: {other[0]!r}")
    return (False, "clear deletion phrasing, no negation or scope")


def classify_by_rules(request: Request) -> IntentDecision | None:
    """Decide a request without a model call, or return ``None``."""
    required, reason = needs_model(request.request_text)
    if required:
        return None
    return IntentDecision(
        case_id=request.case_id,
        is_deletion_request=True,
        request_type=RequestType.ACCOUNT_DELETION,
        confidence=0.95,
        reason=reason,
        decided_by=Decider.RULES,
    )


def _validate_batch(payload: object) -> bool:
    """Whether a model reply has the expected array-of-objects shape."""
    if not isinstance(payload, list):
        return False
    for item in payload:
        if not isinstance(item, Mapping):
            return False
        if "id" not in item or "is_deletion_request" not in item:
            return False
        if not isinstance(item["is_deletion_request"], bool):
            return False
    return True


def _decision_from(
    case_id: str, item: Mapping[str, object], decided_by: Decider
) -> IntentDecision:
    """Build a decision from one model result, normalising the type."""
    is_deletion = bool(item.get("is_deletion_request"))
    raw_type = str(item.get("request_type", "")).strip().upper()
    try:
        request_type = RequestType(raw_type)
    except ValueError:
        request_type = (
            RequestType.ACCOUNT_DELETION if is_deletion else RequestType.OTHER
        )
    # The two fields must agree: the type is what the boolean means.
    if is_deletion:
        request_type = RequestType.ACCOUNT_DELETION
    elif request_type is RequestType.ACCOUNT_DELETION:
        request_type = RequestType.OTHER
    try:
        confidence = float(item.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    reason = str(item.get("short_reason", ""))[:120]

    # The asymmetric safety rule: an unconfident non-deletion verdict would throw
    # the whole case away, so it is audited as a deletion instead.
    overridden = False
    if not is_deletion and confidence < MIN_NON_DELETION_CONFIDENCE:
        overridden = True
        is_deletion = True
        request_type = RequestType.ACCOUNT_DELETION
        reason = (
            f"non-deletion verdict held at only {confidence:.2f} "
            f"(< {MIN_NON_DELETION_CONFIDENCE}); audited as a deletion. "
            f"Model said: {reason}"
        )[:240]

    return IntentDecision(
        case_id=case_id,
        is_deletion_request=is_deletion,
        request_type=request_type,
        confidence=confidence,
        reason=reason,
        decided_by=decided_by,
        overridden=overridden,
    )


class IntentClassifier:
    """Classifies request intent, rules first and the model second."""

    def __init__(
        self,
        gateway: LlmGateway,
        prompts_dir: Path,
        fallback_is_deletion: bool = True,
    ) -> None:
        """Load the prompt and hold the gateway.

        Args:
            gateway: the model gateway. When unavailable every undecided request
                takes the fallback.
            prompts_dir: where ``request_intent_classifier.md`` lives.
            fallback_is_deletion: what an undecided request becomes when no model
                is available. ``True`` reproduces the deterministic baseline,
                where every request is treated as a deletion request.
        """
        self.gateway = gateway
        self.version, self.prompt = load_prompt(prompts_dir / PROMPT_FILE)
        # The cache token is this prompt file's own fingerprint, so editing
        # it re-pays only the classification calls (decision D41).
        self.prompt_token = prompt_fingerprint(prompts_dir / PROMPT_FILE)
        self.fallback_is_deletion = fallback_is_deletion
        self.rule_decided = 0
        self.model_decided = 0
        self.fallback_decided = 0

    def classify(self, requests: Sequence[Request]) -> dict[str, IntentDecision]:
        """Classify every request, batching the ones rules cannot decide."""
        decisions: dict[str, IntentDecision] = {}
        undecided: list[Request] = []
        for request in requests:
            decided = classify_by_rules(request)
            if decided is not None:
                decisions[request.case_id] = decided
                self.rule_decided += 1
            else:
                undecided.append(request)

        if not undecided:
            return decisions

        # Group by normalised text so identical wording is classified once.
        by_template: dict[str, list[Request]] = {}
        for request in undecided:
            by_template.setdefault(normalise_request(request.request_text), []).append(
                request
            )

        resolved = self._classify_templates(sorted(by_template))
        for template, group in by_template.items():
            result = resolved.get(template)
            for request in group:
                if result is None:
                    decisions[request.case_id] = self._fallback(request)
                    self.fallback_decided += 1
                else:
                    decisions[request.case_id] = _decision_from(
                        request.case_id, result, Decider.MODEL
                    )
                    self.model_decided += 1
        return decisions

    def _classify_templates(
        self, templates: Sequence[str]
    ) -> dict[str, Mapping[str, object]]:
        """Classify distinct texts, using the cache per text and batching misses."""
        results: dict[str, Mapping[str, object]] = {}
        pending: list[str] = []
        for template in templates:
            cached = self._cached(template)
            if cached is not None:
                results[template] = cached
            else:
                pending.append(template)

        if not pending or not self.gateway.available:
            return results

        size = max(1, self.gateway.settings.classifier_batch_size)
        for start in range(0, len(pending), size):
            batch = pending[start : start + size]
            answered = self._call_batch(batch)
            for template, item in answered.items():
                results[template] = item
                self._store(template, item)
        return results

    def _cache_key(self, template: str) -> str:
        """The per-text cache key."""
        return CacheStore.key(
            self.gateway.settings.classifier_model,
            self.prompt_token,
            {"template": template},
        )

    def _cached(self, template: str) -> Mapping[str, object] | None:
        """A previously classified text, if it is in the cache.

        A disabled gateway reads nothing: ``--no-llm`` means no model-derived
        answer reaches the pipeline, cached or not. Otherwise the deterministic
        baseline would silently inherit verdicts from an earlier live run
        (decision D39).
        """
        if not self.gateway.enabled:
            return None
        entry = self.gateway.cache.get(self._cache_key(template))
        if entry is None:
            return None
        response = entry.get("response")
        if isinstance(response, Mapping) and "is_deletion_request" in response:
            self.gateway.tracker.record(
                CallRecord(
                    model=self.gateway.settings.classifier_model,
                    purpose=PURPOSE_CLASSIFY,
                    prompt_version=self.prompt_token,
                    input_tokens=0,
                    output_tokens=0,
                    cache_hit=True,
                    latency_ms=0,
                )
            )
            return response
        return None

    def _store(self, template: str, item: Mapping[str, object]) -> None:
        """Cache one classified text."""
        self.gateway.cache.put(
            self._cache_key(template),
            {
                "model": self.gateway.settings.classifier_model,
                "purpose": PURPOSE_CLASSIFY,
                "response": dict(item),
            },
        )

    def _call_batch(self, templates: Sequence[str]) -> dict[str, Mapping[str, object]]:
        """Classify one batch of distinct texts."""
        numbered = [
            {"id": f"r{index}", "request_text": template}
            for index, template in enumerate(templates)
        ]
        blocks = [
            {
                "type": "text",
                "text": (
                    "Classify each request below. Reply with a JSON array of "
                    "one object per id, in the same order.\n\n"
                    + "\n\n".join(
                        f"id: {item['id']}\nrequest_text: {item['request_text']}"
                        for item in numbered
                    )
                ),
            }
        ]
        payload = self.gateway.call_json(
            purpose=PURPOSE_CLASSIFY,
            prompt_version=self.prompt_token,
            system=self.prompt,
            blocks=blocks,
            validator=_validate_batch,
            items=len(templates),
            cache_payload={"batch": list(templates)},
        )
        if payload is None:
            return {}
        by_id = {str(item.get("id")): item for item in payload if isinstance(item, Mapping)}
        answered: dict[str, Mapping[str, object]] = {}
        for index, template in enumerate(templates):
            item = by_id.get(f"r{index}")
            if item is not None:
                answered[template] = item
        return answered

    def _fallback(self, request: Request) -> IntentDecision:
        """What an undecided request becomes with no model available."""
        return IntentDecision(
            case_id=request.case_id,
            is_deletion_request=self.fallback_is_deletion,
            request_type=RequestType.ACCOUNT_DELETION
            if self.fallback_is_deletion
            else RequestType.OTHER,
            confidence=0.0,
            reason=f"no model available ({self.gateway.unavailable_reason()})",
            decided_by=Decider.FALLBACK,
        )
