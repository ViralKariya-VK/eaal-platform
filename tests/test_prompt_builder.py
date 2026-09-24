"""Tests for assembling a student's question + on-screen context into the
text actually sent to a model."""

from __future__ import annotations

from eaal_platform.ai.prompt_builder import build_prompt
from eaal_platform.ai.provider import GenerationContext


def test_empty_context_returns_bare_question() -> None:
    assert build_prompt("what is a list?", GenerationContext()) == "what is a list?"


def test_includes_current_code() -> None:
    prompt = build_prompt("why is this broken?", GenerationContext(current_code="x = 1"))
    assert "x = 1" in prompt
    assert "why is this broken?" in prompt


def test_includes_task_description() -> None:
    prompt = build_prompt(
        "how do I start?", GenerationContext(task_description="Reverse a linked list")
    )
    assert "Reverse a linked list" in prompt


def test_prefers_stderr_over_stdout_when_both_present() -> None:
    prompt = build_prompt(
        "what's wrong?",
        GenerationContext(recent_stdout="42", recent_stderr="ValueError: boom"),
    )
    assert "ValueError: boom" in prompt
    assert "42" not in prompt


def test_uses_stdout_when_no_stderr() -> None:
    prompt = build_prompt("did it work?", GenerationContext(recent_stdout="42"))
    assert "42" in prompt


def test_long_code_is_truncated_keeping_the_end() -> None:
    code = "a" * 5000 + "\nlast_line_marker\n" + "b" * 5000
    prompt = build_prompt("help", GenerationContext(current_code=code))
    assert "last_line_marker" in prompt
    assert "truncated" in prompt
    assert len(prompt) < len(code)


def test_all_sections_present_together() -> None:
    prompt = build_prompt(
        "why does this fail?",
        GenerationContext(
            task_description="Sort a list",
            current_code="def sort(xs): return xs",
            recent_stderr="TypeError",
        ),
    )
    assert "Sort a list" in prompt
    assert "def sort(xs)" in prompt
    assert "TypeError" in prompt
    assert "why does this fail?" in prompt
