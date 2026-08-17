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
    emit_llm_invocation_token_summary,
    emit_llm_token_usage_event,
    emit_operational_event,
    emit_quiz_validation_event,
)
from app.app_utils.token_usage import (
    CallStage,
    InvocationTokenUsage,
    TerminalOutcome,
    TokenUsage,
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


def test_llm_token_usage_event_has_only_allowlisted_metadata(capsys) -> None:
    usage = TokenUsage(
        prompt_token_count=100,
        cached_content_token_count=40,
        candidates_token_count=20,
        thoughts_token_count=30,
        tool_use_prompt_token_count=5,
        total_token_count=155,
    )
    private_values = (
        "PRIVATE-PROMPT-83e1",
        "PRIVATE-QUIZ-f932",
        "203.0.113.9",
        "session-private-71a2",
    )

    with patch(
        "app.app_utils.operational_logging.get_build_info",
        return_value={
            "version": "1.1.0-dev",
            "short_commit_sha": "abc1234",
        },
    ):
        emit_llm_token_usage_event(
            call_stage=CallStage.QUIZ_GENERATOR,
            model="gemini-2.5-flash",
            usage=usage,
            generation_attempt=2,
        )

    output = capsys.readouterr()
    payload = json.loads(output.err)
    assert payload == {
        "schema_version": 1,
        "severity": "INFO",
        "event": "llm_token_usage",
        "phase": "model_usage",
        "call_stage": "quiz_generator",
        "model": "gemini-2.5-flash",
        "generation_attempt": 2,
        "prompt_token_count": 100,
        "uncached_prompt_token_count": 60,
        "cached_content_token_count": 40,
        "candidates_token_count": 20,
        "thoughts_token_count": 30,
        "tool_use_prompt_token_count": 5,
        "total_token_count": 155,
        "service_version": "1.1.0-dev",
        "deployment_revision": "abc1234",
    }
    for private_value in private_values:
        assert private_value not in output.err
    assert output.out == ""


def test_invocation_summary_contains_only_aggregate_usage(capsys) -> None:
    accumulator = InvocationTokenUsage()
    accumulator.add_direct(
        CallStage.SECURITY_CLASSIFIER,
        TokenUsage(prompt_token_count=20, total_token_count=25),
    )
    accumulator.add_direct(
        CallStage.QUIZ_GENERATOR,
        TokenUsage(
            prompt_token_count=100,
            cached_content_token_count=40,
            candidates_token_count=20,
            thoughts_token_count=30,
            total_token_count=150,
        ),
    )

    with patch(
        "app.app_utils.operational_logging.get_build_info",
        return_value={
            "version": "1.1.0-dev",
            "short_commit_sha": "abc1234",
        },
    ):
        emit_llm_invocation_token_summary(
            usage=accumulator,
            terminal_outcome=TerminalOutcome.SUCCESS,
        )

    output = capsys.readouterr()
    payload = json.loads(output.err)
    assert payload["event"] == "llm_invocation_token_summary"
    assert payload["phase"] == "invocation_usage"
    assert payload["terminal_outcome"] == "success"
    assert payload["model_call_count"] == 2
    assert payload["total_token_count"] == 175
    assert payload["cached_content_token_count"] == 40
    assert payload["uncached_prompt_token_count"] == 80
    assert payload["stage_total_token_counts"]["security_classifier"] == 25
    assert payload["stage_total_token_counts"]["quiz_generator"] == 150
    forbidden_fields = {
        "prompt",
        "response",
        "quiz",
        "ip",
        "anonymous_id",
        "browser_id",
        "user_id",
        "session_id",
        "invocation_id",
    }
    assert forbidden_fields.isdisjoint(payload)
    assert output.out == ""
