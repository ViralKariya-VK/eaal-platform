"""Hand-labeled example sets for the LLM-rubric signals (S1.1, S1.2, S3.1).

These are the one artifact in this whole suite that is NOT purely
mechanical: the "expected_rank" on each example is a single researcher's
judgment (mine, in this session), not an independently collected human
rating. That's a real limitation, stated plainly in the report — a small,
single-rater face-validity spot-check is evidence of *some* alignment
between the model's judgment and a reasonable reading of the rubric, not
a validated inter-rater study. Sample sizes here (6-8) are far too small
for the correlation statistics to be more than illustrative; they're
reported as such, not as significance tests with real statistical power.

Each example carries two kinds of expected label, both assigned by reading
the rubric text the model itself is given (see
``eaal_platform.signals.compute._RUBRIC_INSTRUCTION`` /
``_CONCEPT_CHECK_RUBRIC_INSTRUCTION``) and applying it the way an
instructor grading against that rubric would:

- ``expected_*_rank``: a 1..N ordinal (1 = should score lowest, N =
  highest) — used for the Spearman rank-correlation face-validity check.
- ``expected_*_value``: a calibrated 0-1 value anchored directly to the
  rubric's own stated anchors (e.g. its explicit "0 means a complete-
  solution request with no attempt" / "1 means well-timed, specifically
  targeted") — used for the ICC agreement check, which (unlike Spearman)
  assumes the two raters are trying to hit the same absolute scale, not
  just the same ordering.
"""

from __future__ import annotations

from dataclasses import dataclass

from eaal_platform.ai.provider import GenerationContext

_LINKED_LIST_TASK = "Implement a function that reverses a singly linked list in place."


@dataclass(frozen=True)
class HelpSeekingExample:
    label: str
    message: str
    context: GenerationContext
    expected_calibration_rank: int
    expected_grounding_rank: int
    expected_calibration_value: float
    expected_grounding_value: float
    rationale: str


@dataclass(frozen=True)
class ConceptExample:
    label: str
    task_description: str
    response_text: str
    expected_rank: int
    expected_value: float
    rationale: str


HELP_SEEKING_EXAMPLES: list[HelpSeekingExample] = [
    HelpSeekingExample(
        label="explicit_solution_request_no_attempt",
        message="Please just write the whole reverseList function for me, I don't want to think about it.",
        context=GenerationContext(task_description=_LINKED_LIST_TASK),
        expected_calibration_rank=1,
        expected_grounding_rank=1,
        expected_calibration_value=0.0,
        expected_grounding_value=0.0,
        rationale="Rubric explicitly calls this out as a 0: complete-solution request, no attempt.",
    ),
    HelpSeekingExample(
        label="bare_vague_request",
        message="why is my loop wrong",
        context=GenerationContext(task_description=_LINKED_LIST_TASK),
        expected_calibration_rank=2,
        expected_grounding_rank=2,
        expected_calibration_value=0.15,
        expected_grounding_value=0.15,
        rationale="A real question, but no code, no specifics, no description of the actual behavior.",
    ),
    HelpSeekingExample(
        label="preliminary_concept_question",
        message="Before I start, what's the general idea behind reversing a linked list in place?",
        context=GenerationContext(task_description=_LINKED_LIST_TASK),
        expected_calibration_rank=3,
        expected_grounding_rank=3,
        expected_calibration_value=0.45,
        expected_grounding_value=0.2,
        rationale="Reasonable preliminary question (not a solution request) but still no own attempt to ground it in.",
    ),
    HelpSeekingExample(
        label="reasoning_check_no_code",
        message=(
            "I'm planning to use two pointers, prev and curr, to reverse the list in place. "
            "I think I might also need a third pointer to save curr.next before I overwrite "
            "it — does that reasoning sound right?"
        ),
        context=GenerationContext(task_description=_LINKED_LIST_TASK),
        expected_calibration_rank=6,
        expected_grounding_rank=6,
        expected_calibration_value=0.85,
        expected_grounding_value=0.85,
        rationale="Specific, well-timed check of the student's own reasoning before writing code.",
    ),
    HelpSeekingExample(
        label="specific_bug_with_code",
        message=(
            "My reverse() function returns None instead of the reversed list. I think I'm not "
            "returning the new head at the end. Am I right?"
        ),
        context=GenerationContext(
            task_description=_LINKED_LIST_TASK,
            current_code=(
                "def reverse(head):\n"
                "    prev = None\n"
                "    curr = head\n"
                "    while curr:\n"
                "        nxt = curr.next\n"
                "        curr.next = prev\n"
                "        prev = curr\n"
                "        curr = nxt\n"
            ),
        ),
        expected_calibration_rank=7,
        expected_grounding_rank=7,
        expected_calibration_value=0.95,
        expected_grounding_value=0.95,
        rationale="Specific, targeted, references their own real code and a concrete symptom.",
    ),
    HelpSeekingExample(
        label="error_traceback_with_stderr",
        message="I'm getting this error when I run my code, what does it mean?",
        context=GenerationContext(
            task_description=_LINKED_LIST_TASK,
            current_code="def reverse(head):\n    prev = None\n    curr = head\n    while curr:\n        curr = curr.nxt\n",
            recent_stderr="AttributeError: 'ListNode' object has no attribute 'nxt'",
        ),
        expected_calibration_rank=8,
        expected_grounding_rank=8,
        expected_calibration_value=1.0,
        expected_grounding_value=1.0,
        rationale="Well-timed (right after hitting a real error) and grounded in the actual traceback.",
    ),
    HelpSeekingExample(
        label="premature_full_solution_with_no_code_shown",
        message="Can you just give me the full working solution to this?",
        context=GenerationContext(task_description=_LINKED_LIST_TASK),
        expected_calibration_rank=1,
        expected_grounding_rank=1,
        expected_calibration_value=0.0,
        expected_grounding_value=0.0,
        rationale="Same construct as the first example, different phrasing — a second low-anchor point.",
    ),
    HelpSeekingExample(
        label="bare_greeting",
        message="hi, can you help?",
        context=GenerationContext(task_description=_LINKED_LIST_TASK),
        expected_calibration_rank=2,
        expected_grounding_rank=1,
        expected_calibration_value=0.1,
        expected_grounding_value=0.05,
        rationale="Not yet a real request at all; essentially zero grounding.",
    ),
]


