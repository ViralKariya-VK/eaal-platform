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


# Written BEFORE the S3.1 rubric was revised, on tasks the rubric text was never
# tuned against, so they can show whether a prompt change generalises rather
# than just fixing the six examples above. Same single-rater caveat applies:
# these labels are one researcher's judgement, not an independent gold standard.
_PALINDROME_TASK = (
    "Write a function that returns True if a string reads the same forwards and backwards."
)
_BINARY_SEARCH_TASK = (
    "Implement binary search: return the index of a target in a sorted list, or -1."
)

CONCEPT_UNDERSTANDING_HELDOUT_EXAMPLES: list[ConceptExample] = [
    ConceptExample(
        label="palindrome_rigorous",
        task_description=_PALINDROME_TASK,
        response_text=(
            "A string is a palindrome exactly when its first half mirrors its second half, so I "
            "only need to compare the character at position i with the one at position n-1-i. If "
            "any pair differs it can't be a palindrome, and if I get to the middle with no "
            "mismatch every pair matched. That needs just one pass, O(n), with two indices."
        ),
        expected_rank=7,
        expected_value=0.95,
        rationale="States the mirror property, why a mismatch decides it, and the cost.",
    ),
    ConceptExample(
        label="binary_search_rigorous",
        task_description=_BINARY_SEARCH_TASK,
        response_text=(
            "Because the list is sorted, comparing the target with the middle element tells me "
            "which half it must be in, so I can throw the other half away. Each step halves the "
            "search range, so it takes about log2(n) steps instead of n. The loop keeps the "
            "invariant that if the target exists it lies between low and high."
        ),
        expected_rank=7,
        expected_value=0.95,
        rationale="Explains why halving is valid (sortedness), the invariant, and the cost.",
    ),
    ConceptExample(
        label="binary_search_terse_but_correct",
        task_description=_BINARY_SEARCH_TASK,
        response_text="The list is sorted, so I check the middle and discard the half that can't contain the target.",
        expected_rank=5,
        expected_value=0.7,
        rationale="Short, but it gives the actual reason the approach works. Must not be over-penalised.",
    ),
    ConceptExample(
        label="palindrome_core_idea",
        task_description=_PALINDROME_TASK,
        response_text="You compare the string with itself reversed, and if they're the same it's a palindrome.",
        expected_rank=5,
        expected_value=0.7,
        rationale="Correct core idea in one sentence, no depth. Mid-high.",
    ),
    ConceptExample(
        label="palindrome_surface_restatement",
        task_description=_PALINDROME_TASK,
        response_text=(
            "I define a function that takes s. Then I make a variable left equal to 0 and right "
            "equal to len(s) minus 1. Then I start a while loop that runs while left is less than "
            "right. Inside it I check if s[left] is not equal to s[right] and if so I return "
            "False. Otherwise I add 1 to left and subtract 1 from right. At the end I return True."
        ),
        expected_rank=3,
        expected_value=0.2,
        rationale="Pure line-by-line narration of the code; never says why checking pairs works.",
    ),
    ConceptExample(
        label="binary_search_surface_long",
        task_description=_BINARY_SEARCH_TASK,
        response_text=(
            "First I set low to 0 and high to the last index. While low is at most high I compute "
            "mid as the average of low and high using integer division. If arr[mid] equals the "
            "target I return mid. If arr[mid] is less than the target I set low to mid plus 1, "
            "and otherwise I set high to mid minus 1. When the loop ends without finding it I "
            "return -1."
        ),
        expected_rank=3,
        expected_value=0.25,
        rationale="Long and precise-sounding but purely procedural: describes WHAT, never WHY.",
    ),
    ConceptExample(
        label="palindrome_vague",
        task_description=_PALINDROME_TASK,
        response_text="It looks through the string and checks it with some conditions to see if it's the same.",
        expected_rank=2,
        expected_value=0.1,
        rationale="Gestures at checking without saying what is compared or why.",
    ),
    ConceptExample(
        label="binary_search_no_understanding",
        task_description=_BINARY_SEARCH_TASK,
        response_text="I got the code from the AI and it passes the tests. I'm not sure how it finds the number.",
        expected_rank=1,
        expected_value=0.05,
        rationale="Admits no understanding.",
    ),
    ConceptExample(
        label="palindrome_confidently_wrong",
        task_description=_PALINDROME_TASK,
        response_text=(
            "It works because a palindrome is a string where all the characters are the same, so "
            "I just check that every letter equals the first letter."
        ),
        expected_rank=1,
        expected_value=0.0,
        rationale="States a wrong definition of palindrome (AAAA only) with confidence.",
    ),
]


