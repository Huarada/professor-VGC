"""Bound transient Gemini failures without real API requests."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import (
    BadRequest,
    DeadlineExceeded,
    InternalServerError,
    PermissionDenied,
    ServiceUnavailable,
)

from src.adapters.llm.gemini_provider import GeminiProvider
from src.domain.exceptions import ConfigurationError, LLMProviderError
from src.domain.models import ChatMessage


def provider_with(model: Mock) -> GeminiProvider:
    provider = GeminiProvider.__new__(GeminiProvider)
    provider._model_name = "gemini-3.5-flash"
    provider._timeout_seconds = 90.0
    provider._total_timeout_seconds = 180.0
    provider._genai = SimpleNamespace(GenerativeModel=Mock(return_value=model))
    return provider


@pytest.mark.parametrize("failure", [InternalServerError, ServiceUnavailable, DeadlineExceeded])
def test_transient_failure_recovers_with_the_same_request(monkeypatch, failure):
    sleep = Mock()
    monkeypatch.setattr("src.adapters.llm.gemini_provider.time.sleep", sleep)
    model = Mock()
    model.generate_content.side_effect = [failure("temporary"), SimpleNamespace(text="Recovered")]
    answer = provider_with(model).complete(
        system="Explain evidence", messages=[ChatMessage(role="user", content="Evidence")],
    )
    assert answer == "Recovered"
    assert model.generate_content.call_count == 2
    assert model.generate_content.call_args_list[0] == model.generate_content.call_args_list[1]
    assert dict(model.generate_content.call_args.kwargs["request_options"]) == {"retry": None, "timeout": 90.0}
    sleep.assert_called_once_with(1)


def test_persistent_internal_error_stops_after_three_attempts(monkeypatch):
    sleep = Mock()
    monkeypatch.setattr("src.adapters.llm.gemini_provider.time.sleep", sleep)
    model = Mock()
    model.generate_content.side_effect = InternalServerError("Internal error encountered")
    with pytest.raises(LLMProviderError, match="Gemini request failed"):
        provider_with(model).complete(system="Explain", messages=[])
    assert model.generate_content.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [1, 2]


@pytest.mark.parametrize("failure", [BadRequest, PermissionDenied])
def test_permanent_failure_is_not_retried(monkeypatch, failure):
    sleep = Mock()
    monkeypatch.setattr("src.adapters.llm.gemini_provider.time.sleep", sleep)
    model = Mock()
    model.generate_content.side_effect = failure("permanent")
    with pytest.raises(LLMProviderError):
        provider_with(model).complete(system="Explain", messages=[])
    assert model.generate_content.call_count == 1
    sleep.assert_not_called()


def test_empty_completion_stays_an_explicit_error():
    model = Mock()
    model.generate_content.return_value = SimpleNamespace(text="")
    with pytest.raises(LLMProviderError, match="empty completion"):
        provider_with(model).complete(system="Explain", messages=[])
    assert model.generate_content.call_count == 1


def test_slow_attempts_share_one_deadline_and_reduce_the_next_timeout(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("src.adapters.llm.gemini_provider.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("src.adapters.llm.gemini_provider.time.sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    timeouts = []
    def slow(history, *, request_options):
        timeouts.append(request_options["timeout"])
        assert request_options["retry"] is None
        clock[0] += request_options["timeout"]
        raise DeadlineExceeded("remote deadline")
    model = Mock()
    model.generate_content.side_effect = slow
    with pytest.raises(LLMProviderError, match=r"timed out.*attempts=2.*180s"):
        provider_with(model).complete(system="Explain", messages=[])
    assert timeouts == [90.0, 89.0]
    assert clock[0] == 180.0


def test_a_short_total_budget_clips_the_first_request_and_never_sleeps_past_it(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("src.adapters.llm.gemini_provider.time.monotonic", lambda: clock[0])
    sleep = Mock()
    monkeypatch.setattr("src.adapters.llm.gemini_provider.time.sleep", sleep)
    def slow(history, *, request_options):
        assert request_options["timeout"] == 0.5
        clock[0] = 0.5
        raise ServiceUnavailable("busy")
    model = Mock()
    model.generate_content.side_effect = slow
    provider = provider_with(model)
    provider._total_timeout_seconds = 0.5
    with pytest.raises(LLMProviderError, match=r"timed out.*attempts=1"):
        provider.complete(system="Explain", messages=[])
    sleep.assert_not_called()
    assert model.generate_content.call_count == 1


def test_a_response_after_the_total_deadline_is_not_accepted(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("src.adapters.llm.gemini_provider.time.monotonic", lambda: clock[0])
    def late(*args, **kwargs):
        clock[0] = 181.0
        return SimpleNamespace(text="Too late")
    model = Mock()
    model.generate_content.side_effect = late
    with pytest.raises(LLMProviderError, match="timed out"):
        provider_with(model).complete(system="Explain", messages=[])
    assert model.generate_content.call_count == 1


def test_retry_logs_metadata_without_prompt_or_exception_payload(monkeypatch, caplog):
    monkeypatch.setattr("src.adapters.llm.gemini_provider.time.sleep", Mock())
    model = Mock()
    model.generate_content.side_effect = [DeadlineExceeded("private payload sentinel"), SimpleNamespace(text="Recovered")]
    provider_with(model).complete(system="private prompt sentinel", messages=[])
    assert "attempt=1" in caplog.text and "DeadlineExceeded" in caplog.text
    assert "private payload sentinel" not in caplog.text and "private prompt sentinel" not in caplog.text


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout_is_rejected_before_loading_the_sdk(value):
    with pytest.raises(ConfigurationError, match="finite positive"):
        GeminiProvider(api_key="test-only", timeout_seconds=value)


def test_container_passes_configured_gemini_deadlines(monkeypatch):
    from src.config import Settings
    from src.services.container import Container

    constructor = Mock()
    monkeypatch.setattr("src.services.container.GeminiProvider", constructor)
    settings = Settings(_env_file=None, gemini_api_key="test-only", gemini_request_timeout_seconds=75,
        gemini_total_timeout_seconds=130)
    Container(settings).build_llm("gemini")
    assert constructor.call_args.kwargs["timeout_seconds"] == 75
    assert constructor.call_args.kwargs["total_timeout_seconds"] == 130