CONCEPT_UNDERSTANDING_EXAMPLES: list[ConceptExample] = [
    ConceptExample(
        label="rigorous_explanation",
        task_description=_LINKED_LIST_TASK,
        response_text=(
            "The list is reversed by walking through it once while keeping three pointers: the "
            "previous node, the current node, and the next node (saved before we overwrite the "
            "link). At each step we point curr.next backward to prev, then advance prev and curr "
            "forward. Because we only rewire existing pointers rather than allocate new nodes, "
            "this runs in O(n) time and O(1) extra space."
        ),
        expected_rank=6,
        expected_value=0.95,
        rationale="Explains the mechanism, why it works, and its complexity — full understanding.",
    ),
    ConceptExample(
        label="core_idea_less_rigorous",
        task_description=_LINKED_LIST_TASK,
        response_text=(
            "We flip each node's pointer to point to the previous node instead of the next one, "
            "one at a time, until the whole list points backward."
        ),
        expected_rank=5,
        expected_value=0.75,
        rationale="Captures the real idea correctly, just less detailed than the rigorous version.",
    ),
    ConceptExample(
        label="surface_line_by_line_restatement",
        task_description=_LINKED_LIST_TASK,
        response_text=(
            "First I set prev to None. Then I loop while curr is not None. Inside the loop I save "
            "next as curr.next, then set curr.next to prev, then set prev to curr, then set curr "
            "to next. After the loop I return prev."
        ),
        expected_rank=3,
        expected_value=0.2,
        rationale="Rubric explicitly penalizes this: restates code line-by-line, never says why.",
    ),
    ConceptExample(
        label="vague_gesture_at_mechanism",
        task_description=_LINKED_LIST_TASK,
        response_text="Because we use a while loop and some pointers to go through the list.",
        expected_rank=2,
        expected_value=0.15,
        rationale="Gestures at the mechanism without ever explaining the actual reversal logic.",
    ),
    ConceptExample(
        label="no_understanding_admitted",
        task_description=_LINKED_LIST_TASK,
        response_text="It just works, honestly I don't really know why, I copied it from the AI.",
        expected_rank=1,
        expected_value=0.05,
        rationale="Explicitly no understanding — should be among the very lowest scores.",
    ),
    ConceptExample(
        label="confidently_wrong",
        task_description=_LINKED_LIST_TASK,
        response_text=(
            "It works because Python automatically sorts the list for us when we call .reverse(), "
            "so the algorithm underneath doesn't really matter."
        ),
        expected_rank=1,
        expected_value=0.0,
        rationale="Factually wrong and irrelevant to the actual algorithm — a second low anchor.",
    ),
]
