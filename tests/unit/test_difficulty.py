# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for semantic DifficultyLevel enum, legacy parsing, and schema validation."""

import pytest

from app.agent import Quiz, QuizQuestion
from app.domain.difficulty import DifficultyLevel
from app.domain.quiz_generation import (
    GeneratedQuiz,
    GeneratedQuizQuestion,
    normalize_generated_quiz,
)


def test_difficulty_level_values() -> None:
    assert DifficultyLevel.EASY.value == "easy"
    assert DifficultyLevel.MEDIUM.value == "medium"
    assert DifficultyLevel.HARD.value == "hard"
    assert DifficultyLevel.EASY == "easy"
    assert DifficultyLevel.MEDIUM == "medium"
    assert DifficultyLevel.HARD == "hard"


@pytest.mark.parametrize(
    ("raw_input", "expected"),
    [
        ("easy", DifficultyLevel.EASY),
        ("medium", DifficultyLevel.MEDIUM),
        ("hard", DifficultyLevel.HARD),
        ("EASY", DifficultyLevel.EASY),
        ("  Hard  ", DifficultyLevel.HARD),
        ("🌱 Easy", DifficultyLevel.EASY),
        ("⭐ Medium", DifficultyLevel.MEDIUM),
        ("🚀 Hard", DifficultyLevel.HARD),
        ("🌱 Einfach", DifficultyLevel.EASY),
        ("🚀 Schwer", DifficultyLevel.HARD),
        ("🌱 Fácil", DifficultyLevel.EASY),
        ("🚀 Difícil", DifficultyLevel.HARD),
        ("⭐ Mittel", DifficultyLevel.MEDIUM),
        ("⭐ Médio", DifficultyLevel.MEDIUM),
        (None, DifficultyLevel.MEDIUM),
        ("", DifficultyLevel.MEDIUM),
        ("unknown_garbage", DifficultyLevel.MEDIUM),
        (12345, DifficultyLevel.MEDIUM),
    ],
)
def test_from_raw_parsing(raw_input: object, expected: DifficultyLevel) -> None:
    assert DifficultyLevel.from_raw(raw_input) == expected


def test_from_raw_custom_default() -> None:
    assert (
        DifficultyLevel.from_raw(None, default=DifficultyLevel.HARD)
        == DifficultyLevel.HARD
    )
    assert (
        DifficultyLevel.from_raw("invalid", default=DifficultyLevel.EASY)
        == DifficultyLevel.EASY
    )


def test_quiz_model_difficulty_validation_and_serialization() -> None:
    sample_questions = [
        QuizQuestion(
            question=f"Q{i}",
            options=["A", "B", "C", "D"],
            correct_option_index=0,
            explanation=f"Exp {i}",
        )
        for i in range(10)
    ]

    quiz_easy = Quiz(
        title="Sample Quiz",
        questions=sample_questions,
        difficulty=DifficultyLevel.EASY,
    )
    assert quiz_easy.difficulty == DifficultyLevel.EASY
    assert quiz_easy.model_dump()["difficulty"] == "easy"
    assert '"difficulty":"easy"' in quiz_easy.model_dump_json()

    quiz_from_str = Quiz(
        title="Sample Quiz",
        questions=sample_questions,
        difficulty="hard",
    )
    assert quiz_from_str.difficulty == DifficultyLevel.HARD

    default_quiz = Quiz(
        title="Sample Quiz",
        questions=sample_questions,
    )
    assert default_quiz.difficulty == DifficultyLevel.MEDIUM


def test_generated_quiz_difficulty_normalization() -> None:
    sample_gen_questions = [
        GeneratedQuizQuestion(
            question=f"Q{i}",
            options=["Alpha", "Beta", "Gamma", "Delta"],
            correct_answer="Alpha",
            explanation=f"Exp {i}",
        )
        for i in range(10)
    ]

    # Legacy decorated string normalized to semantic code
    gen_quiz = GeneratedQuiz(
        title="Legacy Test",
        questions=sample_gen_questions,
        difficulty="⭐ Medium",
    )
    normalized = normalize_generated_quiz(gen_quiz, grade="5")
    assert normalized["difficulty"] == "medium"

    # Semantic code preserved
    gen_quiz_hard = GeneratedQuiz(
        title="Hard Test",
        questions=sample_gen_questions,
        difficulty=DifficultyLevel.HARD,
    )
    normalized_hard = normalize_generated_quiz(gen_quiz_hard, grade="5")
    assert normalized_hard["difficulty"] == "hard"

    # None preserved
    gen_quiz_none = GeneratedQuiz(
        title="None Test",
        questions=sample_gen_questions,
        difficulty=None,
    )
    normalized_none = normalize_generated_quiz(gen_quiz_none, grade="5")
    assert normalized_none["difficulty"] is None
