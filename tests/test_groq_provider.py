"""Tests for GroqProvider against a mocked HTTP transport (no live Groq
API key or network access required)."""

from __future__ import annotations

import json

import httpx
import pytest

from eaal_platform.ai.groq_provider import GroqProvider
from eaal_platform.ai.provider import GenerationContext, Purpose


def _client_with_handler(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://test-groq",
        headers={"Authorization": "Bearer test-key"},
    )


def test_ping_true_on_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/models"
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"data": []})

    provider = GroqProvider(api_key="test-key", client=_client_with_handler(handler))
    assert provider.ping() is True


def test_ping_false_on_401() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    provider = GroqProvider(api_key="bad-key", client=_client_with_handler(handler))
    assert provider.ping() is False


def test_ping_false_on_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = GroqProvider(api_key="test-key", client=_client_with_handler(handler))
    assert provider.ping() is False


def test_generate_returns_text_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat/completions"
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello there"}}]})

    provider = GroqProvider(api_key="test-key", client=_client_with_handler(handler))
    result = provider.generate("hi", GenerationContext(), Purpose.CHAT)

    assert result.available is True
    assert result.text == "hello there"
    assert result.error is None


def test_generate_marks_unavailable_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    provider = GroqProvider(api_key="test-key", client=_client_with_handler(handler))
    result = provider.generate("hi", GenerationContext(), Purpose.CHAT)

    assert result.available is False
    assert result.text == ""
    assert result.error is not None


def test_generate_marks_unavailable_on_malformed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = GroqProvider(api_key="test-key", client=_client_with_handler(handler))
    result = provider.generate("hi", GenerationContext(), Purpose.CHAT)

    assert result.available is False
    assert result.error is not None


def test_generate_marks_unavailable_on_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = GroqProvider(api_key="test-key", client=_client_with_handler(handler))
    result = provider.generate("hi", GenerationContext(), Purpose.CHAT)

    assert result.available is False
    assert result.error is not None


def test_rubric_scoring_uses_low_temperature_and_json_response_format() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    provider = GroqProvider(api_key="test-key", client=_client_with_handler(handler))
    provider.generate("score this", GenerationContext(), Purpose.RUBRIC_SCORING)

    body = captured["body"]
    assert body["temperature"] == pytest.approx(0.1)  # type: ignore[index]
    assert body["response_format"] == {"type": "json_object"}  # type: ignore[index]


def test_chat_purpose_does_not_set_temperature_or_response_format() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = GroqProvider(api_key="test-key", client=_client_with_handler(handler))
    provider.generate("hi", GenerationContext(), Purpose.CHAT)

    body = captured["body"]
    assert "temperature" not in body  # type: ignore[operator]
    assert "response_format" not in body  # type: ignore[operator]


def test_provider_name_and_model_name() -> None:
    client = _client_with_handler(lambda r: httpx.Response(200))
    provider = GroqProvider(api_key="test-key", model="llama-3.3-70b-versatile", client=client)
    assert provider.provider_name == "groq"
    assert provider.model_name == "llama-3.3-70b-versatile"


def test_api_key_never_appears_in_request_body() -> None:
    """The key must travel only in the Authorization header, never leak
    into the JSON payload where it might end up logged or stored."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = GroqProvider(api_key="super-secret-key", client=_client_with_handler(handler))
    provider.generate("hi", GenerationContext(), Purpose.CHAT)

    assert "super-secret-key" not in json.dumps(captured["body"])
