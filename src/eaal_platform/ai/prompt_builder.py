"""Turns a bare student question plus ``GenerationContext`` into the actual
text sent to a model.

Lives outside ``ollama_provider.py`` because this assembly logic isn't
Ollama-specific — a future Groq adapter needs the exact same context
included, so this is the one place that defines what "giving the AI
context" means for this app.
"""

from __future__ import annotations

from eaal_platform.ai.provider import GenerationContext

# Local models have a limited context window, and a very long file or wall
# of output pushes out the parts that actually matter (the question, the
# most recent error). Truncating from the front keeps the *end* of each
# piece of text, since that's usually where the relevant part is (the
# bottom of a file being edited, the last lines of a traceback).
_MAX_CODE_CHARS = 6000
_MAX_OUTPUT_CHARS = 2000


def _truncated(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"...[truncated]...\n{text[-limit:]}"


def build_prompt(question: str, context: GenerationContext) -> str:
    """Assemble the full prompt to send to a model.

    Returns just ``question`` unchanged when the context is entirely
    empty, so a bare "ping"-style call or a task with no code yet doesn't
    get wrapped in empty template sections.
    """
    sections: list[str] = []

    if context.task_description:
        sections.append(f"Task:\n{context.task_description}")

    if context.current_code:
        code = _truncated(context.current_code, _MAX_CODE_CHARS)
        sections.append(f"The student's current code:\n```\n{code}\n```")

    if context.recent_stderr:
        stderr = _truncated(context.recent_stderr, _MAX_OUTPUT_CHARS)
        sections.append(f"The most recent error when they ran it:\n```\n{stderr}\n```")
    elif context.recent_stdout:
        stdout = _truncated(context.recent_stdout, _MAX_OUTPUT_CHARS)
        sections.append(f"The most recent output when they ran it:\n```\n{stdout}\n```")

    if not sections:
        return question

    context_block = "\n\n".join(sections)
    return (
        "You are a coding assistant embedded in a student's code editor. "
        "Use the context below to answer their question about their own "
        "work — don't just solve the task for them if they're asking about "
        "something specific.\n\n"
        f"{context_block}\n\n"
        f"The student's question:\n{question}"
    )
