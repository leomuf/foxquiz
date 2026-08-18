# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the deterministic public FoxQuiz request contract."""

import json

import pytest

from app.domain.quiz_request import (
    QuizRequestValidationError,
    invalid_request_message,
    parse_quiz_request,
)


def test_initial_request_uses_safe_optional_defaults() -> None:
    request = parse_quiz_request(
        json.dumps(
            {
                "grade": "Grade 8",
                "subject": "Biology",
                "topic": "Cells",
            }
        )
    )

    assert request.preferred_language == "en"
    assert request.mascot_id == "fox"
    assert request.previous_score is None


def test_clarification_request_preserves_original_topic_and_added_scope() -> None:
    request = parse_quiz_request(
        json.dumps(
            {
                "grade": "Grade 12",
                "subject": "Mathematics",
                "topic": "Multiplication",
                "preferred_language": "en",
                "clarification_response": "Matrix multiplication",
            }
        )
    )

    assert request.topic == "Multiplication"
    assert request.clarification_response == "Matrix multiplication"


def test_adaptive_request_accepts_previous_quiz_object() -> None:
    request = parse_quiz_request(
        json.dumps(
            {
                "grade": "Klasse 6",
                "subject": "Science",
                "topic": "States of matter",
                "previous_score": 2,
                "previous_questions": ["Previous question?"],
                "previous_quiz_json": {"title": "Previous quiz"},
            }
        )
    )

    assert request.previous_score == 2
    assert request.previous_quiz_json == {"title": "Previous quiz"}


@pytest.mark.parametrize(
    "payload",
    [
        "Create a Grade 8 biology quiz about private-marker-should-not-appear.",
        "{not-json-private-marker-should-not-appear}",
        json.dumps(
            {
                "grade": "Grade 8",
                "subject": "private-marker-should-not-appear",
            }
        ),
        json.dumps(
            {
                "grade": "Grade 8",
                "subject": "Biology",
                "topic": "Cells",
                "unsupported": "private-marker-should-not-appear",
            }
        ),
        json.dumps(
            {
                "grade": "Grade 8",
                "subject": "Biology",
                "topic": "private-marker-should-not-appear",
                "previous_score": 11,
            }
        ),
    ],
)
def test_unsupported_payloads_fail_with_privacy_safe_error(payload: str) -> None:
    private_marker = "private-marker-should-not-appear"

    with pytest.raises(QuizRequestValidationError) as exc_info:
        parse_quiz_request(payload)

    assert private_marker not in str(exc_info.value)


@pytest.mark.parametrize(
    ("locale", "expected_fragment"),
    [("de-DE", "Quizformat"), ("pt-BR", "formato"), ("fr", "quiz format")],
)
def test_invalid_request_message_is_localized(
    locale: str, expected_fragment: str
) -> None:
    assert expected_fragment in invalid_request_message(locale)
