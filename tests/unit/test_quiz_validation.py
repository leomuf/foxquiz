# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for objective quiz-candidate invariants.

Purpose:
    Ensure malformed quizzes and answer-revealing option decorations are
    rejected before an LLM judge call or browser output.

Boundary:
    These tests deliberately do not decide whether a question emoji
    semantically reveals an answer. That judgment remains with the LLM judge
    and behavioral evaluations.
"""

import pytest

from app.domain.quiz_validation import (
    QuizValidationCode,
    build_retry_guidance,
    validate_quiz_candidate,
)


def _valid_quiz() -> dict:
    return {
        "title": "Arithmetic practice",
        "difficulty": "Medium",
        "questions": [
            {
                "question": f"Think carefully \U0001f4a1: What is {number} + 1?",
                "options": [str(number + 1), str(number + 2), str(number + 3)],
                "correct_option_index": 0,
                "explanation": f"{number} plus one is {number + 1}.",
            }
            for number in range(1, 11)
        ],
    }


def _codes(candidate: dict) -> set[QuizValidationCode]:
    return {issue.code for issue in validate_quiz_candidate(candidate).issues}


def test_allows_non_answer_revealing_emoji_in_question() -> None:
    """A decorative thinking emoji in a question must remain supported."""
    assert validate_quiz_candidate(_valid_quiz()).is_valid


@pytest.mark.parametrize(
    ("option", "expected_code"),
    [
        ("Four \u2705", QuizValidationCode.ANSWER_CUE_IN_OPTION),
        ("The Sun \U0001f31e", QuizValidationCode.EMOJI_IN_OPTION),
        (
            "A family \U0001f468\u200d\U0001f469\u200d\U0001f467",
            QuizValidationCode.EMOJI_IN_OPTION,
        ),
    ],
)
def test_rejects_unicode_emoji_sequences_in_options(
    option: str, expected_code: QuizValidationCode
) -> None:
    """Single-codepoint and joined emojis must never decorate answer options."""
    quiz = _valid_quiz()
    quiz["questions"][0]["options"][0] = option

    assert expected_code in _codes(quiz)


def test_reports_structure_duplicates_and_invalid_correct_index() -> None:
    """Fast validation covers the objective schema invariants used for routing."""
    quiz = _valid_quiz()
    quiz["questions"][0]["options"] = [" Same ", "same", "Other"]
    quiz["questions"][0]["correct_option_index"] = 9

    codes = _codes(quiz)

    assert QuizValidationCode.DUPLICATE_OPTION in codes
    assert QuizValidationCode.INVALID_CORRECT_INDEX in codes


def test_retry_guidance_contains_locations_but_not_candidate_text() -> None:
    """Retry diagnostics guide regeneration without retaining generated content."""
    quiz = _valid_quiz()
    secret_option = "Sensitive candidate text \u2705"
    quiz["questions"][2]["options"][1] = secret_option

    guidance = build_retry_guidance(validate_quiz_candidate(quiz))

    assert "Question 3, option 2" in guidance
    assert "answer cue in option" in guidance
    assert secret_option not in guidance


def test_retry_guidance_explains_normalized_duplicate_correction() -> None:
    """Duplicate retries must explain how equivalent option text is detected."""
    quiz = _valid_quiz()
    quiz["questions"][4]["options"] = [" Recessive ", "recessive", "Dominant"]

    guidance = build_retry_guidance(validate_quiz_candidate(quiz))

    assert "Question 5, option 2: duplicate option" in guidance
    assert "compare every pair of options after Unicode normalization" in guidance
    assert "ignoring capitalization" in guidance
    assert "meaningfully distinct distractor" in guidance
