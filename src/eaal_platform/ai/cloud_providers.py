"""Hosted AI providers a person can connect with their own API key.

OpenAI (ChatGPT), Anthropic (Claude), Google (Gemini), xAI (Grok) and Groq. Each
class knows how to list the models a key may use (which doubles as the key
check), and how to answer one prompt. They follow the same rules as the
other providers: same ``build_prompt`` context, low temperature for rubric
scoring, and an ordinary failure (bad key, no network, no quota) comes back
as ``GenerationResult(available=False, error=...)`` in plain words, never
as an exception.

Keys are handed in by the caller and kept only in memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from eaal_platform.ai.prompt_builder import build_prompt
from eaal_platform.ai.provider import AIProvider, GenerationContext, GenerationResult, Purpose

_LIST_TIMEOUT = 15.0
_GENERATE_TIMEOUT = 90.0
_RUBRIC_TEMPERATURE = 0.1  # reproducible scoring, same rationale as the other providers
_CHAT_MAX_TOKENS = 2048
_RUBRIC_MAX_TOKENS = 1024


@dataclass(frozen=True)
class ProviderInfo:
    """What the setup screen needs to know about a provider."""

    key: str
    label: str
    help_url: str  # where a person gets an API key
    key_hint: str  # what a key looks like (shown as a placeholder)
    needs_key: bool = True


PROVIDERS: dict[str, ProviderInfo] = {
    "openai": ProviderInfo(
        "openai", "OpenAI (ChatGPT)", "https://platform.openai.com/api-keys", "sk-…"
    ),
    "anthropic": ProviderInfo(
        "anthropic", "Anthropic (Claude)", "https://console.anthropic.com/settings/keys", "sk-ant-…"
    ),
    "gemini": ProviderInfo(
        "gemini", "Google (Gemini)", "https://aistudio.google.com/app/apikey", "AIza…"
    ),
    "xai": ProviderInfo("xai", "xAI (Grok)", "https://console.x.ai", "xai-…"),
    "groq": ProviderInfo("groq", "Groq", "https://console.groq.com/keys", "gsk_…"),
    "ollama": ProviderInfo(
        "ollama", "Ollama (this computer)", "https://ollama.com", "", needs_key=False
    ),
}
# What a student chooses between for their own use.
STUDENT_PROVIDER_KEYS = ("gemini", "anthropic", "openai", "xai", "groq")


class ProviderError(Exception):
    """A failure worded for the person using the app."""


def _error_detail(response: httpx.Response) -> str:
    """The one-line reason a provider gave for an error reply, if it gave one."""
    try:
        body = response.json()
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        detail = str(error.get("message") or "")
    elif isinstance(error, str):
        detail = error
    else:
        return ""
    return detail.strip().splitlines()[0][:200] if detail.strip() else ""


def explain_http_error(label: str, response: httpx.Response) -> str:
    """Turn an error reply into one sentence saying what to do about it."""
    status = response.status_code
    detail = _error_detail(response)
    key_problem = any(
        w in detail.lower() for w in ("api key", "api_key", "apikey", "authentication")
    )
    if status in (401, 403) or (status == 400 and key_problem):
        # Gemini and Grok report a bad key as a 400 with the reason in the text.
        return f"{label} rejected the API key. Check that you copied it completely."
    fixed = {
        404: f"{label} doesn't recognise that model. Choose another one.",
        429: (
            f"{label} says this key is rate-limited or out of credit. "
            "Check your plan, or wait a moment."
        ),
    }
    if status in fixed:
        return fixed[status]
    if status == 400 and detail:
        return f"{label} refused the request: {detail}"
    if status >= 500:
        return f"{label} is having problems ({status}). Try again shortly."
    return f"{label} returned an error ({status}). {detail}".strip()


def _network_error(label: str) -> str:
    return f"Can't reach {label}. Check your internet connection."


def _pick(models: list[str], preferences: list[str]) -> str | None:
    """The best model: an exact preferred name, else the newest one containing a preferred word."""
    for wanted in preferences:
        if wanted in models:
            return wanted
    for wanted in preferences:
        matches = sorted((m for m in models if wanted in m), reverse=True)
        if matches:
            return matches[0]
    return models[0] if models else None


class CloudProvider(AIProvider):
    """Shared behaviour of the hosted providers."""

    info: ProviderInfo
    default_preferences: list[str] = []  # noqa: RUF012 - read-only class data

    def __init__(
        self, api_key: str, model: str | None = None, client: httpx.Client | None = None
    ) -> None:
        self._api_key = api_key.strip()
        self._model = model or None
        self._client = client or httpx.Client()

    # -- to implement ---------------------------------------------------------------

    def _list_url(self) -> str:
        raise NotImplementedError

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _parse_models(self, data: Any) -> list[str]:
        raise NotImplementedError

    def _generate(self, prompt: str, purpose: Purpose) -> GenerationResult:
        raise NotImplementedError

    # -- shared ---------------------------------------------------------------------------

    @property
    def label(self) -> str:
        return self.info.label

    def list_models(self) -> list[str]:
        """The chat models this key can use. Raises ``ProviderError`` if the key doesn't work."""
        try:
            response = self._client.get(
                self._list_url(), headers=self._headers(), timeout=_LIST_TIMEOUT
            )
        except httpx.HTTPError:
            raise ProviderError(_network_error(self.label)) from None
        if response.status_code != httpx.codes.OK:
            raise ProviderError(explain_http_error(self.label, response))
        try:
            models = self._parse_models(response.json())
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ProviderError(f"{self.label} sent a reply this app doesn't understand.") from None
        if not models:
            raise ProviderError(f"{self.label} accepted the key but offers no chat models to it.")
        return models

    def default_model(self, models: list[str]) -> str | None:
        return _pick(models, self.default_preferences)

    def diagnose(self) -> str | None:
        try:
            models = self.list_models()
        except ProviderError as exc:
            return str(exc)
        if self._model and self._model not in models:
            return f"{self.label} doesn't offer the model “{self._model}” to this key."
        return None

    def ping(self) -> bool:
        return self.diagnose() is None

    def generate(
        self, prompt: str, context: GenerationContext, purpose: Purpose
    ) -> GenerationResult:
        if not self._model:
            return GenerationResult(text="", available=False, error="No model has been chosen.")
        return self._generate(build_prompt(prompt, context), purpose)

    @property
    def provider_name(self) -> str:
        return self.info.key

    @property
    def model_name(self) -> str | None:
        return self._model


