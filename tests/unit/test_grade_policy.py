# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the authoritative school-grade policy."""

import pytest

from app.domain.grade_policy import (
    Grade,
    PedagogicalStage,
    build_grade_prompt_guidance,
    get_grade_policy,
    parse_grade,
)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Klasse 1", Grade.GRADE_1),
        ("Grade 4", Grade.GRADE_4),
        ("3º Ano", Grade.GRADE_3),
        ("2º Ano do Ensino Médio", Grade.GRADE_11),
    ],
)
def test_parse_grade_accepts_supported_localized_labels(
    label: str, expected: Grade
) -> None:
    """Public localized labels resolve to one unambiguous grade enum."""
    assert parse_grade(label) is expected


def test_parse_grade_rejects_values_outside_supported_range() -> None:
    """Arbitrary grade text cannot bypass the central policy."""
    with pytest.raises(ValueError, match="Unsupported school grade"):
        parse_grade("University")


def test_early_primary_policy_requires_exactly_three_options() -> None:
    """Grades 1 and 2 use the strictest structural and language contract."""
    policy = get_grade_policy("Grade 2")

    assert policy.stage is PedagogicalStage.PRIMARY_EARLY
    assert (policy.minimum_options, policy.maximum_options) == (3, 3)
    assert policy.maximum_explanation_sentences == 2
    assert not policy.negation_questions_allowed
    assert policy.question_emojis_allowed
    assert "one or two short sentences" in build_grade_prompt_guidance(policy)


def test_existing_secondary_policy_keeps_three_to_five_options() -> None:
    """The feature preserves the established structural range for Grade 5+."""
    policy = get_grade_policy("Klasse 5")

    assert policy.stage is PedagogicalStage.SECONDARY_LOWER
    assert (policy.minimum_options, policy.maximum_options) == (3, 5)
    assert policy.question_emojis_allowed


@pytest.mark.parametrize(
    ("grade_input", "lang", "expected_label"),
    [
        ("Grade 5", "de", "Klasse 5"),
        ("Grade 5", "en", "Grade 5"),
        ("Grade 5", "pt", "5º ano"),
        ("Grade 11", "de", "Klasse 11"),
        ("Grade 11", "en", "Grade 11"),
        ("Grade 11", "pt", "2º ano do ensino médio"),
        ("Grade 1", "en", "Grade 1"),
        ("Grade 1", "pt", "1º ano"),
    ],
)
def test_grade_policy_localized_label(
    grade_input: str, lang: str, expected_label: str
) -> None:
    """GradePolicy localized_label produces correct strings for de, en, pt."""
    policy = get_grade_policy(grade_input)
    assert policy.localized_label(lang) == expected_label
