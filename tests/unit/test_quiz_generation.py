# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Unit coverage for internal answer normalization and public conversion."""

import random

import pytest

from app.domain.quiz_generation import (
    GeneratedQuizQuestion,
    QuizNormalizationCode,
    QuizNormalizationError,
    normalize_generated_question,
)


def _question(
    *,
    options: list[str] | None = None,
    correct_answer: str = "Correct",
) -> GeneratedQuizQuestion:
    return GeneratedQuizQuestion(
        question="Which answer is correct?",
        options=options or ["Correct", "Wrong A", "Wrong B"],
        correct_answer=correct_answer,
        explanation="The selected answer is correct.",
    )


def test_unique_correct_answer_derives_index_after_shuffling() -> None:
    public = normalize_generated_question(_question(), rng=random.Random(7))

    assert set(public) == {
        "question",
        "options",
        "correct_option_index",
        "explanation",
    }
    assert public["options"][public["correct_option_index"]] == "Correct"
    assert "correct_answer" not in public


def test_unicode_and_whitespace_normalization_find_the_selected_answer() -> None:
    public = normalize_generated_question(
        _question(
            options=["16", "14", "15"],
            correct_answer=" \uff11\uff14 ",
        ),
        rng=random.Random(3),
    )

    assert public["options"][public["correct_option_index"]] == "14"


def test_meaningful_capitalization_remains_distinct() -> None:
    public = normalize_generated_question(
        _question(options=["PP", "Pp", "pp"], correct_answer="Pp"),
        rng=random.Random(3),
    )

    assert public["options"][public["correct_option_index"]] == "Pp"


@pytest.mark.parametrize(
    ("correct_answer", "options", "expected"),
    [
        (
            "Missing",
            ["A", "B", "C"],
            QuizNormalizationCode.CORRECT_ANSWER_NOT_IN_OPTIONS,
        ),
        ("A", ["A", " A ", "B"], QuizNormalizationCode.AMBIGUOUS_CORRECT_ANSWER),
    ],
)
def test_missing_and_ambiguous_answers_fail_before_public_conversion(
    correct_answer: str,
    options: list[str],
    expected: QuizNormalizationCode,
) -> None:
    with pytest.raises(QuizNormalizationError) as exc_info:
        normalize_generated_question(
            _question(options=options, correct_answer=correct_answer)
        )

    assert exc_info.value.code is expected


def test_grade_specific_option_count_is_checked_at_normalization_boundary() -> None:
    with pytest.raises(QuizNormalizationError) as exc_info:
        normalize_generated_question(
            _question(options=["A", "B", "C", "D"]),
            grade="Klasse 1",
        )

    assert exc_info.value.code is QuizNormalizationCode.INVALID_OPTION_COUNT
