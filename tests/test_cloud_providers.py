"""OpenAI, Claude, Gemini and Grok providers, against simulated APIs.

The replies follow each company's documented format. These tests prove the
app sends what each API expects and reads what it sends back; they can't prove
a real key works (that needs a real account).
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from eaal_platform.ai import cloud_providers as cp
from eaal_platform.ai.provider import GenerationContext, Purpose

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok(body: object) -> httpx.Response:
    return httpx.Response(200, json=body)


CTX = GenerationContext(task_description="Print 1 to 5")


# -- model lists and key checks ---------------------------------------------------------------


def _openai_models() -> dict[str, object]:
    ids = [
        "gpt-4o", "gpt-4o-mini", "gpt-4o-mini-2024-07-18", "gpt-4o-audio-preview",
        "gpt-4o-realtime-preview", "gpt-4o-mini-transcribe", "text-embedding-3-small",
        "dall-e-3", "whisper-1", "o3-mini", "gpt-image-1", "omni-moderation-latest",
        "gpt-3.5-turbo-instruct", "tts-1",
    ]  # fmt: skip
    return {"data": [{"id": i} for i in ids]}


def test_openai_lists_only_chat_models_and_prefers_a_small_one() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        return _ok(_openai_models())

    result = cp.check_key("openai", "  sk-test  ", _client(handler))

    assert seen == {"url": "https://api.openai.com/v1/models", "auth": "Bearer sk-test"}
    assert result.ok
    assert result.default == "gpt-4o-mini"
    assert result.models[0] == "gpt-4o-mini"  # the recommended one comes first
    assert set(result.models) == {"gpt-4o", "gpt-4o-mini", "gpt-4o-mini-2024-07-18", "o3-mini"}


def test_anthropic_models_and_headers() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"], seen["version"] = (
            request.headers["x-api-key"],
            request.headers["anthropic-version"],
        )
        seen["path"] = request.url.path
        return _ok(
            {
                "data": [
                    {"id": "claude-sonnet-5-5"},
                    {"id": "claude-haiku-4-5-20251001"},
                    {"id": "claude-opus-5-5"},
                ]
            }
        )

    result = cp.check_key("anthropic", "sk-ant-x", _client(handler))
    assert seen == {"key": "sk-ant-x", "version": "2023-06-01", "path": "/v1/models"}
    assert result.default == "claude-haiku-4-5-20251001"
    assert result.models[0] == "claude-haiku-4-5-20251001"


def test_gemini_models_keep_only_text_models_and_avoid_experimental_defaults() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "AIza-test"
        gen = ["generateContent", "countTokens"]
        return _ok(
            {
                "models": [
                    {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": gen},
                    {
                        "name": "models/gemini-2.5-flash-preview-05-20",
                        "supportedGenerationMethods": gen,
                    },
                    {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": gen},
                    {
                        "name": "models/text-embedding-004",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                    {
                        "name": "models/gemini-embedding-001",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                    {"name": "models/gemini-2.0-flash-live", "supportedGenerationMethods": gen},
                    {"name": "models/imagen-3.0", "supportedGenerationMethods": ["predict"]},
                ]
            }
        )

    result = cp.check_key("gemini", "AIza-test", _client(handler))
    assert result.default == "gemini-2.5-flash"
    assert sorted(result.models) == [
        "gemini-2.5-flash",
        "gemini-2.5-flash-preview-05-20",
        "gemini-2.5-pro",
    ]


def test_grok_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.x.ai/v1/models"
        return _ok({"data": [{"id": "grok-3"}, {"id": "grok-3-mini"}, {"id": "grok-2-image"}]})

    result = cp.check_key("xai", "xai-k", _client(handler))
    assert result.default == "grok-3-mini"
    assert "grok-2-image" not in result.models


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {"error": {"message": "Incorrect API key"}}, "rejected the API key"),
        (403, {}, "rejected the API key"),
        (429, {"error": {"message": "quota"}}, "rate-limited or out of credit"),
        (500, {}, "having problems"),
        (404, {}, "doesn't recognise that model"),
        (400, {"error": {"message": "Bad thing\nsecond line"}}, "refused the request: Bad thing"),
        # Gemini and Grok say "bad key" as a 400, not a 401:
        (
            400,
            {"error": {"message": "API key not valid. Please pass a valid API key."}},
            "rejected the API key",
        ),
        (400, {"error": "Incorrect API key provided."}, "rejected the API key"),
    ],
)
def test_failures_are_explained_in_plain_words(status: int, body: object, expected: str) -> None:
    result = cp.check_key("openai", "sk-x", _client(lambda r: httpx.Response(status, json=body)))
    assert not result.ok
    assert result.error is not None
    assert expected in result.error
    assert "OpenAI" in result.error


def test_no_internet_and_odd_replies_and_empty_keys() -> None:
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    assert "internet connection" in (cp.check_key("gemini", "k", _client(offline)).error or "")
    garbled = cp.check_key("openai", "k", _client(lambda r: _ok({"unexpected": 1})))
    assert "doesn't understand" in (garbled.error or "")
    no_models = cp.check_key("openai", "k", _client(lambda r: _ok({"data": [{"id": "dall-e-3"}]})))
    assert "no chat models" in (no_models.error or "")
    assert cp.check_key("openai", "   ").error == "Paste your API key first."
    assert cp.check_key("nobody", "k").error == "Unknown AI provider “nobody”."


# -- answering ------------------------------------------------------------------------------------


def test_openai_chat_request_and_reply() -> None:
    sent: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["url"] = str(request.url)
        sent["body"] = json.loads(request.content)
        return _ok({"choices": [{"message": {"content": "Use range(1, 6)."}}]})

    provider = cp.make_cloud_provider("openai", "sk-1", "gpt-4o-mini", _client(handler))
    result = provider.generate("How do I loop?", CTX, Purpose.CHAT)

    assert result.available and result.text == "Use range(1, 6)."
    assert sent["url"] == "https://api.openai.com/v1/chat/completions"
    body = sent["body"]
    assert isinstance(body, dict)
    assert body["model"] == "gpt-4o-mini"
    assert "Print 1 to 5" in body["messages"][0]["content"]  # the task context is included
    assert "temperature" not in body  # chat keeps the provider's own sampling
    assert (provider.provider_name, provider.model_name) == ("openai", "gpt-4o-mini")


def test_rubric_scoring_asks_for_json_and_low_temperature_then_falls_back() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "temperature" in body:  # a model that refuses tuning options
            return httpx.Response(
                400, json={"error": {"message": "Unsupported parameter: temperature"}}
            )
        return _ok({"choices": [{"message": {"content": '{"score": 1}'}}]})

    provider = cp.make_cloud_provider("openai", "sk-1", "o3-mini", _client(handler))
    result = provider.generate("rate this", CTX, Purpose.RUBRIC_SCORING)

    assert result.available and result.text == '{"score": 1}'
    assert bodies[0]["temperature"] == 0.1
    assert bodies[0]["response_format"] == {"type": "json_object"}
    assert "temperature" not in bodies[1] and "response_format" not in bodies[1]


def test_claude_request_and_reply() -> None:
    sent: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["url"], sent["headers"], sent["body"] = (
            str(request.url),
            dict(request.headers),
            json.loads(request.content),
        )
        return _ok(
            {"content": [{"type": "text", "text": "Hello "}, {"type": "text", "text": "there"}]}
        )

    provider = cp.make_cloud_provider(
        "anthropic", "sk-ant-1", "claude-haiku-4-5-20251001", _client(handler)
    )
    chat = provider.generate("Hi", CTX, Purpose.CHAT)
    rubric = provider.generate("Rate", CTX, Purpose.RUBRIC_SCORING)

    assert chat.text == "Hello there"
    assert sent["url"] == "https://api.anthropic.com/v1/messages"
    headers = sent["headers"]
    assert isinstance(headers, dict)
    assert headers["x-api-key"] == "sk-ant-1"
    body = sent["body"]
    assert isinstance(body, dict)
    assert body["max_tokens"] == 1024 and body["temperature"] == 0.1  # the rubric call (last sent)
    assert rubric.available


def test_gemini_request_and_reply() -> None:
    sent: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["url"], sent["body"] = str(request.url), json.loads(request.content)
        return _ok({"candidates": [{"content": {"parts": [{"text": "A "}, {"text": "loop"}]}}]})

    provider = cp.make_cloud_provider("gemini", "AIza-1", "gemini-2.5-flash", _client(handler))
    result = provider.generate("Hi", CTX, Purpose.RUBRIC_SCORING)

    assert result.text == "A loop"
    assert sent["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    )
    body = sent["body"]
    assert isinstance(body, dict)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["contents"][0]["parts"][0]["text"].startswith("You are a coding assistant")


def test_gemini_blocked_and_empty_replies() -> None:
    provider = cp.make_cloud_provider(
        "gemini",
        "k",
        "gemini-2.5-flash",
        _client(lambda r: _ok({"promptFeedback": {"blockReason": "SAFETY"}})),
    )
    result = provider.generate("x", CTX, Purpose.CHAT)
    assert not result.available
    assert "SAFETY" in (result.error or "")


def test_grok_uses_its_own_address() -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return _ok({"choices": [{"message": {"content": "hi"}}]})

    provider = cp.make_cloud_provider("xai", "xai-1", "grok-3-mini", _client(handler))
    assert provider.generate("x", CTX, Purpose.CHAT).text == "hi"
    assert urls == ["https://api.x.ai/v1/chat/completions"]


@pytest.mark.parametrize("key", ["openai", "anthropic", "gemini", "xai"])
def test_every_provider_reports_failures_instead_of_raising(key: str) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    provider = cp.make_cloud_provider(key, "k", "some-model", _client(broken))
    result = provider.generate("x", CTX, Purpose.CHAT)
    assert not result.available
    assert "internet connection" in (result.error or "")

    rejected = cp.make_cloud_provider(
        key, "k", "some-model", _client(lambda r: httpx.Response(401, json={}))
    ).generate("x", CTX, Purpose.CHAT)
    assert "rejected the API key" in (rejected.error or "")

    garbled = cp.make_cloud_provider(
        key, "k", "some-model", _client(lambda r: _ok({"nonsense": True}))
    ).generate("x", CTX, Purpose.CHAT)
    assert not garbled.available


def test_a_model_must_be_chosen_and_diagnose_checks_it() -> None:
    no_model = cp.make_cloud_provider("openai", "k", None, _client(lambda r: _ok(_openai_models())))
    assert "No model" in (no_model.generate("x", CTX, Purpose.CHAT).error or "")

    client = _client(lambda r: _ok(_openai_models()))
    assert cp.make_cloud_provider("openai", "k", "gpt-4o", client).diagnose() is None
    missing = cp.make_cloud_provider("openai", "k", "gpt-9-imaginary", client).diagnose()
    assert "doesn't offer the model" in (missing or "")


def test_provider_catalogue() -> None:
    assert cp.STUDENT_PROVIDER_KEYS == ("gemini", "anthropic", "openai", "xai")
    assert all(k in cp.PROVIDERS for k in cp.STUDENT_PROVIDER_KEYS)
    assert cp.PROVIDERS["ollama"].needs_key is False
    assert all(info.help_url.startswith("https://") for info in cp.PROVIDERS.values())
