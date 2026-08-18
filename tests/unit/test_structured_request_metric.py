# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the deterministic structured-request outcome metric."""

import json
import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _evaluate() -> Callable[[dict[str, Any]], dict[str, float | str]]:
    """Load the metric exactly as standalone evaluation code."""
    metric_path = Path(__file__).parents[1] / "eval" / "structured_request_metric.py"
    return runpy.run_path(str(metric_path))["evaluate"]


def _instance(
    response_text: str,
    expected_outcome: str,
    *,
    expected_difficulty: str | None = None,
) -> dict[str, Any]:
    """Build the custom fields and response shape supplied by agents-cli."""
    instance = {
        "expected_outcome": expected_outcome,
        "response": {"role": "model", "parts": [{"text": response_text}]},
    }
    if expected_difficulty is not None:
        instance["expected_difficulty"] = expected_difficulty
    return instance


def test_metric_accepts_fixed_invalid_request_response() -> None:
    """A contract rejection passes only with the fixed privacy-safe response."""
    result = _evaluate()(
        _instance(
            "This request does not use the expected quiz format. "
            "Please use the FoxQuiz form and try again.",
            "invalid_request",
        )
    )

    assert result["score"] == 1.0


def test_metric_accepts_structured_block_and_clarification() -> None:
    """Security and clarification branches must expose their status envelopes."""
    evaluate = _evaluate()

    blocked = evaluate(
        _instance(
            json.dumps({"status": "blocked", "block_type": "MALICIOUS"}),
            "blocked",
        )
    )
    clarification = evaluate(
        _instance(
            json.dumps({"status": "clarification_required", "message": "Scope?"}),
            "clarification_required",
        )
    )

    assert blocked["score"] == 1.0
    assert clarification["score"] == 1.0


def test_metric_accepts_quiz_with_expected_difficulty() -> None:
    """A delivered quiz must contain ten questions at the expected difficulty."""
    quiz = {
        "difficulty": "⭐ Medium",
        "questions": [{"question": f"Question {number}"} for number in range(10)],
    }

    result = _evaluate()(
        _instance(
            json.dumps(quiz),
            "quiz",
            expected_difficulty="⭐ Medium",
        )
    )

    assert result["score"] == 1.0


def test_metric_rejects_mismatched_or_unknown_outcome() -> None:
    """Unexpected response shapes and unknown case contracts must fail closed."""
    evaluate = _evaluate()

    mismatched = evaluate(_instance("Not JSON", "blocked"))
    unknown = evaluate(_instance("Anything", "unknown"))

    assert mismatched["score"] == 0.0
    assert unknown["score"] == 0.0
