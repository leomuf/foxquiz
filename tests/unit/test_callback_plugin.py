# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Lifecycle tests for the FoxQuiz ADK security and budget plugin.

Purpose:
    Verify token accumulation and exactly-once flushing, best-effort post-run
    persistence, plugin execution around ADK workflows, and terminal routing
    for expected security blocks.

Regression focus:
    Expected blocks must become structured workflow state rather than runner
    errors, and blocked requests must not reach quiz-processing nodes.

Boundary:
    Firestore and security decisions are mocked. These tests validate callback
    wiring, not semantic classifier quality.
"""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from google.adk.agents.context import Context
from google.adk.apps import App
from google.adk.events.event import Event
from google.adk.runners import InMemoryRunner
from google.adk.workflow import START, Workflow, node
from google.genai import types

from app.app_utils.callbacks import (
    SECURITY_BLOCK_STATE_KEY,
    FoxQuizSecurityPlugin,
    SecurityBlockException,
    after_agent_callback,
    record_token_usage,
    set_invocation_outcome,
)
from app.app_utils.request_context import anonymous_id_ctx
from app.app_utils.token_usage import (
    CallStage,
    InvocationTokenUsage,
    TerminalOutcome,
    TokenUsage,
)
from app.database.firestore_repo import FirestorePersistenceError


def test_record_token_usage_accumulates_direct_genai_responses():
    callback_context = MagicMock()
    callback_context.state = {}
    first_response = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=80,
            candidates_token_count=20,
            thoughts_token_count=20,
            total_token_count=120,
        )
    )
    second_response = SimpleNamespace(
        usage_metadata=SimpleNamespace(total_token_count=30)
    )

    with patch("app.app_utils.callbacks.emit_llm_token_usage_event") as emit_event:
        assert (
            record_token_usage(
                callback_context,
                first_response,
                call_stage=CallStage.QUIZ_GENERATOR,
                generation_attempt=1,
            )
            == 120
        )
        assert (
            record_token_usage(
                callback_context,
                second_response,
                call_stage=CallStage.ACADEMIC_JUDGE,
                judge_attempt=1,
            )
            == 30
        )

    accumulator = InvocationTokenUsage.from_state(
        callback_context.state["temp:foxquiz_token_usage"]
    )
    assert accumulator.totals.total_token_count == 150
    assert accumulator.stage_call_counts == {
        CallStage.QUIZ_GENERATOR: 1,
        CallStage.ACADEMIC_JUDGE: 1,
    }
    assert emit_event.call_count == 2


def test_record_token_usage_rejects_unknown_stage() -> None:
    callback_context = MagicMock()
    callback_context.state = {}
    response = SimpleNamespace(usage_metadata=SimpleNamespace(total_token_count=10))

    with pytest.raises(ValueError):
        record_token_usage(
            callback_context,
            response,
            call_stage="private-user-supplied-stage",
        )

    assert "temp:foxquiz_token_usage" not in callback_context.state


def test_record_token_usage_keeps_accounting_when_logging_fails() -> None:
    callback_context = MagicMock()
    callback_context.state = {}
    response = SimpleNamespace(usage_metadata=SimpleNamespace(total_token_count=10))

    with patch(
        "app.app_utils.callbacks.emit_llm_token_usage_event",
        side_effect=RuntimeError("logging unavailable"),
    ):
        assert (
            record_token_usage(
                callback_context,
                response,
                call_stage=CallStage.SECURITY_CLASSIFIER,
            )
            == 10
        )

    accumulator = InvocationTokenUsage.from_state(
        callback_context.state["temp:foxquiz_token_usage"]
    )
    assert accumulator.totals.total_token_count == 10


def test_record_token_usage_never_serializes_response_content(capsys) -> None:
    private_response = "PRIVATE-QUIZ-CONTENT-27ac"
    callback_context = MagicMock()
    callback_context.state = {}
    response = SimpleNamespace(
        text=private_response,
        usage_metadata=SimpleNamespace(total_token_count=10),
    )

    record_token_usage(
        callback_context,
        response,
        call_stage=CallStage.CURRICULUM_EVALUATOR,
    )

    output = capsys.readouterr()
    assert private_response not in output.err
    assert output.out == ""


@pytest.mark.asyncio
async def test_after_callback_flushes_direct_usage_exactly_once():
    callback_context = MagicMock()
    accumulator = InvocationTokenUsage()
    accumulator.add_direct(
        CallStage.QUIZ_GENERATOR,
        TokenUsage(total_token_count=150),
    )
    callback_context.state = {
        "temp:foxquiz_token_usage": accumulator.as_state(),
        "temp:foxquiz_token_usage_flushed": False,
    }
    set_invocation_outcome(callback_context, TerminalOutcome.SUCCESS)
    callback_context.invocation_id = "invocation-1"
    callback_context.session.events = []
    token = anonymous_id_ctx.set("test-anonymous-user")

    try:
        with (
            patch("app.app_utils.callbacks.FirestoreRepository") as repo_class,
            patch(
                "app.app_utils.callbacks.emit_llm_invocation_token_summary"
            ) as emit_summary,
        ):
            repo = repo_class.return_value

            await after_agent_callback(callback_context)
            await after_agent_callback(callback_context)

            assert repo.increment_token_budget.call_args_list == [
                call("budget_test-anonymous-user", 150),
                call("global", 150),
            ]
            emit_summary.assert_called_once()
            assert (
                emit_summary.call_args.kwargs["terminal_outcome"]
                is TerminalOutcome.SUCCESS
            )
    finally:
        anonymous_id_ctx.reset(token)


@pytest.mark.asyncio
async def test_after_callback_keeps_successful_quiz_when_budget_flush_fails():
    """Post-run token persistence is observable but never replaces a valid quiz."""
    callback_context = MagicMock()
    callback_context.state = {
        "temp:foxquiz_token_usage": 150,
        "temp:foxquiz_token_usage_flushed": False,
    }
    callback_context.invocation_id = "invocation-1"
    callback_context.session.events = []

    with patch("app.app_utils.callbacks.FirestoreRepository") as repo_class:
        repo_class.return_value.increment_token_budget.side_effect = (
            FirestorePersistenceError("increment_token_budget", "token_usage_flush")
        )

        assert await after_agent_callback(callback_context) is None

    assert (
        InvocationTokenUsage.from_state(
            callback_context.state["temp:foxquiz_token_usage"]
        ).model_call_count
        == 0
    )
    assert callback_context.state["temp:foxquiz_token_usage_flushed"] is True


@pytest.mark.asyncio
async def test_after_callback_combines_disjoint_direct_and_adk_usage() -> None:
    callback_context = MagicMock()
    accumulator = InvocationTokenUsage()
    accumulator.add_direct(
        CallStage.SECURITY_CLASSIFIER,
        TokenUsage(total_token_count=10),
    )
    callback_context.state = {
        "temp:foxquiz_token_usage": accumulator.as_state(),
        "temp:foxquiz_token_usage_flushed": False,
    }
    set_invocation_outcome(callback_context, TerminalOutcome.NEEDS_INPUT)
    callback_context.invocation_id = "invocation-1"
    callback_context.session.events = [
        SimpleNamespace(
            invocation_id="invocation-1",
            usage_metadata=SimpleNamespace(total_token_count=5),
        ),
        SimpleNamespace(
            invocation_id="another-invocation",
            usage_metadata=SimpleNamespace(total_token_count=999),
        ),
    ]

    with (
        patch("app.app_utils.callbacks.FirestoreRepository") as repo_class,
        patch(
            "app.app_utils.callbacks.emit_llm_invocation_token_summary"
        ) as emit_summary,
    ):
        await after_agent_callback(callback_context)

    assert repo_class.return_value.increment_token_budget.call_args_list == [
        call("budget_anon-default", 15),
        call("global", 15),
    ]
    summary = emit_summary.call_args.kwargs["usage"]
    assert summary.totals.total_token_count == 15
    assert summary.model_call_count == 2
    assert summary.adk_managed_model_call_count == 1
    assert (
        emit_summary.call_args.kwargs["terminal_outcome"] is TerminalOutcome.NEEDS_INPUT
    )


@pytest.mark.asyncio
async def test_summary_logging_failure_does_not_change_budget_flush() -> None:
    callback_context = MagicMock()
    accumulator = InvocationTokenUsage()
    accumulator.add_direct(
        CallStage.QUIZ_GENERATOR,
        TokenUsage(total_token_count=42),
    )
    callback_context.state = {
        "temp:foxquiz_token_usage": accumulator.as_state(),
        "temp:foxquiz_token_usage_flushed": False,
        "temp:foxquiz_terminal_outcome": "success",
    }
    callback_context.invocation_id = "invocation-1"
    callback_context.session.events = []

    with (
        patch("app.app_utils.callbacks.FirestoreRepository") as repo_class,
        patch(
            "app.app_utils.callbacks.emit_llm_invocation_token_summary",
            side_effect=RuntimeError("logging unavailable"),
        ),
    ):
        assert await after_agent_callback(callback_context) is None

    assert repo_class.return_value.increment_token_budget.call_args_list == [
        call("budget_anon-default", 42),
        call("global", 42),
    ]
    assert callback_context.state["temp:foxquiz_token_usage_flushed"] is True


@pytest.mark.asyncio
async def test_error_callback_flushes_usage_with_error_outcome() -> None:
    plugin = FoxQuizSecurityPlugin()
    callback_context = MagicMock()
    callback_context.state = {}
    invocation_context = MagicMock()

    with (
        patch("app.app_utils.callbacks.Context", return_value=callback_context),
        patch(
            "app.app_utils.callbacks.after_agent_callback",
            new_callable=AsyncMock,
        ) as after_callback,
    ):
        await plugin.on_run_error_callback(
            invocation_context=invocation_context,
            error=RuntimeError("private failure details"),
        )

    assert callback_context.state["temp:foxquiz_terminal_outcome"] == "error"
    after_callback.assert_awaited_once_with(callback_context)


@pytest.mark.asyncio
async def test_app_plugin_wraps_workflow_execution():
    executed: list[bool] = []

    @node
    async def finish(ctx: Context, node_input: Any) -> Event:
        executed.append(True)
        return Event(output={"status": "done"})

    workflow = Workflow(name="test_workflow", edges=[(START, finish)])
    app = App(
        name="test_app",
        root_agent=workflow,
        plugins=[FoxQuizSecurityPlugin()],
    )
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name=app.name,
        user_id="test-user",
    )

    with (
        patch(
            "app.app_utils.callbacks.before_agent_callback",
            new_callable=AsyncMock,
        ) as before_callback,
        patch(
            "app.app_utils.callbacks.after_agent_callback",
            new_callable=AsyncMock,
        ) as after_callback,
    ):
        events = [
            event
            async for event in runner.run_async(
                user_id="test-user",
                session_id=session.id,
                new_message=types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="Start")],
                ),
            )
        ]

    assert events
    assert executed == [True]
    before_callback.assert_awaited_once()
    after_callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_app_plugin_records_structured_block_without_runner_error():
    """Expected security blocks become workflow state instead of runner errors."""
    executed: list[bool] = []

    @node
    async def finish(ctx: Context, node_input: Any) -> Event:
        executed.append(True)
        return Event(output={"status": "done"})

    workflow = Workflow(name="blocked_workflow", edges=[(START, finish)])
    app = App(
        name="blocked_app",
        root_agent=workflow,
        plugins=[FoxQuizSecurityPlugin()],
    )
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name=app.name,
        user_id="test-user",
    )

    with (
        patch(
            "app.app_utils.callbacks.before_agent_callback",
            new_callable=AsyncMock,
            side_effect=SecurityBlockException("Do not share personal data.", "PII"),
        ),
        patch(
            "app.app_utils.callbacks.after_agent_callback",
            new_callable=AsyncMock,
        ) as after_callback,
    ):
        events = [
            event
            async for event in runner.run_async(
                user_id="test-user",
                session_id=session.id,
                new_message=types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="private input")],
                ),
            )
        ]

    assert executed == [True]
    assert events
    after_callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_foxquiz_workflow_routes_security_block_to_terminal_response():
    """The real first workflow node must prevent all quiz processing on blocks."""
    from app.agent import app as foxquiz_app

    runner = InMemoryRunner(app=foxquiz_app)
    session = await runner.session_service.create_session(
        app_name=foxquiz_app.name,
        user_id="test-user",
    )

    with (
        patch(
            "app.app_utils.callbacks.before_agent_callback",
            new_callable=AsyncMock,
            side_effect=SecurityBlockException("Do not share personal data.", "PII"),
        ),
        patch("app.agent.Client") as client_class,
    ):
        events = [
            event
            async for event in runner.run_async(
                user_id="test-user",
                session_id=session.id,
                new_message=types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="private input")],
                ),
            )
        ]

    content_events = [
        event
        for event in events
        if event.content is not None
        and event.content.parts
        and any(part.text for part in event.content.parts)
    ]
    assert len(content_events) == 1
    block_event = json.loads(content_events[0].content.parts[0].text)
    assert block_event == {
        "status": "blocked",
        "block_type": "PII",
        "message": "Do not share personal data.",
    }
    assert SECURITY_BLOCK_STATE_KEY not in session.state
    assert not any(event.output for event in events)
    client_class.assert_not_called()
