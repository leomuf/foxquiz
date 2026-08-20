# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Deterministic input contract for every supported FoxQuiz request."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.domain.grade_policy import get_grade_policy

_INVALID_REQUEST_MESSAGES = {
    "de": "Diese Anfrage hat nicht das erwartete Quizformat. Bitte verwende das FoxQuiz-Formular und versuche es erneut.",
    "pt": "Esta solicitação não está no formato de quiz esperado. Use o formulário do FoxQuiz e tente novamente.",
    "en": "This request does not use the expected quiz format. Please use the FoxQuiz form and try again.",
}


class QuizRequestValidationError(ValueError):
    """Raised without user content when a request violates the public contract."""


class QuizRequest(BaseModel):
    """Structured initial, clarification, or adaptive quiz request."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    grade: str = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=200)
    topic: str = Field(min_length=1, max_length=500)
    preferred_language: Literal["de", "pt", "en"] = "en"
    mascot_id: Literal["fox", "owl", "dragon"] = "fox"
    previous_score: int | None = Field(default=None, ge=0, le=10)
    previous_questions: list[str] | None = Field(default=None, max_length=10)
    previous_quiz_json: str | dict[str, Any] | None = None
    selected_difficulty: Literal["medium", "hard"] | None = None
    clarification_response: str | None = Field(default=None, max_length=500)

    @field_validator("grade")
    @classmethod
    def normalize_grade(cls, value: str) -> str:
        """Normalize supported localized labels to the stable internal value."""
        return get_grade_policy(value).canonical_value


def parse_quiz_request(payload: str) -> QuizRequest:
    """Parse JSON text into the shared contract without exposing invalid input."""
    try:
        decoded = json.loads(payload)
        return QuizRequest.model_validate(decoded)
    except (json.JSONDecodeError, TypeError, ValidationError):
        raise QuizRequestValidationError(
            "Request does not match the structured FoxQuiz contract."
        ) from None


def invalid_request_message(locale: str) -> str:
    """Return the fixed public contract error for a supported locale."""
    normalized = locale.lower()
    if normalized.startswith("de"):
        language = "de"
    elif normalized.startswith("pt"):
        language = "pt"
    else:
        language = "en"
    return _INVALID_REQUEST_MESSAGES[language]
