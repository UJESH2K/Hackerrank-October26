"""The single gateway for every model call, with caching and token accounting.

Nothing else in the codebase talks to the Anthropic API. That keeps three
properties easy to guarantee:

* **the key never leaks** - it is read from ``ANTHROPIC_API_KEY`` and never
  logged, cached or written to a report;
* **a repeat run is free** - every call is cached on disk by the hash of the
  model, the prompt version and the input, so a warm run spends no tokens;
* **the numbers are real** - input and output token counts come from the
  response's own ``usage`` field and are never estimated.

When no key is configured, or ``--no-llm`` is passed, the gateway reports itself
unavailable and every caller falls back to its deterministic default.

Sampling note: ``temperature`` is deliberately **not** sent. On
``claude-haiku-5-5`` a non-default sampling value returns 400, and on
``claude-opus-5-5`` the sampling parameters are removed entirely. Repeatability
comes from the disk cache and a pinned ``effort`` instead.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Environment variable holding the API key. Never read from anywhere else.
API_KEY_ENV = "ANTHROPIC_API_KEY"

#: The repository root, where ``.env`` lives if there is one.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_env_file() -> None:
    """Load ``.env`` from the repository root, if python-dotenv is installed.

    This is the only place in the codebase that reads ``.env``, and it runs once
    at import. ``override=False`` means a real environment variable always wins,
    so an operator can point a run at a different key without editing the file.

    The file's contents are never logged, echoed or copied anywhere: the SDK
    reads the key straight out of the environment.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_file = _REPO_ROOT / ".env"
    if env_file.is_file():
        load_dotenv(env_file, override=False)


_load_env_file()

#: Purposes a call can serve, used in the usage report.
PURPOSE_CLASSIFY = "classify"
PURPOSE_EXTRACT = "extract"


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """USD per million tokens for one model."""

    input_per_mtok: float
    output_per_mtok: float

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        """The cost of one call in USD."""
        return (
            input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok
        ) / 1_000_000


@dataclass(frozen=True, slots=True)
class LlmSettings:
    """Model choices, limits and prices, all from ``config.yaml``."""

    classifier_model: str
    vision_model: str
    prices: Mapping[str, ModelPricing]
    max_output_tokens: int = 4096
    classifier_effort: str = "low"
    vision_effort: str = "medium"
    classifier_batch_size: int = 20
    cache_enabled: bool = True

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> LlmSettings:
        """Read the ``llm`` and ``prices_per_1m_tokens`` sections of the config."""
        llm = config.get("llm", {})
        raw_prices = config.get("prices_per_1m_tokens", {})
        prices = {
            model: ModelPricing(
                input_per_mtok=float(entry.get("input", 0.0)),
                output_per_mtok=float(entry.get("output", 0.0)),
            )
            for model, entry in raw_prices.items()
            if isinstance(entry, Mapping)
        }
        return cls(
            classifier_model=str(llm.get("classifier_model", "")),
            vision_model=str(llm.get("vision_model", "")),
            prices=prices,
            max_output_tokens=int(llm.get("max_output_tokens", 4096)),
            classifier_effort=str(llm.get("classifier_effort", "low")),
            vision_effort=str(llm.get("vision_effort", "medium")),
            classifier_batch_size=int(llm.get("classifier_batch_size", 20)),
            cache_enabled=bool(llm.get("cache_enabled", True)),
        )

    def pricing_for(self, model: str) -> ModelPricing:
        """The pricing for a model, or zero if the config does not list it."""
        return self.prices.get(model, ModelPricing(0.0, 0.0))


@dataclass(frozen=True, slots=True)
class CallRecord:
    """One model call, or one cache hit."""

    model: str
    purpose: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    cache_hit: bool
    latency_ms: int
    items: int = 1

    @property
    def total_tokens(self) -> int:
        """Tokens billed for this call. Zero for a cache hit."""
        return self.input_tokens + self.output_tokens


