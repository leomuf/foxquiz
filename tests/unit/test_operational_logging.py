# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Privacy-contract tests for structured operational events.

Purpose:
    Ensure Firestore failures and deterministic quiz-validation outcomes emit
    queryable JSON containing only their explicitly approved diagnostic fields.

Privacy boundary:
    Exception messages, prompts, generated quiz titles, questions, options,
    explanations, IP addresses, client identifiers, hashed signatures, and
    private security rules must never appear. Cloud Logging ingestion and
    metrics are infrastructure concerns outside this unit test.
"""

import json
from unittest.mock import patch

from app.app_utils.operational_logging import (
    emit_operational_event,
    emit_quiz_validation_event,
)
from app.domain.quiz_validation import validate_quiz_candidate


def test_operational_event_is_structured_and_privacy_safe(capsys) -> None:
    """Operational failures expose diagnostics without user or rule content."""
    secret = "prompt=Meu CPF is 123; ip=203.0.113.7; rule=private-pattern"
    error = RuntimeError(secret)
    error.code = secret

    with patch(
        "app.app_utils.operational_logging.get_build_info",
        return_value={
            "version": "1.1.0",
            "short_commit_sha": "abc1234",
        },
    ):
        emit_operational_event(
            event="firestore_operation_failed",
            phase="ban_check",
            operation="check_banned_signature",
            error=error,
        )

    output = capsys.readouterr()
    payload = json.loads(output.err)
    assert payload == {
        "deployment_revision": "abc1234",
        "error_type": "RuntimeError",
        "event": "firestore_operation_failed",
        "operation": "check_banned_signature",
        "phase": "ban_check",
        "service_version": "1.1.0",
        "severity": "ERROR",
    }
    assert secret not in output.err
    assert output.out == ""


def test_quiz_validation_event_never_logs_generated_quiz_text(capsys) -> None:
    """Validation telemetry exposes aggregate codes, never candidate content."""
    private_title = "PRIVATE-TITLE-7e3e"
    private_question = "PRIVATE-QUESTION-3a91"
    private_option = "PRIVATE-OPTION-d84f ✅"
    private_explanation = "PRIVATE-EXPLANATION-b205"
    candidate = {
        "title": private_title,
        "questions": [
            {
                "question": f"{private_question}-{number}",
                "options": [private_option, "Neutral A", "Neutral B"],
                "correct_option_index": 0,
                "explanation": private_explanation,
            }
            for number in range(10)
        ],
    }
    result = validate_quiz_candidate(candidate)

    with patch(
        "app.app_utils.operational_logging.get_build_info",
        return_value={
            "version": "1.1.0",
            "short_commit_sha": "abc1234",
        },
    ):
        emit_quiz_validation_event(
            event="quiz_validation_failed",
            generation_attempt=1,
            result=result,
        )

    output = capsys.readouterr()
    payload = json.loads(output.err)
    assert payload == {
        "deployment_revision": "abc1234",
        "event": "quiz_validation_failed",
        "generation_attempt": 1,
        "issue_codes": ["answer_cue_in_option"],
        "issue_count": 10,
        "phase": "deterministic_quiz_validation",
        "service_version": "1.1.0",
        "severity": "WARNING",
    }
    for private_value in (
        private_title,
        private_question,
        private_option,
        private_explanation,
    ):
        assert private_value not in output.err
    assert output.out == ""
