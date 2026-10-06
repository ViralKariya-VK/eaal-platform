"""Groq-backed ``AIProvider``: a cloud model backend for machines that can't
run a local model comfortably (an 8B-parameter model is a rough ask on an
ordinary laptop — see the pilot web app, which is the reason this exists).

Talks to Groq's OpenAI-compatible chat-completions API via ``httpx``, the
same way ``OllamaProvider`` talks to Ollama's — same ``build_prompt``
context assembly, same per-``Purpose`` sampling policy, so a session's
signal computations mean the same thing regardless of which provider
happened to serve it.

The API key is supplied by the caller at construction time, never read
from an environment variable here — each pilot participant supplies their
own key for their own session, so there is no single "the app's" key to
default to. Callers must not persist a key handed to this class beyond
that session's process lifetime.
"""

from __future__ import annotations

import httpx

from eaal_platform.ai.prompt_builder import build_prompt
from eaal_platform.ai.provider import AIProvider, GenerationContext, GenerationResult, Purpose

_DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
_DEFAULT_MODEL = "openai/gpt-oss-120b"
# Groq retires models now and then; if the configured one is gone, use the
# first of these that the key can see rather than failing every question.
_FALLBACK_MODELS = ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile")
_DEFAULT_TIMEOUT_SECONDS = 30.0
_PING_TIMEOUT_SECONDS = 5.0

# Matches OllamaProvider's rationale exactly: rubric scoring needs
# reproducible, low-variance output (EAAL framework §6.4); chat keeps the
# provider's own default sampling since a natural-feeling reply, not
# reproducibility, is the goal there.
_RUBRIC_SCORING_TEMPERATURE = 0.1


class GroqProvider(AIProvider):
    """Generates responses using Groq's hosted inference API."""

    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        self._model = model
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def ping(self) -> bool:
        try:
            response = self._client.get("/models", timeout=_PING_TIMEOUT_SECONDS)
            return response.status_code == httpx.codes.OK
        except httpx.HTTPError:
            return False

    def diagnose(self) -> str | None:
        try:
            response = self._client.get("/models", timeout=_PING_TIMEOUT_SECONDS)
        except httpx.HTTPError:
            return "Can't reach Groq. Check your internet connection."
        if response.status_code in (401, 403):
            return "Groq rejected the API key. Check it in Profile."
        if response.status_code != httpx.codes.OK:
            return f"Groq returned an error ({response.status_code}). Try again shortly."
        return None

    def generate(
        self, prompt: str, context: GenerationContext, purpose: Purpose
    ) -> GenerationResult:
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": "user", "content": build_prompt(prompt, context)}],
        }
        if purpose is Purpose.RUBRIC_SCORING:
            payload["temperature"] = _RUBRIC_SCORING_TEMPERATURE
            payload["response_format"] = {"type": "json_object"}

        try:
            response = self._post_chat(payload)
            if response.status_code == httpx.codes.NOT_FOUND and self._switch_model():
                payload["model"] = self._model
                response = self._post_chat(payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return GenerationResult(text="", available=False, error=str(exc))

        data = response.json()
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return GenerationResult(text="", available=False, error="malformed response from Groq")
        return GenerationResult(text=text, available=True)

    def _post_chat(self, payload: dict[str, object]) -> httpx.Response:
        return self._client.post(
            "/chat/completions", json=payload, timeout=_DEFAULT_TIMEOUT_SECONDS
        )

    def _switch_model(self) -> bool:
        """Move to a model this key can use, after the current one was refused."""
        try:
            listing = self._client.get("/models", timeout=_PING_TIMEOUT_SECONDS).json()
            available = {m["id"] for m in listing["data"]}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return False
        for candidate in _FALLBACK_MODELS:
            if candidate in available and candidate != self._model:
                self._model = candidate
                return True
        return False

    @property
    def provider_name(self) -> str:
        return "groq"

    @property
    def model_name(self) -> str | None:
        return self._model