# Third set, written after the held-out set above had already been looked at
# while revising the S3.1 rubric (so the held-out set is no longer a clean
# test). These were written before the final prompt was run on them and are
# scored exactly once, at the end.
_FACTORIAL_TASK = "Write a recursive function that returns n factorial."
_PRIME_TASK = "Write a function that returns True if a number is prime."
_MERGE_TASK = "Merge two sorted lists into one sorted list."

CONCEPT_UNDERSTANDING_FRESH_EXAMPLES: list[ConceptExample] = [
    ConceptExample(
        label="factorial_rigorous",
        task_description=_FACTORIAL_TASK,
        response_text=(
            "n factorial is n times the factorial of n-1, so each call hands a slightly smaller "
            "version of the same problem to the next call. The base case, n equal to 0 returning "
            "1, is what stops the chain, and as the calls return each one multiplies the result "
            "it was waiting for by its own n, which builds up the product."
        ),
        expected_rank=7,
        expected_value=0.95,
        rationale="Self-similarity, the role of the base case, and how the result is assembled.",
    ),
    ConceptExample(
        label="prime_rigorous",
        task_description=_PRIME_TASK,
        response_text=(
            "If n has a divisor then it has one that is at most its square root, because divisors "
            "come in pairs that multiply to n and one of each pair can't exceed the square root. "
            "So I only need to test up to the square root, which is O(sqrt n) instead of O(n)."
        ),
        expected_rank=7,
        expected_value=0.95,
        rationale="Gives the pairing argument that justifies stopping at sqrt(n), and the cost.",
    ),
    ConceptExample(
        label="merge_terse_correct",
        task_description=_MERGE_TASK,
        response_text="Both lists are sorted, so the smallest remaining item is always at the front of one of them; I keep taking the smaller front item.",
        expected_rank=5,
        expected_value=0.75,
        rationale="Short but states exactly the property that makes the merge correct.",
    ),
    ConceptExample(
        label="prime_core_idea",
        task_description=_PRIME_TASK,
        response_text="A prime has no divisors besides 1 and itself, so I try dividing by smaller numbers and see if any of them divide evenly.",
        expected_rank=5,
        expected_value=0.65,
        rationale="Correct definition-level idea; no insight into why the search can stop early.",
    ),
    ConceptExample(
        label="factorial_surface",
        task_description=_FACTORIAL_TASK,
        response_text=(
            "I define factorial with a parameter n. If n equals 0 I return 1. Otherwise I return "
            "n multiplied by factorial of n minus 1. Then I call factorial on 5 and print the result."
        ),
        expected_rank=3,
        expected_value=0.25,
        rationale="Narrates the code; never says why recursion produces the factorial.",
    ),
    ConceptExample(
        label="prime_surface_with_sqrt",
        task_description=_PRIME_TASK,
        response_text=(
            "I check if n is less than 2 and return False. Then I loop i from 2 up to the integer "
            "square root of n plus 1. For each i, if n modulo i equals 0 I return False. If the "
            "loop finishes I return True."
        ),
        expected_rank=3,
        expected_value=0.25,
        rationale="Mentions the square root but only as a step; the reason it's enough is never given.",
    ),
    ConceptExample(
        label="merge_vague",
        task_description=_MERGE_TASK,
        response_text="It goes through both lists and puts things in the right order somehow.",
        expected_rank=2,
        expected_value=0.1,
        rationale="No mechanism at all.",
    ),
    ConceptExample(
        label="factorial_no_understanding",
        task_description=_FACTORIAL_TASK,
        response_text="Honestly I just asked the AI and pasted it. Recursion confuses me.",
        expected_rank=1,
        expected_value=0.05,
        rationale="Admits no understanding.",
    ),
    ConceptExample(
        label="merge_confidently_wrong",
        task_description=_MERGE_TASK,
        response_text=(
            "It works because I concatenate the two lists, and Python lists are always kept "
            "sorted automatically, so no comparing is needed."
        ),
        expected_rank=1,
        expected_value=0.0,
        rationale="Confident and factually wrong.",
    ),
]
