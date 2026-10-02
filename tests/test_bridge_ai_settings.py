"""Tests for switching the AI assistant between local Ollama and hosted Groq."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

import eaal_platform.api.bridge as bridge_module
from eaal_platform.ai.provider import AIProvider, GenerationContext, GenerationResult, Purpose
from eaal_platform.api.bridge import CavyApi
from eaal_platform.events.logger import EventLogger

_VALID_KEY = "gsk_valid_test_key"


class _FakeBackend(AIProvider):
    name = "fake"
    reachable = True

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    def generate(
        self, prompt: str, context: GenerationContext, purpose: Purpose
    ) -> GenerationResult:
        return GenerationResult(text="hi", available=True)

    def ping(self) -> bool:
        return self.reachable

    @property
    def provider_name(self) -> str:
        return self.name

    @property
    def model_name(self) -> str | None:
        return f"{self.name}-model"


class _FakeGroq(_FakeBackend):
    name = "groq"

    def ping(self) -> bool:
        return self.api_key == _VALID_KEY


class _FakeOllama(_FakeBackend):
    name = "ollama"
    reachable = False


@pytest.fixture
def api(db_session_factory: sessionmaker[OrmSession], monkeypatch: pytest.MonkeyPatch) -> CavyApi:
    monkeypatch.setattr(bridge_module, "GroqProvider", _FakeGroq)
    monkeypatch.setattr(bridge_module, "OllamaProvider", _FakeOllama)
    logger = EventLogger(db_session_factory)
    return CavyApi(db_session_factory, logger, ai_provider=_FakeOllama())


def test_get_ai_settings_reports_current_provider(api: CavyApi) -> None:
    assert api.get_ai_settings() == {
        "provider": "ollama",
        "model": "ollama-model",
        "available": False,
        "problem": "The AI assistant isn't reachable right now.",
    }


def test_switch_to_groq_with_valid_key(api: CavyApi) -> None:
    result = api.set_ai_provider("groq", f"  {_VALID_KEY}  ")

    assert result == {
        "ok": True,
        "provider": "groq",
        "model": "groq-model",
        "available": True,
        "problem": None,
    }
    assert api.ping_ai() is True


def test_rejected_groq_key_keeps_previous_provider(api: CavyApi) -> None:
    result = api.set_ai_provider("groq", "gsk_wrong")

    assert result["ok"] is False
    assert "Groq" in result["error"]
    assert api.get_ai_settings()["provider"] == "ollama"


def test_blank_groq_key_is_rejected_without_contacting_groq(
    api: CavyApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _must_not_construct(*args: object, **kwargs: object) -> None:
        raise AssertionError("GroqProvider should not be built for a blank key")

    monkeypatch.setattr(bridge_module, "GroqProvider", _must_not_construct)

    assert api.set_ai_provider("groq", "   ") == {"ok": False, "error": "Enter a Groq API key."}


def test_switch_back_to_ollama_even_if_not_running(api: CavyApi) -> None:
    api.set_ai_provider("groq", _VALID_KEY)

    result = api.set_ai_provider("ollama")

    assert result["ok"] is True
    assert (result["provider"], result["available"]) == ("ollama", False)
    assert result["problem"]


def test_groq_key_is_never_returned_to_the_frontend(api: CavyApi) -> None:
    result = api.set_ai_provider("groq", _VALID_KEY)

    assert _VALID_KEY not in repr(result)
    assert _VALID_KEY not in repr(api.get_ai_settings())


def test_unknown_provider_raises(api: CavyApi) -> None:
    with pytest.raises(ValueError, match="Unknown AI provider"):
        api.set_ai_provider("openai")
