"""Tests for OllamaProvider against a mocked HTTP transport (no live
Ollama server required)."""

from __future__ import annotations

import httpx
import pytest

from eaal_platform.ai.ollama_provider import OllamaProvider
from eaal_platform.ai.provider import GenerationContext, Purpose


def _client_with_handler(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test-ollama")


def test_ping_true_on_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": []})

    provider = OllamaProvider(client=_client_with_handler(handler))
    assert provider.ping() is True


def test_ping_false_on_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = OllamaProvider(client=_client_with_handler(handler))
    assert provider.ping() is False


def test_generate_returns_text_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/generate"
        return httpx.Response(200, json={"response": "hello there"})

    provider = OllamaProvider(client=_client_with_handler(handler))
    result = provider.generate("hi", GenerationContext(), Purpose.CHAT)

    assert result.available is True
    assert result.text == "hello there"
    assert result.error is None


def test_generate_marks_unavailable_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    provider = OllamaProvider(client=_client_with_handler(handler))
    result = provider.generate("hi", GenerationContext(), Purpose.CHAT)

    assert result.available is False
    assert result.text == ""
    assert result.error is not None


def test_generate_marks_unavailable_on_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = OllamaProvider(client=_client_with_handler(handler))
    result = provider.generate("hi", GenerationContext(), Purpose.CHAT)

    assert result.available is False
    assert result.error is not None


def test_rubric_scoring_uses_low_temperature_and_json_format() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": "{}"})

    provider = OllamaProvider(client=_client_with_handler(handler))
    provider.generate("score this", GenerationContext(), Purpose.RUBRIC_SCORING)

    body = captured["body"]
    assert body["format"] == "json"  # type: ignore[index]
    assert body["options"]["temperature"] == pytest.approx(0.1)  # type: ignore[index]


def test_chat_purpose_does_not_set_format_or_options() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": "ok"})

    provider = OllamaProvider(client=_client_with_handler(handler))
    provider.generate("hi", GenerationContext(), Purpose.CHAT)

    body = captured["body"]
    assert "format" not in body  # type: ignore[operator]
    assert "options" not in body  # type: ignore[operator]


def test_provider_name_and_model_name() -> None:
    client = _client_with_handler(lambda r: httpx.Response(200))
    provider = OllamaProvider(model="qwen3:8b", client=client)
    assert provider.provider_name == "ollama"
    assert provider.model_name == "qwen3:8b"
