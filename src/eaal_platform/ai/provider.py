"""AI provider abstraction.

One interface, multiple backends (Ollama now; Groq later). The ``purpose``
argument exists from day one because the two call sites have fundamentally
different requirements: a student-facing chat reply wants normal sampling,
while rubric-based signal scoring (a later phase) needs a low temperature
and structured/JSON output for reproducibility. Baking ``purpose`` into the
interface now means real adapters can honor it later without changing every
call site.

``ping()`` is separate from ``generate()`` because the two have very
different costs: checking whether a backend is reachable should be near-
instant (e.g. one small HTTP call), while ``generate()`` can legitimately
take several seconds (real model inference). The chat panel uses ``ping()``
to show a status line without paying generation cost just to find out if
the assistant is there.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass


class Purpose(enum.StrEnum):
    """Why a generation call is being made.

    Distinguishing "chat" from "rubric_scoring" lets a real provider
    implementation apply different sampling settings per call without the
    caller needing to know or specify temperature/format directly.
    """

    CHAT = "chat"
    RUBRIC_SCORING = "rubric_scoring"


@dataclass(frozen=True, slots=True)
class GenerationContext:
    """What the student was looking at when they asked their question.

    This is what turns the assistant from a generic chatbot into one that
    actually knows what's on screen — the same "grounding" idea the EAAL
    framework docs describe (S1.2 Student Grounding): a question means
    something different depending on whether the student has working code,
    a fresh error, or a blank editor. All fields are optional because none
    of this is guaranteed to exist (a brand new task has no code yet, a
    clean run has no error).
    """

    task_description: str | None = None
    current_code: str | None = None
    recent_stdout: str | None = None
    recent_stderr: str | None = None


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """Outcome of a ``generate`` call."""

    text: str
    available: bool
    error: str | None = None


class AIProvider(ABC):
    """Abstract interface every AI backend (Ollama, Groq, ...) implements."""

    @abstractmethod
    def generate(
        self, prompt: str, context: GenerationContext, purpose: Purpose
    ) -> GenerationResult:
        """Produce a response to ``prompt``, given ``context`` and ``purpose``.

        Must never raise for an ordinary failure (backend down, timeout,
        bad response) — callers rely on ``GenerationResult.available`` to
        decide what to show, matching the requirement that the editor/
        sandbox keep working even when AI is unreachable.
        """
        raise NotImplementedError

    @abstractmethod
    def ping(self) -> bool:
        """Cheaply check whether this backend is currently reachable."""
        raise NotImplementedError

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Short identifier stored in ``AIInteraction.provider`` (e.g. "ollama")."""
        raise NotImplementedError

    @property
    @abstractmethod
    def model_name(self) -> str | None:
        """Model identifier stored in ``AIInteraction.model``, if applicable."""
        raise NotImplementedError


class UnavailableProvider(AIProvider):
    """Placeholder backend used when no real adapter is configured.

    Always returns ``available=False`` / ``ping() -> False`` rather than
    raising, so the chat panel can display a clear "AI unavailable" state
    instead of crashing — matching the non-functional requirement that the
    editor/sandbox must keep working even when AI is down (see docs/EAAL
    Platform — Implementation Flow, §4).
    """

    def generate(
        self, prompt: str, context: GenerationContext, purpose: Purpose
    ) -> GenerationResult:
        return GenerationResult(
            text="",
            available=False,
            error="No AI provider is configured yet.",
        )

    def ping(self) -> bool:
        return False

    @property
    def provider_name(self) -> str:
        return "unavailable"

    @property
    def model_name(self) -> str | None:
        return None