class _OpenAICompatible(CloudProvider):
    """OpenAI and xAI speak the same chat-completions protocol."""

    base_url: str

    def _list_url(self) -> str:
        return f"{self.base_url}/models"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _generate(self, prompt: str, purpose: Purpose) -> GenerationResult:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
        }
        optional: dict[str, Any] = {}
        if purpose is Purpose.RUBRIC_SCORING:
            optional = {
                "temperature": _RUBRIC_TEMPERATURE,
                "response_format": {"type": "json_object"},
            }
        try:
            response = self._post(payload | optional)
            if response.status_code == httpx.codes.BAD_REQUEST and optional:
                # Some newer models refuse tuning options: ask again with the plain request.
                response = self._post(payload)
        except httpx.HTTPError:
            return GenerationResult("", False, _network_error(self.label))
        if response.status_code != httpx.codes.OK:
            return GenerationResult("", False, explain_http_error(self.label, response))
        try:
            text = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            return GenerationResult(
                "", False, f"{self.label} sent a reply this app doesn't understand."
            )
        return GenerationResult(text or "", True)

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        return self._client.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
            timeout=_GENERATE_TIMEOUT,
        )


_OPENAI_EXCLUDE = (
    "instruct", "audio", "realtime", "transcribe", "tts", "image", "embedding", "moderation",
    "search", "whisper", "dall-e", "davinci", "babbage", "computer-use", "codex", "diarize",
)  # fmt: skip


