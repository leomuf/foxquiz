# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Pure deterministic validation for generated quiz candidates.

The validator has no ADK, persistence, or model dependencies. It reports stable,
privacy-safe issue codes and positions so workflow routing, diagnostics, and
unit tests do not need to retain generated question or option text.
"""

import unicodedata
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

import emoji

EXPECTED_QUESTION_COUNT = 10
MIN_OPTION_COUNT = 3
MAX_OPTION_COUNT = 5
ANSWER_CUE_MARKERS = frozenset(
    {
        "\u2705",  # white heavy check mark
        "\u2713",  # check mark
        "\u2714",  # heavy check mark
        "\u2611",  # ballot box with check
        "\u274c",  # cross mark
        "\u2717",  # ballot x
        "\u2718",  # heavy ballot x
    }
)


class QuizValidationCode(StrEnum):
    """Stable categories for deterministic quiz-quality failures."""

    INVALID_QUIZ = "invalid_quiz"
    WRONG_QUESTION_COUNT = "wrong_question_count"
    INVALID_QUESTION = "invalid_question"
    EMPTY_QUESTION = "empty_question"
    INVALID_OPTION_COUNT = "invalid_option_count"
    EMPTY_OPTION = "empty_option"
    DUPLICATE_OPTION = "duplicate_option"
    INVALID_CORRECT_INDEX = "invalid_correct_index"
    EMOJI_IN_OPTION = "emoji_in_option"
    ANSWER_CUE_IN_OPTION = "answer_cue_in_option"
    EMPTY_EXPLANATION = "empty_explanation"


@dataclass(frozen=True)
class QuizValidationIssue:
    """A non-sensitive issue location suitable for state and diagnostics."""

    code: QuizValidationCode
    question_index: int | None = None
    option_index: int | None = None

    def as_dict(self) -> dict[str, str | int]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class QuizValidationResult:
    """The complete deterministic assessment of one candidate."""

    issues: tuple[QuizValidationIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not self.issues


def normalize_option(value: str) -> str:
    """Normalize Unicode and whitespace while preserving meaningful case."""
    return " ".join(unicodedata.normalize("NFKC", value).split())


def find_emojis(value: str) -> tuple[str, ...]:
    """Return complete Unicode emoji sequences found in text."""
    return tuple(match["emoji"] for match in emoji.emoji_list(value))


def validate_quiz_candidate(candidate: Any) -> QuizValidationResult:
    """Validate fast, objective invariants without changing the candidate."""
    issues: list[QuizValidationIssue] = []
    if not isinstance(candidate, dict):
        return QuizValidationResult(
            (QuizValidationIssue(QuizValidationCode.INVALID_QUIZ),)
        )

    questions = candidate.get("questions")
    if not isinstance(questions, list):
        return QuizValidationResult(
            (QuizValidationIssue(QuizValidationCode.INVALID_QUIZ),)
        )

    if len(questions) != EXPECTED_QUESTION_COUNT:
        issues.append(QuizValidationIssue(QuizValidationCode.WRONG_QUESTION_COUNT))

    for question_index, question in enumerate(questions):
        if not isinstance(question, dict):
            issues.append(
                QuizValidationIssue(
                    QuizValidationCode.INVALID_QUESTION,
                    question_index=question_index,
                )
            )
            continue

        question_text = question.get("question")
        if not isinstance(question_text, str) or not question_text.strip():
            issues.append(
                QuizValidationIssue(
                    QuizValidationCode.EMPTY_QUESTION,
                    question_index=question_index,
                )
            )

        explanation = question.get("explanation")
        if not isinstance(explanation, str) or not explanation.strip():
            issues.append(
                QuizValidationIssue(
                    QuizValidationCode.EMPTY_EXPLANATION,
                    question_index=question_index,
                )
            )

        options = question.get("options")
        if not isinstance(options, list):
            issues.append(
                QuizValidationIssue(
                    QuizValidationCode.INVALID_OPTION_COUNT,
                    question_index=question_index,
                )
            )
            continue

        if not MIN_OPTION_COUNT <= len(options) <= MAX_OPTION_COUNT:
            issues.append(
                QuizValidationIssue(
                    QuizValidationCode.INVALID_OPTION_COUNT,
                    question_index=question_index,
                )
            )

        correct_index = question.get("correct_option_index")
        # Reject booleans explicitly because bool is a subclass of int in Python;
        # then ensure the index points to an existing option.
        if (
            isinstance(correct_index, bool)
            or not isinstance(correct_index, int)
            or not 0 <= correct_index < len(options)
        ):
            issues.append(
                QuizValidationIssue(
                    QuizValidationCode.INVALID_CORRECT_INDEX,
                    question_index=question_index,
                )
            )

        normalized_options: dict[str, int] = {}
        for option_index, option in enumerate(options):
            if not isinstance(option, str) or not option.strip():
                issues.append(
                    QuizValidationIssue(
                        QuizValidationCode.EMPTY_OPTION,
                        question_index=question_index,
                        option_index=option_index,
                    )
                )
                continue

            # Compare canonicalized text so whitespace or Unicode representation
            # cannot disguise duplicates. Preserve case because it can change the
            # meaning of scientific notation such as PP, Pp, and pp genotypes.
            normalized = normalize_option(option)
            if normalized in normalized_options:
                issues.append(
                    QuizValidationIssue(
                        QuizValidationCode.DUPLICATE_OPTION,
                        question_index=question_index,
                        option_index=option_index,
                    )
                )
            else:
                normalized_options[normalized] = option_index

            # Report explicit correctness symbols more precisely than generic
            # emojis, while still rejecting every other emoji in answer options.
            if any(marker in option for marker in ANSWER_CUE_MARKERS):
                issues.append(
                    QuizValidationIssue(
                        QuizValidationCode.ANSWER_CUE_IN_OPTION,
                        question_index=question_index,
                        option_index=option_index,
                    )
                )
            elif find_emojis(option):
                issues.append(
                    QuizValidationIssue(
                        QuizValidationCode.EMOJI_IN_OPTION,
                        question_index=question_index,
                        option_index=option_index,
                    )
                )

    return QuizValidationResult(tuple(issues))


def build_retry_guidance(result: QuizValidationResult) -> str:
    """Build concise generator feedback without embedding candidate content."""
    lines = ["The previous candidate failed deterministic validation:"]
    for issue in result.issues:
        location = ""
        if issue.question_index is not None:
            location = f"Question {issue.question_index + 1}"
        if issue.option_index is not None:
            location += f", option {issue.option_index + 1}"
        prefix = f"{location}: " if location else ""
        lines.append(f"- {prefix}{issue.code.value.replace('_', ' ')}.")
    if any(
        issue.code is QuizValidationCode.DUPLICATE_OPTION for issue in result.issues
    ):
        lines.append(
            "- Duplicate-option correction: within each affected question, "
            "compare every pair of options after Unicode normalization, trimming or "
            "collapsing whitespace while preserving meaningful capitalization; replace every "
            "repeated or equivalent choice with a meaningfully distinct distractor."
        )
    lines.append("Regenerate the complete quiz and correct every listed issue.")
    return "\n".join(lines)