@dataclass
class UsageTracker:
    """Collects call records and summarises them for the usage report."""

    records: list[CallRecord] = field(default_factory=list)

    def record(self, call: CallRecord) -> None:
        """Add one call."""
        self.records.append(call)

    @property
    def calls(self) -> int:
        """Every call, including cache hits."""
        return len(self.records)

    @property
    def api_calls(self) -> int:
        """Calls that actually reached the API."""
        return sum(1 for call in self.records if not call.cache_hit)

    @property
    def cache_hits(self) -> int:
        """Calls served from disk."""
        return sum(1 for call in self.records if call.cache_hit)

    @property
    def input_tokens(self) -> int:
        """Total input tokens billed."""
        return sum(call.input_tokens for call in self.records)

    @property
    def output_tokens(self) -> int:
        """Total output tokens billed."""
        return sum(call.output_tokens for call in self.records)

    @property
    def is_cold(self) -> bool:
        """Whether this run spent any tokens at all."""
        return self.api_calls > 0

    def by_model(self) -> dict[str, dict[str, int]]:
        """Per-model call and token totals."""
        totals: dict[str, dict[str, int]] = {}
        for call in self.records:
            entry = totals.setdefault(
                call.model,
                {"calls": 0, "api_calls": 0, "cache_hits": 0, "input": 0, "output": 0},
            )
            entry["calls"] += 1
            entry["api_calls"] += 0 if call.cache_hit else 1
            entry["cache_hits"] += 1 if call.cache_hit else 0
            entry["input"] += call.input_tokens
            entry["output"] += call.output_tokens
        return totals

    def by_purpose(self) -> dict[str, dict[str, int]]:
        """Per-purpose call and token totals."""
        totals: dict[str, dict[str, int]] = {}
        for call in self.records:
            entry = totals.setdefault(
                call.purpose,
                {"calls": 0, "api_calls": 0, "cache_hits": 0, "input": 0, "output": 0},
            )
            entry["calls"] += 1
            entry["api_calls"] += 0 if call.cache_hit else 1
            entry["cache_hits"] += 1 if call.cache_hit else 0
            entry["input"] += call.input_tokens
            entry["output"] += call.output_tokens
        return totals

    def cost(self, settings: LlmSettings) -> float:
        """Total estimated cost in USD."""
        return sum(
            settings.pricing_for(call.model).cost(call.input_tokens, call.output_tokens)
            for call in self.records
        )

    def summary(
        self,
        settings: LlmSettings,
        cases: int,
        errors: Sequence[str] = (),
        auth_failed: bool = False,
    ) -> dict[str, Any]:
        """The run record appended to ``usage_run.json``.

        A run whose calls failed is marked as such. Without that, a rejected key
        produces a record showing calls and zero tokens, which reads exactly like
        a successful cached run.
        """
        return {
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "run_type": "failed"
            if errors and not self.input_tokens
            else ("cold" if self.is_cold else "warm"),
            "api_errors": list(errors),
            "auth_failed": auth_failed,
            "cases": cases,
            "calls": self.calls,
            "api_calls": self.api_calls,
            "cache_hits": self.cache_hits,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "tokens_per_case": round((self.input_tokens + self.output_tokens) / cases, 2)
            if cases
            else 0.0,
            "estimated_cost_usd": round(self.cost(settings), 6),
            "cost_per_case_usd": round(self.cost(settings) / cases, 8) if cases else 0.0,
            "by_model": self.by_model(),
            "by_purpose": self.by_purpose(),
            "models": {
                "classifier": settings.classifier_model,
                "vision": settings.vision_model,
            },
        }

    def append_run(
        self,
        path: Path,
        settings: LlmSettings,
        cases: int,
        errors: Sequence[str] = (),
        auth_failed: bool = False,
    ) -> None:
        """Append this run to the usage history, keeping earlier runs."""
        history: list[dict[str, Any]] = []
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    history = loaded
            except json.JSONDecodeError:
                history = []
        history.append(self.summary(settings, cases, errors, auth_failed))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(history, indent=1, ensure_ascii=False), encoding="utf-8"
        )


