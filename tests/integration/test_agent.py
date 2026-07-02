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

from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import root_agent


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
                "explanation": "1/2 of 10 is 5."
            }
        ] * 10
    }

    # 1. Test Reinforcement Mode (score <= 4)
    payload_reinforce = {
        "grade": "Grade 5",
        "subject": "Math",
        "topic": "Fractions",
        "preferred_language": "en",
        "previous_score": 3,
        "previous_questions": ["What is 1/2 of 10?"],
        "previous_quiz_json": json.dumps(mock_previous_quiz)
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

    quiz_output = None
    for ev in events:
        if ev.output and "questions" in ev.output:
            quiz_output = ev.output
            break

    assert quiz_output is not None, "Expected structured quiz output"
    assert quiz_output.get("difficulty") == "🌱 Easy", f"Expected '🌱 Easy', got {quiz_output.get('difficulty')}"

