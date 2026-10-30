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
from src.domain.exceptions import LLMProviderError
from src.domain.models import ChatMessage


def provider_with(model: Mock) -> GeminiProvider:
    provider = GeminiProvider.__new__(GeminiProvider)
    provider._model_name = "gemini-3.5-flash"
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
    assert dict(model.generate_content.call_args.kwargs["request_options"]) == {"retry": None, "timeout": 30}
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