class CacheStore:
    """Disk cache for model responses, keyed by content hash."""

    def __init__(self, directory: Path | None, enabled: bool = True) -> None:
        """Hold the cache directory; a ``None`` directory disables the cache."""
        self.directory = directory
        self.enabled = enabled and directory is not None

    @staticmethod
    def key(model: str, prompt_version: str, payload: Any) -> str:
        """The cache key: a hash of the model, prompt version and input.

        The payload is serialised with sorted keys so an equivalent input always
        produces the same key.
        """
        serialised = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256()
        digest.update(model.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(prompt_version.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(serialised.encode("utf-8"))
        return digest.hexdigest()

    def path_for(self, key: str) -> Path | None:
        """Where an entry lives."""
        return None if self.directory is None else self.directory / f"{key}.json"

    def get(self, key: str) -> Any | None:
        """Read an entry, or ``None`` on a miss."""
        path = self.path_for(key)
        if not self.enabled or path is None or not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def put(self, key: str, value: Any) -> None:
        """Write an entry."""
        path = self.path_for(key)
        if not self.enabled or path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, indent=1, ensure_ascii=False), encoding="utf-8"
        )


def extract_json(text: str) -> Any | None:
    """Parse a model response that should be JSON.

    Tolerates a fenced code block, which a model sometimes adds despite being
    asked for bare JSON. Returns ``None`` when the text is not JSON.
    """
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.partition("\n")[2]
        fence = candidate.rfind("```")
        if fence != -1:
            candidate = candidate[:fence]
    candidate = candidate.strip()
    if not candidate:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start = min(
            (index for index in (candidate.find("["), candidate.find("{")) if index != -1),
            default=-1,
        )
        end = max(candidate.rfind("]"), candidate.rfind("}"))
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None


