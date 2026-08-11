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

# ==============================================================================
# Modified and extended by Leonardo Muffato (AUTOSOFT Engineering - www.autosoft-engineering.de) 2026.
# Copyright (c) 2026 Leonardo Muffato (AUTOSOFT Engineering - www.autosoft-engineering.de).
# All custom integration tests, upfront validation checks, and mocking structures
# are licensed under CC BY 4.0. See global LICENSE file for details.
# ==============================================================================

from unittest.mock import MagicMock, patch

import pytest
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import root_agent

pytestmark = pytest.mark.google_cloud


@pytest.fixture(autouse=True)
def mock_wikipedia_requests():
    """Autouse fixture to mock external Wikipedia HTTP requests to prevent hangs/sandboxing issues."""
    with patch("requests.get") as mock_get:
        # Mock responses for both search and extract APIs
        def side_effect(url, params=None, **kwargs):
            resp = MagicMock()
            if params and params.get("list") == "search":
                resp.json.return_value = {
                    "query": {"search": [{"pageid": 123, "title": "Mock Title"}]}
                }
            else:
                resp.json.return_value = {
                    "query": {
                        "pages": [
                            {
                                "pageid": 123,
                                "title": "Mock Title",
                                "extract": "This is a mock Wikipedia extract for testing.",
                            }
                        ]
                    }
                }
            resp.status_code = 200
            return resp

        mock_get.side_effect = side_effect
        yield mock_get


def test_agent_stream() -> None:
    """
    Integration test for the agent stream functionality.
    Tests that the agent returns valid streaming responses.
    """

    session_service = InMemorySessionService()

    session = session_service.create_session_sync(user_id="test_user", app_name="test")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="test")

    message = types.Content(
        role="user", parts=[types.Part.from_text(text="Why is the sky blue?")]
    )

    events = list(
        runner.run(
            new_message=message,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )
    assert len(events) > 0, "Expected at least one message"

    has_text_content = False
    for event in events:
        if (
            event.content
            and event.content.parts
            and any(part.text for part in event.content.parts)
        ):
            has_text_content = True
            break
    assert has_text_content, "Expected at least one message with text content"


def test_adaptive_quiz_generation() -> None:
    """
    Integration test for the adaptive quiz generation logic.
    Verifies reinforcement mode (score <= 4) generates '🌱 Easy' difficulty.
    """
    import json

    session_service = InMemorySessionService()
    session = session_service.create_session_sync(user_id="test_user", app_name="test")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="test")

    mock_previous_quiz = {
        "title": "Old fractions quiz",
        "questions": [
            {
                "question": "What is 1/2 of 10?",
                "options": ["5", "3", "2"],
                "correct_option_index": 0,
                "explanation": "1/2 of 10 is 5.",
            }
        ]
        * 10,
    }

    # 1. Test Reinforcement Mode (score <= 4)
    payload_reinforce = {
        "grade": "Grade 5",
        "subject": "Math",
        "topic": "Fractions",
        "preferred_language": "en",
        "previous_score": 3,
        "previous_questions": ["What is 1/2 of 10?"],
        "previous_quiz_json": json.dumps(mock_previous_quiz),
    }

    message = types.Content(
        role="user", parts=[types.Part.from_text(text=json.dumps(payload_reinforce))]
    )

    events = list(
        runner.run(
            new_message=message,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )

    quiz_outputs = [
        event.output
        for event in events
        if event.output
        and isinstance(event.output, dict)
        and "questions" in event.output
    ]

    assert len(quiz_outputs) == 1, (
        "Only the validated terminal node may expose quiz JSON"
    )
    quiz_output = quiz_outputs[0]
    assert quiz_output.get("difficulty") == "🌱 Easy", (
        f"Expected '🌱 Easy', got {quiz_output.get('difficulty')}"
    )


def test_upfront_curriculum_validation_mismatch() -> None:
    """
    Integration test for the upfront curriculum validation check.
    Verifies that choosing an incompatible topic (e.g., Differential Equations for Grade 5)
    is caught upfront, clears the topic, and triggers a friendly mascot explanation.
    """
    import json

    session_service = InMemorySessionService()
    session = session_service.create_session_sync(user_id="test_user", app_name="test")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="test")

    payload_mismatch = {
        "grade": "Grade 5",
        "subject": "Math",
        "topic": "Differential Equations",
        "preferred_language": "en",
    }

    message = types.Content(
        role="user", parts=[types.Part.from_text(text=json.dumps(payload_mismatch))]
    )

    events = list(
        runner.run(
            new_message=message,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )

    # Verify that the route was ask_more (which returns content, but no output quiz JSON)
    has_mascot_response = False
    has_quiz_output = False

    for ev in events:
        if ev.content and ev.content.parts:
            for part in ev.content.parts:
                if part.text:
                    has_mascot_response = True
        if ev.output:
            has_quiz_output = True

    assert has_mascot_response, (
        "Expected mascot response explaining the incompatibility"
    )
    assert not has_quiz_output, "Should not generate a quiz for incompatible inputs"

    # Retrieve final session state and verify that topic was cleared
    final_session = session_service.get_session_sync(
        user_id="test_user", session_id=session.id, app_name="test"
    )
    assert final_session.state.get("topic") is None, (
        "Incompatible topic should be cleared from state"
    )


def test_upfront_curriculum_validation_clarifies_broad_advanced_topic() -> None:
    """Avoid generating before an ambiguous topic has a grade-appropriate scope."""
    import json

    session_service = InMemorySessionService()
    session = session_service.create_session_sync(user_id="test_user", app_name="test")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="test")

    payload = {
        "grade": "Grade 12",
        "subject": "Math",
        "topic": "Multiplication",
        "preferred_language": "en",
    }
    message = types.Content(
        role="user", parts=[types.Part.from_text(text=json.dumps(payload))]
    )

    events = list(
        runner.run(
            new_message=message,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )

    has_clarification = any(
        event.content
        and event.content.parts
        and any(part.text for part in event.content.parts)
        for event in events
    )
    has_quiz_output = any(
        event.output and isinstance(event.output, dict) and "questions" in event.output
        for event in events
    )
    final_session = session_service.get_session_sync(
        user_id="test_user", session_id=session.id, app_name="test"
    )

    assert has_clarification
    assert not has_quiz_output
    assert final_session.state.get("curriculum_status") == "needs_clarification"
    assert final_session.state.get("topic") is None
