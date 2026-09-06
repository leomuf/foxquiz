# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the deterministic evaluation metric wrapper."""

import json
import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _evaluate() -> Callable[[dict[str, Any]], dict[str, float | str]]:
    """Load the metric exactly as standalone evaluation code."""
    metric_path = Path(__file__).parents[1] / "eval" / "quiz_structure_metric.py"
    return runpy.run_path(str(metric_path))["evaluate"]


def _quiz() -> dict[str, Any]:
    """Build the smallest ten-question quiz satisfying every invariant."""
    return {
        "title": "Arithmetic practice",
        "difficulty": "⭐ Medium",
        "questions": [
            {
                "question": f"What is {number} + 1?",
                "options": [str(number + 1), str(number + 2), str(number + 3)],
                "correct_option_index": 0,
                "explanation": f"{number} plus one is {number + 1}.",
            }
            for number in range(1, 11)
        ],
    }


def _instance(response_text: str, *, grade: str | None = None) -> dict[str, Any]:
    """Wrap final response text in the shape supplied by agents-cli."""
    instance = {"response": {"role": "model", "parts": [{"text": response_text}]}}
    if grade is not None:
        instance["prompt"] = {
            "role": "user",
            "parts": [{"text": json.dumps({"grade": grade})}],
        }
    return instance


def test_quiz_structure_metric_accepts_valid_quiz() -> None:
    """A released quiz satisfying every deterministic invariant scores one."""
    result = _evaluate()(_instance(json.dumps(_quiz())))

    assert result == {
        "score": 1.0,
        "explanation": "The released quiz passed every structural invariant.",
    }


def test_quiz_structure_metric_rejects_normalized_duplicate() -> None:
    """The eval metric must reuse normalized duplicate detection."""
    quiz = _quiz()
    quiz["questions"][0]["options"] = [" Recessive ", "Recessive", "Dominant"]

    result = _evaluate()(_instance(json.dumps(quiz)))

    assert result["score"] == 0.0
    assert result["explanation"] == "Deterministic validation failed: duplicate_option"


def test_quiz_structure_metric_accepts_case_sensitive_genotypes() -> None:
    """Evaluation must not collapse scientifically distinct genotype notation."""
    quiz = _quiz()
    quiz["questions"][0]["options"] = ["PP", "Pp", "pp"]
    quiz["questions"][1]["options"] = ["Todos BB", "Todos Bb", "Todos bb"]

    result = _evaluate()(_instance(json.dumps(quiz)))

    assert result["score"] == 1.0


def test_quiz_structure_metric_rejects_non_json_response() -> None:
    """A clarification or quality-failure message cannot pass structural grading."""
    result = _evaluate()(_instance("Please try again."))

    assert result == {
        "score": 0.0,
        "explanation": "The final response was not valid JSON.",
    }


def test_quiz_structure_metric_enforces_three_options_for_grade_one() -> None:
    """Behavioral grading mirrors the Grade 1 deterministic invariant."""
    quiz = _quiz()
    quiz["questions"][0]["options"].append("A fourth option")

    result = _evaluate()(_instance(json.dumps(quiz), grade="Klasse 1"))

    assert result["score"] == 0.0
    assert (
        result["explanation"] == "Deterministic validation failed: invalid_option_count"
    )
