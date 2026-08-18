# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Deterministically validate structured-request evaluation outcomes."""

import json
from typing import Any

_INVALID_REQUEST_MESSAGE = (
    "This request does not use the expected quiz format. "
    "Please use the FoxQuiz form and try again."
)


def _final_response_text(instance: dict[str, Any]) -> str | None:
    """Extract the last non-empty text part from an evaluation response."""
    response = instance.get("response")
    if not isinstance(response, dict):
        return None
    parts = response.get("parts")
    if not isinstance(parts, list):
        return None
    for part in reversed(parts):
        if isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None


def _json_object(response_text: str) -> dict[str, Any] | None:
    """Decode one JSON object, rejecting arrays and scalar JSON values."""
    try:
        decoded = json.loads(response_text)
    except (TypeError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _valid_quiz(candidate: dict[str, Any], expected_difficulty: Any) -> bool:
    """Check the release shape needed by the request-contract measurement."""
    questions = candidate.get("questions")
    return (
        isinstance(questions, list)
        and len(questions) == 10
        and candidate.get("difficulty") == expected_difficulty
    )


def evaluate(instance: dict[str, Any]) -> dict[str, float | str]:
    """Score whether the final response matches the case's expected outcome."""
    expected_outcome = instance.get("expected_outcome")
    response_text = _final_response_text(instance)
    if response_text is None:
        return {"score": 0.0, "explanation": "No final text response was available."}

    if expected_outcome == "invalid_request":
        passed = response_text == _INVALID_REQUEST_MESSAGE
    else:
        candidate = _json_object(response_text)
        if expected_outcome == "blocked":
            passed = bool(candidate and candidate.get("status") == "blocked")
        elif expected_outcome == "clarification_required":
            passed = bool(
                candidate and candidate.get("status") == "clarification_required"
            )
        elif expected_outcome == "quiz":
            passed = bool(
                candidate
                and _valid_quiz(candidate, instance.get("expected_difficulty"))
            )
        else:
            return {
                "score": 0.0,
                "explanation": "The evaluation case has an unknown expected outcome.",
            }

    return {
        "score": 1.0 if passed else 0.0,
        "explanation": (
            "The response matched the expected request-contract outcome."
            if passed
            else "The response did not match the expected request-contract outcome."
        ),
    }
