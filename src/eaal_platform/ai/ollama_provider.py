"""Ollama-backed ``AIProvider``: a fully local, offline model backend.

Talks to Ollama's local REST API (default ``http://localhost:11434``) via
``httpx``, never via the CLI — that's the documented, stable interface, and
it lets us set per-request options (temperature, timeout) and get back
structured JSON instead of parsing terminal output.

This is a synchronous, blocking client on purpose: inference genuinely
takes real wall-clock time (seconds, not milliseconds), and making this
module itself async would just push the "don't block the UI thread"
problem onto every caller instead of solving it once. The one caller that
matters — the chat panel — solves it the same way the sandbox executor
does: by running the call on a background ``QThread``
(see ``ai/qt_runner.py``).

The ``httpx.Client`` is injectable (rather than using module-level
``httpx.get``/``httpx.post``) purely for testability: tests pass a client
built with ``httpx.MockTransport`` so they exercise real request/response
handling without a live Ollama server.
"""

from __future__ import annotations

import httpx

from eaal_platform.ai.prompt_builder import build_prompt
from eaal_platform.ai.provider import AIProvider, GenerationContext, GenerationResult, Purpose

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL = "qwen3:8b"
_DEFAULT_GENERATE_TIMEOUT_SECONDS = 60.0
_PING_TIMEOUT_SECONDS = 2.0

# Rubric scoring (a later phase) needs reproducible, low-variance output —
# this is the same 0.1 the EAAL framework's own proof-of-concept uses (see
# docs/The EAAL Framework.pdf §6.4). Chat uses Ollama's own default
# sampling instead of pinning a value here, since a natural-feeling reply
# is the goal there, not reproducibility.
_RUBRIC_SCORING_TEMPERATURE = 0.1


class OllamaProvider(AIProvider):
    """Generates responses using a locally running Ollama server."""

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        self._model = model
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"))

    def ping(self) -> bool:
        try:
            response = self._client.get("/api/tags", timeout=_PING_TIMEOUT_SECONDS)
            return response.status_code == httpx.codes.OK
        except httpx.HTTPError:
            return False

    def generate(
        self, prompt: str, context: GenerationContext, purpose: Purpose
    ) -> GenerationResult:
        payload: dict[str, object] = {
            "model": self._model,
            "prompt": build_prompt(prompt, context),
            "stream": False,
        }
        if purpose is Purpose.RUBRIC_SCORING:
            payload["format"] = "json"
            payload["options"] = {"temperature": _RUBRIC_SCORING_TEMPERATURE}

        try:
            response = self._client.post(
                "/api/generate",
                json=payload,
                timeout=_DEFAULT_GENERATE_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return GenerationResult(text="", available=False, error=str(exc))

        data = response.json()
        text = data.get("response", "")
        return GenerationResult(text=text, available=True)

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str | None:
        return self._model
