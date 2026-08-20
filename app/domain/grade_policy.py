# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Authoritative grade parsing and pedagogical rules for FoxQuiz."""

from dataclasses import dataclass
from enum import IntEnum, StrEnum


class Grade(IntEnum):
    """Supported school grades, ordered from first through twelfth grade."""

    GRADE_1 = 1
    GRADE_2 = 2
    GRADE_3 = 3
    GRADE_4 = 4
    GRADE_5 = 5
    GRADE_6 = 6
    GRADE_7 = 7
    GRADE_8 = 8
    GRADE_9 = 9
    GRADE_10 = 10
    GRADE_11 = 11
    GRADE_12 = 12


class PedagogicalStage(StrEnum):
    """Age bands that require materially different quiz language and design."""

    PRIMARY_EARLY = "primary_early"
    PRIMARY_LATE = "primary_late"
    SECONDARY_LOWER = "secondary_lower"
    SECONDARY_UPPER = "secondary_upper"


@dataclass(frozen=True, slots=True)
class GradePolicy:
    """Deterministic structural and pedagogical constraints for one grade."""

    grade: Grade
    stage: PedagogicalStage
    minimum_age: int
    maximum_age: int
    minimum_options: int
    maximum_options: int
    maximum_explanation_sentences: int | None
    negation_questions_allowed: bool
    question_emojis_allowed: bool

    @property
    def canonical_value(self) -> str:
        """Return the stable value used in requests and internal state."""
        return f"Klasse {int(self.grade)}"

    @property
    def option_count_instruction(self) -> str:
        """Describe the allowed number of options without duplicating policy text."""
        if self.minimum_options == self.maximum_options:
            return f"exactly {self.minimum_options} answer options"
        return (
            f"between {self.minimum_options} and {self.maximum_options} answer options"
        )


def policy_for_grade(grade: Grade) -> GradePolicy:
    """Build the immutable policy associated with a supported grade."""
    if grade <= Grade.GRADE_2:
        return GradePolicy(
            grade=grade,
            stage=PedagogicalStage.PRIMARY_EARLY,
            minimum_age=6,
            maximum_age=8,
            minimum_options=3,
            maximum_options=3,
            maximum_explanation_sentences=2,
            negation_questions_allowed=False,
            question_emojis_allowed=False,
        )
    if grade <= Grade.GRADE_4:
        return GradePolicy(
            grade=grade,
            stage=PedagogicalStage.PRIMARY_LATE,
            minimum_age=8,
            maximum_age=10,
            minimum_options=3,
            maximum_options=5,
            maximum_explanation_sentences=None,
            negation_questions_allowed=False,
            question_emojis_allowed=False,
        )
    if grade <= Grade.GRADE_8:
        return GradePolicy(
            grade=grade,
            stage=PedagogicalStage.SECONDARY_LOWER,
            minimum_age=10,
            maximum_age=14,
            minimum_options=3,
            maximum_options=5,
            maximum_explanation_sentences=None,
            negation_questions_allowed=True,
            question_emojis_allowed=True,
        )
    return GradePolicy(
        grade=grade,
        stage=PedagogicalStage.SECONDARY_UPPER,
        minimum_age=14,
        maximum_age=18,
        minimum_options=3,
        maximum_options=5,
        maximum_explanation_sentences=None,
        negation_questions_allowed=True,
        question_emojis_allowed=True,
    )


_GRADE_ALIASES = {
    alias.casefold(): grade
    for grade in Grade
    for alias in (
        f"Klasse {int(grade)}",
        f"Grade {int(grade)}",
        str(int(grade)),
    )
}
_GRADE_ALIASES.update({f"{year}º ano".casefold(): Grade(year) for year in range(1, 10)})
_GRADE_ALIASES.update(
    {
        f"{year}º ano do ensino fundamental".casefold(): Grade(year)
        for year in range(1, 10)
    }
)
_GRADE_ALIASES.update(
    {f"{year}º ano do ensino médio".casefold(): Grade(year + 9) for year in range(1, 4)}
)


def parse_grade(value: str) -> Grade:
    """Resolve a supported localized grade label or raise ``ValueError``."""
    normalized = " ".join(value.split()).casefold()
    try:
        return _GRADE_ALIASES[normalized]
    except KeyError:
        raise ValueError("Unsupported school grade.") from None


def get_grade_policy(value: str | Grade) -> GradePolicy:
    """Return the authoritative policy for a request grade."""
    grade = value if isinstance(value, Grade) else parse_grade(value)
    return policy_for_grade(grade)


def build_grade_prompt_guidance(policy: GradePolicy) -> str:
    """Render the shared age-specific contract for evaluator, generator, and judge."""
    common = (
        f"The learner is in Grade {int(policy.grade)}, approximately ages "
        f"{policy.minimum_age}-{policy.maximum_age}. Each question must have "
        f"{policy.option_count_instruction}."
    )
    if policy.stage is PedagogicalStage.PRIMARY_EARLY:
        return common + (
            " Use very short, concrete, easily readable questions and answers. "
            "Use simple, clearly distinct distractors and familiar examples. "
            "Each explanation must contain only one or two short sentences. "
            "Do not use negative questions or double negatives. Do not put "
            "emojis in question text; even playful pictograms can reveal answers."
        )
    if policy.stage is PedagogicalStage.PRIMARY_LATE:
        return common + (
            " Use short, simple language and concrete examples while allowing "
            "slightly more detailed questions. Avoid complicated distractors. "
            "Do not use negative questions or double negatives. Do not put "
            "emojis in question text; even playful pictograms can reveal answers."
        )
    if policy.stage is PedagogicalStage.SECONDARY_LOWER:
        return common + (
            " Keep the tone playful, simplified, and age-appropriate without "
            "talking down to the learner."
        )
    return common + (
        " Use a supportive peer-mentor tone with intellectually respectful, "
        "advanced, and clear explanations."
    )