class LlmGateway:
    """Makes model calls, or declares itself unavailable."""

    #: Appended to a retry when the first response was not valid JSON.
    REPAIR_INSTRUCTION = (
        "Your previous reply was not valid JSON. Reply with the JSON value only: "
        "no prose, no explanation and no code fences."
    )

    def __init__(
        self,
        settings: LlmSettings,
        cache_dir: Path | None = None,
        client: Any | None = None,
        tracker: UsageTracker | None = None,
        enabled: bool = True,
        require_real_client: bool = False,
    ) -> None:
        """Build the gateway.

        Args:
            settings: model ids, limits and prices.
            cache_dir: the ``llm`` cache directory. ``None`` disables caching.
            client: an Anthropic client, or a stand-in for tests. When omitted
                one is constructed only if a key is configured.
            tracker: usage tracker to record into.
            enabled: ``False`` for ``--no-llm``; no client is constructed.
            require_real_client: reject any client that is not the Anthropic
                SDK's own. ``main.py`` sets this, so a production run can never
                be served scripted answers - without a key it runs the
                deterministic path instead.

        Raises:
            ValueError: if ``require_real_client`` is set and ``client`` is not an
                ``anthropic.Anthropic`` instance.
        """
        if require_real_client and client is not None and not is_real_client(client):
            raise ValueError(
                "a production run refuses a substituted client: "
                f"{type(client).__module__}.{type(client).__qualname__}"
            )
        self.settings = settings
        self.cache = CacheStore(cache_dir, settings.cache_enabled)
        self.tracker = tracker if tracker is not None else UsageTracker()
        self.enabled = enabled
        self.require_real_client = require_real_client
        self._client = client
        self._client_failed = False
        #: Call failures, as ``type: message``. Never contains a key: the SDK
        #: redacts it from its own error messages, and nothing here reads it.
        self.errors: list[str] = []
        #: Set when the API rejects the credentials. Further calls are skipped
        #: rather than repeated, so one bad key costs one request, not hundreds.
        self.auth_failed = False

    @property
    def has_key(self) -> bool:
        """Whether an API key is configured."""
        return bool(os.environ.get(API_KEY_ENV, "").strip())

    @property
    def available(self) -> bool:
        """Whether a model call can actually be made."""
        if not self.enabled or self._client_failed or self.auth_failed:
            return False
        return self._client is not None or self.has_key

    def unavailable_reason(self) -> str:
        """Why the gateway cannot be used, for the run summary."""
        if not self.enabled:
            return "disabled by --no-llm or config"
        if self.auth_failed:
            return (
                f"the API rejected the credentials ({API_KEY_ENV} is set but not "
                "accepted)"
            )
        if self._client_failed:
            return "client could not be constructed"
        if not self.has_key and self._client is None:
            return f"{API_KEY_ENV} is not set"
        return ""

    def client(self) -> Any | None:
        """The Anthropic client, constructed on first use.

        ``enabled`` is checked before anything else, so ``--no-llm`` makes no
        call even when a client was injected.
        """
        if not self.enabled or self._client_failed:
            return None
        if self._client is not None:
            return self._client
        if not self.has_key:
            return None
        try:
            import anthropic
        except ImportError:
            self._client_failed = True
            return None
        try:
            # The SDK reads ANTHROPIC_API_KEY itself; the key is never handled
            # here, so it cannot be logged or cached by accident.
            self._client = anthropic.Anthropic()
        except Exception:  # noqa: BLE001 - any construction failure disables the LLM
            self._client_failed = True
            return None
        return self._client

    def call_json(
        self,
        purpose: str,
        prompt_version: str,
        system: str,
        blocks: Sequence[Mapping[str, Any]],
        validator: Callable[[Any], bool],
        model: str | None = None,
        max_tokens: int | None = None,
        effort: str | None = None,
        items: int = 1,
        cache_payload: Any | None = None,
    ) -> Any | None:
        """Make one JSON-returning call, cached, with one retry on bad JSON.

        Args:
            purpose: ``classify`` or ``extract``.
            prompt_version: the prompt file's version, so a prompt edit misses
                the cache rather than serving a stale answer.
            system: the system prompt.
            blocks: the user content blocks (text, image or document).
            validator: returns ``True`` when the parsed JSON has the right shape.
            model: overrides the purpose's default model.
            max_tokens: output cap for this call.
            effort: thinking effort for this call.
            items: how many inputs this call covers, for the usage report.
            cache_payload: what to hash instead of the raw blocks. Used so a
                base64 image is not hashed twice.

        Returns:
            The parsed, validated JSON, or ``None`` if the call failed.
        """
        chosen_model = model or (
            self.settings.classifier_model
            if purpose == PURPOSE_CLASSIFY
            else self.settings.vision_model
        )
        payload = cache_payload if cache_payload is not None else list(blocks)
        key = CacheStore.key(chosen_model, prompt_version, [system, payload])

        cached = self.cache.get(key)
        if cached is not None and validator(cached.get("response")):
            self.tracker.record(
                CallRecord(
                    model=chosen_model,
                    purpose=purpose,
                    prompt_version=prompt_version,
                    input_tokens=0,
                    output_tokens=0,
                    cache_hit=True,
                    latency_ms=0,
                    items=items,
                )
            )
            return cached["response"]

        client = self.client()
        if client is None:
            return None

        request: dict[str, Any] = {
            "model": chosen_model,
            "max_tokens": max_tokens or self.settings.max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": list(blocks)}],
            # No temperature: sampling parameters are rejected by these models.
            "output_config": {
                "effort": effort
                or (
                    self.settings.classifier_effort
                    if purpose == PURPOSE_CLASSIFY
                    else self.settings.vision_effort
                )
            },
        }

        parsed = self._attempt(client, request, purpose, prompt_version, chosen_model, items)
        if parsed is not None and validator(parsed):
            self.cache.put(key, {"model": chosen_model, "purpose": purpose, "response": parsed})
            return parsed
        if self.auth_failed:
            # Retrying a rejected key only wastes a request.
            return None

        retry = dict(request)
        retry["system"] = f"{system}\n\n{self.REPAIR_INSTRUCTION}"
        parsed = self._attempt(client, retry, purpose, prompt_version, chosen_model, items)
        if parsed is not None and validator(parsed):
            self.cache.put(key, {"model": chosen_model, "purpose": purpose, "response": parsed})
            return parsed
        return None

    def _record_failure(self, error: Exception) -> None:
        """Record a call failure, and stop calling if the credentials are bad.

        An authentication failure is terminal for the run: retrying it on every
        remaining request would turn one bad key into hundreds of pointless
        requests, and would still produce no answers.
        """
        name = type(error).__name__
        message = " ".join(str(error).split())[:300]
        entry = f"{name}: {message}"
        if entry not in self.errors:
            self.errors.append(entry)
        lowered_name = name.casefold()
        self.auth_failed = self.auth_failed or (
            "authentication" in lowered_name
            or "permission" in lowered_name
            or "invalid x-api-key" in message
            or "401" in message
        )

    def _attempt(
        self,
        client: Any,
        request: Mapping[str, Any],
        purpose: str,
        prompt_version: str,
        model: str,
        items: int,
    ) -> Any | None:
        """Make one request and record its usage, whatever the outcome."""
        started = time.monotonic()
        try:
            response = client.messages.create(**request)
        except Exception as error:  # noqa: BLE001 - a failed call must not fail the audit
            # A failure must not be silent: a rejected key would otherwise look
            # exactly like a clean deterministic run (decision D38).
            self._record_failure(error)
            self.tracker.record(
                CallRecord(
                    model=model,
                    purpose=purpose,
                    prompt_version=prompt_version,
                    input_tokens=0,
                    output_tokens=0,
                    cache_hit=False,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    items=items,
                )
            )
            return None
        latency_ms = int((time.monotonic() - started) * 1000)
        usage = getattr(response, "usage", None)
        self.tracker.record(
            CallRecord(
                model=model,
                purpose=purpose,
                prompt_version=prompt_version,
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
                cache_hit=False,
                latency_ms=latency_ms,
                items=items,
            )
        )
        return extract_json(_response_text(response))


