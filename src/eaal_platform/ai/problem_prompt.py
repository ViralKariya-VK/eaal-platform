"""The one prompt used every time the AI drafts or tidies a lab problem for a professor.

The answer has two parts, split on the ``PROBLEM:`` and ``INSTRUCTIONS:`` lines, and both parts are
Markdown, which the student view renders (headings, lists, code blocks, bold).
"""

from __future__ import annotations

import re

PROBLEM_AUTHORING_PROMPT = """\
You help a university professor write a programming lab problem in Python.
Never include the solution or hints that give the solution away.

Reply in exactly this shape, and nothing before or after it:

PROBLEM:
One short paragraph (at most 80 words) that says what to build. Plain sentences, **bold** only for
the key term.

INSTRUCTIONS:
### Input
What the program reads, line by line.

### Output
What the program prints.

### Example
```
Input:
3
5 1 9
Output:
9 5 1
```

### Constraints
- one constraint per bullet (value ranges, time and memory limits)

Format rules: Markdown only. Use ### headings exactly as above, "-" bullets, fenced code blocks for
every example, and `backticks` for names and values. No HTML, no tables, no other headings.
Keep every line short. If the professor gave notes or a current draft, keep their meaning and
improve the structure.
"""

_SPLIT = re.compile(r"^\s*(?:#{1,6}\s*)?\**INSTRUCTIONS:?\**\s*$", re.IGNORECASE | re.MULTILINE)
_LEAD = re.compile(r"^\s*(?:#{1,6}\s*)?\**PROBLEM:?\**\s*", re.IGNORECASE)


def split_reply(text: str) -> tuple[str, str]:
    """The question and the instructions of a reply (the whole reply is the question if unsplit)."""
    match = _SPLIT.search(text)
    question, instructions = (text[: match.start()], text[match.end() :]) if match else (text, "")
    return _LEAD.sub("", question).strip(), instructions.strip()