class OpenAIProvider(_OpenAICompatible):
    info = PROVIDERS["openai"]
    base_url = "https://api.openai.com/v1"
    default_preferences = ["gpt-4o-mini", "gpt-4.1-mini", "gpt-5-mini", "gpt-4o", "gpt-4.1"]  # noqa: RUF012

    def _parse_models(self, data: Any) -> list[str]:
        ids = [str(item["id"]) for item in data["data"]]
        chat = [
            i
            for i in ids
            if (i.startswith(("gpt-", "chatgpt-")) or i[:2] in ("o1", "o3", "o4"))
            and not any(word in i for word in _OPENAI_EXCLUDE)
        ]
        return sorted(chat)


class XAIProvider(_OpenAICompatible):
    info = PROVIDERS["xai"]
    base_url = "https://api.x.ai/v1"
    default_preferences = ["grok-3-mini", "grok-4-fast", "mini", "grok"]  # noqa: RUF012

    def _parse_models(self, data: Any) -> list[str]:
        ids = [str(item["id"]) for item in data["data"]]
        return sorted(i for i in ids if i.startswith("grok") and "image" not in i)


_GROQ_EXCLUDE = ("whisper", "orpheus", "guard", "safeguard", "tts", "embed", "allam")  # fmt: skip


class GroqCloudProvider(_OpenAICompatible):
    """Groq (console.groq.com, keys start ``gsk_``): not the same company as xAI's Grok."""

    info = PROVIDERS["groq"]
    base_url = "https://api.groq.com/openai/v1"
    default_preferences = [  # noqa: RUF012
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "llama-3.3-70b-versatile",
        "llama",
    ]

    def _parse_models(self, data: Any) -> list[str]:
        ids = [str(item["id"]) for item in data["data"]]
        return sorted(i for i in ids if not any(word in i for word in _GROQ_EXCLUDE))


class AnthropicProvider(CloudProvider):
    info = PROVIDERS["anthropic"]
    base_url = "https://api.anthropic.com/v1"
    default_preferences = ["claude-haiku-4-5-20251001", "haiku", "sonnet"]  # noqa: RUF012

    def _list_url(self) -> str:
        return f"{self.base_url}/models?limit=1000"

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._api_key, "anthropic-version": "2023-06-01"}

    def _parse_models(self, data: Any) -> list[str]:
        return sorted(
            str(item["id"]) for item in data["data"] if str(item["id"]).startswith("claude")
        )

    def _generate(self, prompt: str, purpose: Purpose) -> GenerationResult:
        rubric = purpose is Purpose.RUBRIC_SCORING
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": _RUBRIC_MAX_TOKENS if rubric else _CHAT_MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }
        if rubric:
            payload["temperature"] = _RUBRIC_TEMPERATURE
        try:
            response = self._client.post(
                f"{self.base_url}/messages",
                headers=self._headers(),
                json=payload,
                timeout=_GENERATE_TIMEOUT,
            )
        except httpx.HTTPError:
            return GenerationResult("", False, _network_error(self.label))
        if response.status_code != httpx.codes.OK:
            return GenerationResult("", False, explain_http_error(self.label, response))
        try:
            blocks = response.json()["content"]
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        except (KeyError, TypeError, ValueError, AttributeError):
            return GenerationResult(
                "", False, f"{self.label} sent a reply this app doesn't understand."
            )
        return GenerationResult(text, True)


