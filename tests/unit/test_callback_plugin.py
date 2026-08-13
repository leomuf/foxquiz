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
)
from app.app_utils.request_context import anonymous_id_ctx
from app.database.firestore_repo import FirestorePersistenceError


def test_record_token_usage_accumulates_direct_genai_responses():
    callback_context = MagicMock()
    callback_context.state = {}
    first_response = MagicMock()
    first_response.usage_metadata.total_token_count = 120
    second_response = MagicMock()
    second_response.usage_metadata.total_token_count = 30

    assert record_token_usage(callback_context, first_response) == 120
    assert record_token_usage(callback_context, second_response) == 30
    assert callback_context.state["temp:foxquiz_token_usage"] == 150


@pytest.mark.asyncio
async def test_after_callback_flushes_direct_usage_exactly_once():
    callback_context = MagicMock()
    callback_context.state = {
        "temp:foxquiz_token_usage": 150,
        "temp:foxquiz_token_usage_flushed": False,
    }
    callback_context.invocation_id = "invocation-1"
    callback_context.session.events = []
    token = anonymous_id_ctx.set("test-anonymous-user")

    try:
        with patch("app.app_utils.callbacks.FirestoreRepository") as repo_class:
            repo = repo_class.return_value

            await after_agent_callback(callback_context)
            await after_agent_callback(callback_context)

            assert repo.increment_token_budget.call_args_list == [
                call("budget_test-anonymous-user", 150),
                call("global", 150),
            ]
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

    assert callback_context.state["temp:foxquiz_token_usage"] == 0
    assert callback_context.state["temp:foxquiz_token_usage_flushed"] is True


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

    with patch(
        "app.app_utils.callbacks.before_agent_callback",
        new_callable=AsyncMock,
        side_effect=SecurityBlockException("Do not share personal data.", "PII"),
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

    content_events = [event for event in events if event.content is not None]
    assert len(content_events) == 1
    block_event = json.loads(content_events[0].content.parts[0].text)
    assert block_event == {
        "status": "blocked",
        "block_type": "PII",
        "message": "Do not share personal data.",
    }
    assert SECURITY_BLOCK_STATE_KEY not in session.state
    assert not any(event.output for event in events)
