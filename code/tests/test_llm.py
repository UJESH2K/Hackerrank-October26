"""Tests for the model gateway: caching, retry, fallback and token accounting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fake_llm import FakeClient

from audit.llm import (
    API_KEY_ENV,
    PURPOSE_CLASSIFY,
    CacheStore,
    CallRecord,
    LlmGateway,
    LlmSettings,
    ModelPricing,
    UsageTracker,
    extract_json,
    load_prompt,
)

SETTINGS = LlmSettings(
    classifier_model="model-cheap",
    vision_model="model-vision",
    prices={
        "model-cheap": ModelPricing(0.10, 0.50),
        "model-vision": ModelPricing(4.00, 20.00),
    },
    max_output_tokens=512,
)

BLOCKS = [{"type": "text", "text": "classify this"}]


def always_valid(payload: object) -> bool:
    """Accept any parsed JSON."""
    return payload is not None


def gateway(
    tmp_path: Path, client: FakeClient | None = None, enabled: bool = True
) -> LlmGateway:
    """A gateway over a temporary cache directory."""
    return LlmGateway(
        SETTINGS, cache_dir=tmp_path / "llm", client=client, enabled=enabled
    )


# -- availability ---------------------------------------------------------- #


def test_no_key_and_no_client_means_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    instance = gateway(tmp_path)
    assert not instance.available
    assert API_KEY_ENV in instance.unavailable_reason()


def test_disabled_gateway_is_unavailable_even_with_a_client(tmp_path: Path) -> None:
    """``--no-llm`` must make no calls, whatever the environment holds."""
    client = FakeClient.with_replies('{"ok": true}')
    instance = gateway(tmp_path, client, enabled=False)
    assert not instance.available
    assert instance.call_json(
        PURPOSE_CLASSIFY, "p@1", "system", BLOCKS, always_valid
    ) is None
    assert client.calls == 0


def test_a_supplied_client_makes_the_gateway_available(tmp_path: Path) -> None:
    instance = gateway(tmp_path, FakeClient.with_replies("{}"))
    assert instance.available
    assert instance.unavailable_reason() == ""


# -- the happy path -------------------------------------------------------- #


def test_a_call_returns_parsed_json_and_records_usage(tmp_path: Path) -> None:
    client = FakeClient.with_replies('{"verdict": "yes"}')
    instance = gateway(tmp_path, client)
    assert instance.call_json(
        PURPOSE_CLASSIFY, "p@1", "system", BLOCKS, always_valid
    ) == {"verdict": "yes"}
    assert instance.tracker.api_calls == 1
    assert instance.tracker.cache_hits == 0
    assert instance.tracker.input_tokens == 100
    assert instance.tracker.output_tokens == 20


def test_sampling_parameters_are_never_sent(tmp_path: Path) -> None:
    """``temperature`` is rejected by both configured models, so it must be absent."""
    client = FakeClient.with_replies("{}")
    gateway(tmp_path, client).call_json(
        PURPOSE_CLASSIFY, "p@1", "system", BLOCKS, always_valid
    )
    request = client.messages.requests[0]
    assert "temperature" not in request
    assert "top_p" not in request
    assert "top_k" not in request
    assert request["output_config"]["effort"] == SETTINGS.classifier_effort


def test_the_purpose_selects_the_model(tmp_path: Path) -> None:
    client = FakeClient.with_replies("{}", "{}")
    instance = gateway(tmp_path, client)
    instance.call_json(PURPOSE_CLASSIFY, "p@1", "s", BLOCKS, always_valid)
    instance.call_json("extract", "p@1", "s", BLOCKS, always_valid)
    assert client.messages.requests[0]["model"] == "model-cheap"
    assert client.messages.requests[1]["model"] == "model-vision"


# -- caching --------------------------------------------------------------- #


def test_a_repeat_call_is_served_from_disk_and_costs_nothing(tmp_path: Path) -> None:
    client = FakeClient.with_replies('{"verdict": "yes"}')
    first = gateway(tmp_path, client)
    first.call_json(PURPOSE_CLASSIFY, "p@1", "system", BLOCKS, always_valid)

    second = gateway(tmp_path, client)
    assert second.call_json(
        PURPOSE_CLASSIFY, "p@1", "system", BLOCKS, always_valid
    ) == {"verdict": "yes"}
    assert client.calls == 1
    assert second.tracker.cache_hits == 1
    assert second.tracker.api_calls == 0
    assert second.tracker.input_tokens == 0
    assert second.tracker.output_tokens == 0
    assert not second.tracker.is_cold


def test_a_prompt_version_change_misses_the_cache(tmp_path: Path) -> None:
    """Editing a prompt must not serve answers from the previous version."""
    client = FakeClient.with_replies('{"v": 1}', '{"v": 2}')
    instance = gateway(tmp_path, client)
    assert instance.call_json(PURPOSE_CLASSIFY, "p@1", "s", BLOCKS, always_valid) == {
        "v": 1
    }
    assert instance.call_json(PURPOSE_CLASSIFY, "p@2", "s", BLOCKS, always_valid) == {
        "v": 2
    }
    assert client.calls == 2


def test_a_different_input_misses_the_cache(tmp_path: Path) -> None:
    client = FakeClient.with_replies('{"a": 1}', '{"b": 2}')
    instance = gateway(tmp_path, client)
    instance.call_json(PURPOSE_CLASSIFY, "p@1", "s", BLOCKS, always_valid)
    instance.call_json(
        PURPOSE_CLASSIFY, "p@1", "s", [{"type": "text", "text": "other"}], always_valid
    )
    assert client.calls == 2


def test_the_cache_key_is_stable_across_key_ordering() -> None:
    first = CacheStore.key("m", "p@1", {"a": 1, "b": 2})
    second = CacheStore.key("m", "p@1", {"b": 2, "a": 1})
    assert first == second


def test_the_cache_key_depends_on_the_model() -> None:
    assert CacheStore.key("m1", "p@1", {"a": 1}) != CacheStore.key("m2", "p@1", {"a": 1})


def test_caching_can_be_turned_off(tmp_path: Path) -> None:
    settings = LlmSettings(
        classifier_model="model-cheap",
        vision_model="model-vision",
        prices={},
        cache_enabled=False,
    )
    client = FakeClient.with_replies('{"a": 1}', '{"a": 1}')
    instance = LlmGateway(settings, cache_dir=tmp_path / "llm", client=client)
    instance.call_json(PURPOSE_CLASSIFY, "p@1", "s", BLOCKS, always_valid)
    instance.call_json(PURPOSE_CLASSIFY, "p@1", "s", BLOCKS, always_valid)
    assert client.calls == 2


# -- invalid JSON, retry and fallback -------------------------------------- #


def test_invalid_json_is_retried_once_then_succeeds(tmp_path: Path) -> None:
    client = FakeClient.with_replies("I think the answer is yes", '{"verdict": "yes"}')
    instance = gateway(tmp_path, client)
    assert instance.call_json(
        PURPOSE_CLASSIFY, "p@1", "system", BLOCKS, always_valid
    ) == {"verdict": "yes"}
    assert client.calls == 2
    # Both attempts are billed, and both are recorded.
    assert instance.tracker.api_calls == 2
    assert instance.tracker.input_tokens == 200


def test_the_retry_carries_a_repair_instruction(tmp_path: Path) -> None:
    client = FakeClient.with_replies("not json", '{"ok": 1}')
    instance = gateway(tmp_path, client)
    instance.call_json(PURPOSE_CLASSIFY, "p@1", "system", BLOCKS, always_valid)
    assert LlmGateway.REPAIR_INSTRUCTION in client.messages.requests[1]["system"]


def test_two_invalid_replies_fall_back_to_none(tmp_path: Path) -> None:
    client = FakeClient.with_replies("nope", "still nope")
    instance = gateway(tmp_path, client)
    assert instance.call_json(
        PURPOSE_CLASSIFY, "p@1", "system", BLOCKS, always_valid
    ) is None
    assert client.calls == 2


def test_a_schema_violation_is_retried_like_invalid_json(tmp_path: Path) -> None:
    client = FakeClient.with_replies('{"wrong": 1}', '{"verdict": "yes"}')
    instance = gateway(tmp_path, client)

    def needs_verdict(payload: object) -> bool:
        return isinstance(payload, dict) and "verdict" in payload

    assert instance.call_json(
        PURPOSE_CLASSIFY, "p@1", "s", BLOCKS, needs_verdict
    ) == {"verdict": "yes"}
    assert client.calls == 2


def test_an_invalid_reply_is_never_cached(tmp_path: Path) -> None:
    client = FakeClient.with_replies("nope", "still nope", '{"ok": 1}')
    instance = gateway(tmp_path, client)
    instance.call_json(PURPOSE_CLASSIFY, "p@1", "s", BLOCKS, always_valid)
    assert instance.call_json(PURPOSE_CLASSIFY, "p@1", "s", BLOCKS, always_valid) == {
        "ok": 1
    }


def test_a_transport_failure_is_recorded_and_does_not_raise(tmp_path: Path) -> None:
    """A failed call must not fail the audit."""
    client = FakeClient.with_replies("{}", raises=True)
    instance = gateway(tmp_path, client)
    assert instance.call_json(
        PURPOSE_CLASSIFY, "p@1", "s", BLOCKS, always_valid
    ) is None
    assert instance.tracker.api_calls == 2
    assert instance.tracker.input_tokens == 0


# -- JSON extraction ------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('[{"a": 1}]', [{"a": 1}]),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n[1, 2]\n```', [1, 2]),
        ('Here it is: {"a": 1}', {"a": 1}),
        ("", None),
        ("no json at all", None),
    ],
)
def test_extract_json(text: str, expected: object) -> None:
    assert extract_json(text) == expected


# -- usage accounting ------------------------------------------------------ #


def test_usage_totals_and_cost() -> None:
    tracker = UsageTracker()
    tracker.record(
        CallRecord("model-cheap", PURPOSE_CLASSIFY, "p@1", 1000, 100, False, 10)
    )
    tracker.record(CallRecord("model-vision", "extract", "p@1", 2000, 500, False, 20))
    tracker.record(CallRecord("model-cheap", PURPOSE_CLASSIFY, "p@1", 0, 0, True, 0))

    assert tracker.calls == 3
    assert tracker.api_calls == 2
    assert tracker.cache_hits == 1
    assert tracker.input_tokens == 3000
    assert tracker.output_tokens == 600
    expected = (1000 * 0.10 + 100 * 0.50 + 2000 * 4.00 + 500 * 20.00) / 1_000_000
    assert tracker.cost(SETTINGS) == pytest.approx(expected)


def test_usage_is_grouped_by_model_and_purpose() -> None:
    tracker = UsageTracker()
    tracker.record(CallRecord("a", PURPOSE_CLASSIFY, "p", 10, 1, False, 1))
    tracker.record(CallRecord("a", PURPOSE_CLASSIFY, "p", 0, 0, True, 0))
    tracker.record(CallRecord("b", "extract", "p", 20, 2, False, 1))

    by_model = tracker.by_model()
    assert by_model["a"] == {
        "calls": 2,
        "api_calls": 1,
        "cache_hits": 1,
        "input": 10,
        "output": 1,
    }
    assert tracker.by_purpose()["extract"]["input"] == 20


def test_an_empty_run_is_warm() -> None:
    assert not UsageTracker().is_cold


def test_the_run_history_is_appended_not_replaced(tmp_path: Path) -> None:
    path = tmp_path / "usage_run.json"
    first = UsageTracker()
    first.record(CallRecord("a", PURPOSE_CLASSIFY, "p", 10, 1, False, 1))
    first.append_run(path, SETTINGS, cases=32)

    second = UsageTracker()
    second.record(CallRecord("a", PURPOSE_CLASSIFY, "p", 0, 0, True, 0))
    second.append_run(path, SETTINGS, cases=32)

    history = json.loads(path.read_text(encoding="utf-8"))
    assert len(history) == 2
    assert history[0]["run_type"] == "cold"
    assert history[1]["run_type"] == "warm"
    assert history[1]["total_tokens"] == 0


def test_pricing_for_an_unlisted_model_is_zero() -> None:
    assert SETTINGS.pricing_for("unknown").cost(1000, 1000) == 0.0


# -- settings and prompts -------------------------------------------------- #


def test_settings_load_from_the_real_config() -> None:
    import yaml

    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    settings = LlmSettings.from_config(
        yaml.safe_load(config_path.read_text(encoding="utf-8"))
    )
    assert settings.classifier_model
    assert settings.vision_model
    # Every configured model must have a price, or the usage report lies.
    assert settings.pricing_for(settings.classifier_model).input_per_mtok > 0
    assert settings.pricing_for(settings.vision_model).input_per_mtok > 0


def test_prompt_front_matter_supplies_the_version(tmp_path: Path) -> None:
    path = tmp_path / "p.md"
    path.write_text(
        "---\nname: thing\nversion: 3\n---\n\nThe body.\n", encoding="utf-8"
    )
    assert load_prompt(path) == ("3", "The body.")


def test_a_prompt_without_front_matter_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "p.md"
    path.write_text("Just a body.", encoding="utf-8")
    assert load_prompt(path) == ("0", "Just a body.")


def test_the_real_prompts_declare_a_version() -> None:
    prompts = Path(__file__).resolve().parent.parent / "prompts"
    for name in ("request_intent_classifier.md", "evidence_extraction.md"):
        version, body = load_prompt(prompts / name)
        assert version != "0", name
        assert len(body) > 500, name


# -- production fencing ---------------------------------------------------- #


def test_a_production_gateway_refuses_a_substituted_client(tmp_path: Path) -> None:
    """``main.py`` must never be served scripted answers."""
    with pytest.raises(ValueError, match="refuses a substituted client"):
        LlmGateway(
            SETTINGS,
            cache_dir=tmp_path / "llm",
            client=FakeClient.with_replies("{}"),
            require_real_client=True,
        )


def test_a_production_gateway_without_a_key_is_simply_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No key means the deterministic path, not a stand-in."""
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    instance = LlmGateway(SETTINGS, cache_dir=tmp_path / "llm", require_real_client=True)
    assert not instance.available
    assert API_KEY_ENV in instance.unavailable_reason()