class GeminiProvider(CloudProvider):
    info = PROVIDERS["gemini"]
    base_url = "https://generativelanguage.googleapis.com/v1beta"
    default_preferences = ["2.5-flash", "2.0-flash", "1.5-flash", "flash"]  # noqa: RUF012

    def _list_url(self) -> str:
        return f"{self.base_url}/models?pageSize=1000"

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key}

    def _parse_models(self, data: Any) -> list[str]:
        found = []
        for item in data["models"]:
            name = str(item["name"]).removeprefix("models/")
            if (
                "generateContent" in item.get("supportedGenerationMethods", [])
                and name.startswith("gemini")
                and not any(w in name for w in ("embedding", "tts", "image", "live", "aqa"))
            ):
                found.append(name)
        return sorted(found)

    def default_model(self, models: list[str]) -> str | None:
        # Prefer a stable "flash" over experimental/preview/lite variants.
        stable = [
            m for m in models if not any(w in m for w in ("preview", "exp", "lite", "thinking"))
        ]
        return _pick(stable or models, self.default_preferences)

    def _generate(self, prompt: str, purpose: Purpose) -> GenerationResult:
        rubric = purpose is Purpose.RUBRIC_SCORING
        config: dict[str, Any] = {
            "maxOutputTokens": _RUBRIC_MAX_TOKENS if rubric else _CHAT_MAX_TOKENS
        }
        if rubric:
            config["temperature"] = _RUBRIC_TEMPERATURE
            config["responseMimeType"] = "application/json"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": config,
        }
        try:
            response = self._client.post(
                f"{self.base_url}/models/{self._model}:generateContent",
                headers=self._headers(),
                json=payload,
                timeout=_GENERATE_TIMEOUT,
            )
        except httpx.HTTPError:
            return GenerationResult("", False, _network_error(self.label))
        if response.status_code != httpx.codes.OK:
            return GenerationResult("", False, explain_http_error(self.label, response))
        try:
            data = response.json()
            candidates = data.get("candidates") or []
            if not candidates:
                reason = (data.get("promptFeedback") or {}).get("blockReason")
                why = f" ({reason})" if reason else ""
                return GenerationResult("", False, f"{self.label} didn't answer that request{why}.")
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)
        except (TypeError, ValueError, AttributeError):
            return GenerationResult(
                "", False, f"{self.label} sent a reply this app doesn't understand."
            )
        return GenerationResult(text, True)


_CLASSES: dict[str, type[CloudProvider]] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "xai": XAIProvider,
    "groq": GroqCloudProvider,
}

# How each company's keys start, to say "that's a Groq key" when one is pasted in the wrong place.
_KEY_PREFIXES = (
    ("sk-ant-", "anthropic"),
    ("gsk_", "groq"),
    ("xai-", "xai"),
    ("AIza", "gemini"),
    ("sk-", "openai"),
)


def guess_provider(api_key: str) -> str | None:
    """Which company a key looks like it belongs to, going by how it starts."""
    text = api_key.strip()
    for prefix, key in _KEY_PREFIXES:
        if text.startswith(prefix):
            return key
    return None


def make_cloud_provider(
    key: str, api_key: str, model: str | None = None, client: httpx.Client | None = None
) -> CloudProvider:
    try:
        return _CLASSES[key](api_key, model, client)
    except KeyError:
        raise ProviderError(f"Unknown AI provider “{key}”.") from None


@dataclass
class KeyCheck:
    """Result of checking an API key: either the models it can use, or what's wrong."""

    ok: bool
    models: list[str] = field(default_factory=list)
    default: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "models": self.models, "default": self.default, "error": self.error}


def check_key(key: str, api_key: str, client: httpx.Client | None = None) -> KeyCheck:
    """Ask the provider which models this key can use; that also proves the key works."""
    if not api_key.strip():
        return KeyCheck(False, error="Paste your API key first.")
    try:
        provider = make_cloud_provider(key, api_key, None, client)
        models = provider.list_models()
    except ProviderError as exc:
        guessed = guess_provider(api_key)
        if guessed and guessed != key and "rejected the API key" in str(exc):
            return KeyCheck(
                False,
                error=(
                    f"This looks like a {PROVIDERS[guessed].label} key, not a "
                    f"{PROVIDERS[key].label} one. Choose {PROVIDERS[guessed].label} in the list."
                ),
            )
        return KeyCheck(False, error=str(exc))
    default = provider.default_model(models)
    ordered = ([default] if default else []) + [m for m in models if m != default]
    return KeyCheck(True, ordered, default)


__all__ = [
    "PROVIDERS",
    "STUDENT_PROVIDER_KEYS",
    "CloudProvider",
    "KeyCheck",
    "ProviderError",
    "check_key",
    "guess_provider",
    "make_cloud_provider",
]
