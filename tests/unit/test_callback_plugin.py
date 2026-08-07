# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
    FoxQuizSecurityPlugin,
    after_agent_callback,
    record_token_usage,
)
from app.app_utils.request_context import anonymous_id_ctx


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