def test_a_production_gateway_accepts_no_client_at_all(tmp_path: Path) -> None:
    instance = LlmGateway(SETTINGS, cache_dir=tmp_path / "llm", require_real_client=True)
    assert instance.require_real_client


def test_the_main_entry_point_requires_a_real_client() -> None:
    """Read back from main.py, so the fence cannot be removed unnoticed."""
    source = (Path(__file__).resolve().parent.parent / "main.py").read_text(
        encoding="utf-8"
    )
    assert "require_real_client=True" in source


def test_dotenv_does_not_override_a_real_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real environment variable wins over the .env file."""
    monkeypatch.setenv(API_KEY_ENV, "set-by-the-environment")
    from audit.llm import _load_env_file

    _load_env_file()
    import os

    assert os.environ[API_KEY_ENV] == "set-by-the-environment"


# -- failure visibility ---------------------------------------------------- #


class _AuthError(Exception):
    """Stands in for ``anthropic.AuthenticationError``."""


def test_a_call_failure_is_recorded_not_swallowed(tmp_path: Path) -> None:
    client = FakeClient.with_replies("{}", raises=True)
    instance = gateway(tmp_path, client)
    instance.call_json(PURPOSE_CLASSIFY, "p@1", "s", BLOCKS, always_valid)
    assert instance.errors
    assert "fake transport failure" in instance.errors[0]


def test_a_rejected_key_stops_further_calls(tmp_path: Path) -> None:
    """One bad key must cost one request, not hundreds."""

    def refuse(_request: object) -> str:
        raise _AuthError("Error code: 401 - invalid x-api-key")

    client = FakeClient.with_responder(refuse)
    instance = gateway(tmp_path, client)
    assert instance.call_json(
        PURPOSE_CLASSIFY, "p@1", "s", BLOCKS, always_valid
    ) is None
    # One attempt, and no retry: the retry would be pointless.
    assert client.calls == 1
    assert instance.auth_failed
    assert not instance.available
    assert "rejected the credentials" in instance.unavailable_reason()


def test_a_rejected_key_is_visible_in_the_reason(tmp_path: Path) -> None:
    def refuse(_request: object) -> str:
        raise _AuthError("Error code: 401 - invalid x-api-key")

    instance = gateway(tmp_path, FakeClient.with_responder(refuse))
    instance.call_json(PURPOSE_CLASSIFY, "p@1", "s", BLOCKS, always_valid)
    assert any("401" in error for error in instance.errors)


def test_a_transient_failure_does_not_mark_auth_failed(tmp_path: Path) -> None:
    """A timeout must not disable the model for the rest of the run."""
    client = FakeClient.with_replies("{}", raises=True)
    instance = gateway(tmp_path, client)
    instance.call_json(PURPOSE_CLASSIFY, "p@1", "s", BLOCKS, always_valid)
    assert not instance.auth_failed
    assert instance.available


def test_main_reports_a_failed_call() -> None:
    """The run summary must not hide a failed model call."""
    source = (Path(__file__).resolve().parent.parent / "main.py").read_text(
        encoding="utf-8"
    )
    assert "MODEL CALLS FAILED" in source


# -- per-prompt cache isolation -------------------------------------------- #


def test_each_prompt_file_has_its_own_fingerprint(tmp_path: Path) -> None:
    from audit.llm import prompt_fingerprint

    first = tmp_path / "alpha.md"
    second = tmp_path / "beta.md"
    first.write_text("---\nversion: 1\n---\nAlpha body.", encoding="utf-8")
    second.write_text("---\nversion: 1\n---\nBeta body.", encoding="utf-8")
    assert prompt_fingerprint(first) != prompt_fingerprint(second)
    assert prompt_fingerprint(first).startswith("alpha.md@1+")


def test_editing_a_prompt_changes_its_fingerprint_even_without_a_version_bump(
    tmp_path: Path,
) -> None:
    from audit.llm import prompt_fingerprint

    path = tmp_path / "alpha.md"
    path.write_text("---\nversion: 1\n---\nFirst body.", encoding="utf-8")
    before = prompt_fingerprint(path)
    path.write_text("---\nversion: 1\n---\nSecond body.", encoding="utf-8")
    assert prompt_fingerprint(path) != before


def test_editing_one_prompt_leaves_the_other_fingerprint_alone(tmp_path: Path) -> None:
    """A classifier edit must not re-pay the document extractions."""
    from audit.llm import prompt_fingerprint

    classifier_prompt = tmp_path / "request_intent_classifier.md"
    extraction_prompt = tmp_path / "evidence_extraction.md"
    classifier_prompt.write_text("---\nversion: 1\n---\nClassify.", encoding="utf-8")
    extraction_prompt.write_text("---\nversion: 1\n---\nExtract.", encoding="utf-8")
    extraction_before = prompt_fingerprint(extraction_prompt)

    classifier_prompt.write_text("---\nversion: 2\n---\nClassify better.", encoding="utf-8")
    assert prompt_fingerprint(extraction_prompt) == extraction_before
