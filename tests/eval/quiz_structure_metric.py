# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Deterministically validate the structure of released FoxQuiz JSON.

The evaluation CLI executes this file as standalone source, so it intentionally
uses only the Python standard library and does not import application modules.
"""

import json
import unicodedata
from typing import Any


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


def _normalize_option(value: str) -> str:
    """Match FoxQuiz's case-sensitive Unicode and whitespace semantics."""
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _requested_grade(instance: dict[str, Any]) -> int | None:
    """Extract a canonical grade number from the structured user prompt."""
    prompt = instance.get("prompt")
    if not isinstance(prompt, dict) or not isinstance(prompt.get("parts"), list):
        return None
    for part in prompt["parts"]:
        if not isinstance(part, dict) or not isinstance(part.get("text"), str):
            continue
        try:
            request = json.loads(part["text"])
        except json.JSONDecodeError:
            continue
        if not isinstance(request, dict):
            continue
        grade = request.get("grade")
        if not isinstance(grade, str):
            continue
        words = grade.replace("º", " ").split()
        for word in words:
            if word.isdigit() and 1 <= int(word) <= 12:
                return int(word)
    return None


def _structural_issue_codes(
    candidate: Any, *, required_option_count: int | None = None
) -> set[str]:
    """Return privacy-safe issue codes for violations of the quiz contract."""
    if not isinstance(candidate, dict):
        return {"invalid_quiz"}
    questions = candidate.get("questions")
    if not isinstance(questions, list):
        return {"invalid_quiz"}

    issues: set[str] = set()
    if len(questions) != 10:
        issues.add("wrong_question_count")
    for question in questions:
        if not isinstance(question, dict):
            issues.add("invalid_question")
            continue
        if (
            not isinstance(question.get("question"), str)
            or not question["question"].strip()
        ):
            issues.add("empty_question")
        if (
            not isinstance(question.get("explanation"), str)
            or not question["explanation"].strip()
        ):
            issues.add("empty_explanation")

        options = question.get("options")
        if not isinstance(options, list):
            issues.add("invalid_option_count")
            continue
        if required_option_count is not None:
            valid_option_count = len(options) == required_option_count
        else:
            valid_option_count = 3 <= len(options) <= 5
        if not valid_option_count:
            issues.add("invalid_option_count")

        correct_index = question.get("correct_option_index")
        if (
            isinstance(correct_index, bool)
            or not isinstance(correct_index, int)
            or not 0 <= correct_index < len(options)
        ):
            issues.add("invalid_correct_index")

        normalized_options: set[str] = set()
        for option in options:
            if not isinstance(option, str) or not option.strip():
                issues.add("empty_option")
                continue
            normalized = _normalize_option(option)
            if normalized in normalized_options:
                issues.add("duplicate_option")
            normalized_options.add(normalized)
    return issues


def evaluate(instance: dict[str, Any]) -> dict[str, float | str]:
    """Return the local-metric score and explanation expected by agents-cli.

    The final text part of ``instance["response"]`` must contain a JSON quiz
    satisfying every structural invariant. Valid quizzes score 1; missing,
    malformed, or structurally invalid responses score 0.
    """
    response_text = _final_response_text(instance)
    if response_text is None:
        return {"score": 0.0, "explanation": "No final text response was available."}

    try:
        candidate = json.loads(response_text)
    except (TypeError, json.JSONDecodeError):
        return {"score": 0.0, "explanation": "The final response was not valid JSON."}

    grade = _requested_grade(instance)
    required_option_count = 3 if grade in {1, 2} else None
    issue_codes = sorted(
        _structural_issue_codes(candidate, required_option_count=required_option_count)
    )
    if not issue_codes:
        return {
            "score": 1.0,
            "explanation": "The released quiz passed every structural invariant.",
        }

    return {
        "score": 0.0,
        "explanation": "Deterministic validation failed: " + ", ".join(issue_codes),
    }