def is_real_client(client: Any) -> bool:
    """Whether a client is the Anthropic SDK's own.

    Used to keep the test stand-in out of a production run. If the SDK is not
    installed there is no real client to be had, so nothing qualifies.
    """
    try:
        import anthropic
    except ImportError:
        return False
    return isinstance(client, anthropic.Anthropic)


def _response_text(response: Any) -> str:
    """Concatenate the text blocks of a response."""
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", "") == "text":
            parts.append(str(getattr(block, "text", "")))
    return "\n".join(parts)


def prompt_fingerprint(path: Path) -> str:
    """A cache token identifying one prompt file's exact contents.

    Combines the file's name, its declared version and a hash of its body, so
    each prompt has its own cache namespace. Editing one prompt therefore
    invalidates only its own cached answers: a change to the classifier prompt
    does not re-pay the fifteen document extractions, and an edit that forgets
    to bump the version still misses the cache (decision D41).
    """
    version, body = load_prompt(path)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    return f"{path.name}@{version}+{digest}"


def load_prompt(path: Path) -> tuple[str, str]:
    """Read a prompt file, returning its version and body.

    The file starts with a YAML front-matter block holding at least
    ``version:``. The version is part of every cache key, so editing a prompt
    invalidates its cached answers instead of serving stale ones.
    """
    text = path.read_text(encoding="utf-8")
    version = "0"
    body = text
    if text.startswith("---"):
        _, _, rest = text.partition("---")
        front, separator, body = rest.partition("---")
        if not separator:
            body = rest
        for line in front.splitlines():
            name, _, value = line.partition(":")
            if name.strip() == "version":
                version = value.strip().strip("\"'") or "0"
    return (version, body.strip())
