# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Internal LLM quiz contracts and deterministic answer normalization."""

import random
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.difficulty import DifficultyLevel
from app.domain.grade_policy import Grade, get_grade_policy
from app.domain.quiz_validation import normalize_option


class QuizNormalizationCode(StrEnum):
    """Stable categories for failures at the LLM-to-public quiz boundary."""

    INVALID_GENERATED_QUIZ = "invalid_generated_quiz"
    INVALID_GENERATED_QUESTION = "invalid_generated_question"
    INVALID_OPTION_COUNT = "invalid_option_count"
    EMPTY_OPTION = "empty_option"
    CORRECT_ANSWER_NOT_IN_OPTIONS = "correct_answer_not_in_options"
    AMBIGUOUS_CORRECT_ANSWER = "ambiguous_correct_answer"


class GeneratedQuizQuestion(BaseModel):
    """Question shape requested from the LLM before public normalization."""

    model_config = ConfigDict(extra="forbid", strict=True)

    question: str = Field(description="The complete question text.")
    options: list[str] = Field(
        description="Neutral text-only answer choices without emojis or answer cues."
    )
    correct_answer: str = Field(
        description=(
            "The exact answer text selected from the options, before application "
            "normalization and shuffling."
        )
    )
    explanation: str = Field(
        description="A clear, factual, age-appropriate explanation of the answer."
    )


class GeneratedQuiz(BaseModel):
    """Quiz shape requested from the LLM before public normalization."""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(description="A fun and engaging title for the quiz.")
    questions: list[GeneratedQuizQuestion] = Field(
        description="List of exactly 10 generated questions."
    )
    difficulty: DifficultyLevel | str | None = Field(
        default=None,
        description=(
            "The requested difficulty level. Application code supplies the "
            "authoritative public value."
        ),
    )


class QuizNormalizationError(ValueError):
    """Raised when an internal generated question cannot become public safely."""

    def __init__(
        self,
        code: QuizNormalizationCode,
        *,
        question_index: int | None = None,
    ) -> None:
        self.code = code
        self.question_index = question_index
        super().__init__(code.value)

    def as_dict(self) -> dict[str, str | int]:
        """Return privacy-safe normalization diagnostics."""
        payload: dict[str, str | int] = {"code": self.code.value}
        if self.question_index is not None:
            payload["question_index"] = self.question_index
        return payload


def _option_bounds(grade: str | Grade | None) -> tuple[int, int]:
    if grade is None:
        return 3, 5
    policy = get_grade_policy(grade)
    return policy.minimum_options, policy.maximum_options


def normalize_generated_question(
    generated: GeneratedQuizQuestion | dict[str, Any],
    *,
    grade: str | Grade | None = None,
    question_index: int | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Convert one internal question into the public question shape.

    The returned mapping contains only public fields. Options and the selected
    answer are compared after the same NFKC/whitespace normalization used by
    duplicate detection, while meaningful capitalization remains significant.
    """
    try:
        question = (
            generated
            if isinstance(generated, GeneratedQuizQuestion)
            else GeneratedQuizQuestion.model_validate(generated)
        )
    except ValidationError as error:
        raise QuizNormalizationError(
            QuizNormalizationCode.INVALID_GENERATED_QUESTION,
            question_index=question_index,
        ) from error

    minimum_options, maximum_options = _option_bounds(grade)
    if not minimum_options <= len(question.options) <= maximum_options:
        raise QuizNormalizationError(
            QuizNormalizationCode.INVALID_OPTION_COUNT,
            question_index=question_index,
        )

    normalized_options: list[str] = []
    for option in question.options:
        normalized = normalize_option(option)
        if not normalized:
            raise QuizNormalizationError(
                QuizNormalizationCode.EMPTY_OPTION,
                question_index=question_index,
            )
        normalized_options.append(normalized)

    normalized_answer = normalize_option(question.correct_answer)
    matches = [
        option_index
        for option_index, option in enumerate(normalized_options)
        if option == normalized_answer
    ]
    if not matches:
        raise QuizNormalizationError(
            QuizNormalizationCode.CORRECT_ANSWER_NOT_IN_OPTIONS,
            question_index=question_index,
        )
    if len(matches) != 1:
        raise QuizNormalizationError(
            QuizNormalizationCode.AMBIGUOUS_CORRECT_ANSWER,
            question_index=question_index,
        )

    permutation = list(range(len(normalized_options)))
    if rng is not None:
        rng.shuffle(permutation)
    else:
        random.shuffle(permutation)

    shuffled_options = [normalized_options[index] for index in permutation]
    public_question = {
        "question": question.question,
        "options": shuffled_options,
        "correct_option_index": permutation.index(matches[0]),
        "explanation": question.explanation,
    }
    return public_question


def normalize_generated_quiz(
    generated: GeneratedQuiz | dict[str, Any],
    *,
    grade: str | Grade | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Convert an internal generated quiz to a public quiz mapping."""
    try:
        quiz = (
            generated
            if isinstance(generated, GeneratedQuiz)
            else GeneratedQuiz.model_validate(generated)
        )
    except ValidationError as error:
        raise QuizNormalizationError(
            QuizNormalizationCode.INVALID_GENERATED_QUIZ
        ) from error

    return {
        "title": quiz.title,
        "questions": [
            normalize_generated_question(
                question,
                grade=grade,
                question_index=question_index,
                rng=rng,
            )
            for question_index, question in enumerate(quiz.questions)
        ],
        "difficulty": (
            DifficultyLevel.from_raw(quiz.difficulty).value
            if quiz.difficulty is not None
            else None
        ),
    }
