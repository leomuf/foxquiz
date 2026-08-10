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

import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
import requests
from requests.exceptions import RequestException

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Skip integration tests in CI/GitHub Actions when no Google Cloud / Gemini credentials are present
skip_integration = os.environ.get("GITHUB_ACTIONS") == "true" and not any(
    os.environ.get(var)
    for var in [
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ]
)

pytestmark = pytest.mark.skipif(
    skip_integration,
    reason="Skipping integration tests in GitHub Actions because Google Cloud/Gemini credentials are not configured.",
)

BASE_URL = "http://127.0.0.1:8000"
STREAM_URL = BASE_URL + "/run_sse"
FEEDBACK_URL = BASE_URL + "/feedback"

HEADERS = {"Content-Type": "application/json"}


def log_output(pipe: Any, log_func: Any) -> None:
    """Log the output from the given pipe."""
    for line in iter(pipe.readline, ""):
        log_func(line.strip())


def start_server() -> subprocess.Popen[str]:
    """Start the FastAPI server using subprocess and log its output."""
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.fast_api_app:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    env = os.environ.copy()
    env["INTEGRATION_TEST"] = "TRUE"
    env["AGENT_VERSION"] = "integration-test"
    env["COMMIT_SHA"] = "0123456789abcdef0123456789abcdef01234567"
    env["BUILD_TIME"] = "2026-08-10T12:00:00Z"
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )

    # Start threads to log stdout and stderr in real-time
    threading.Thread(
        target=log_output, args=(process.stdout, logger.info), daemon=True
    ).start()
    threading.Thread(
        target=log_output, args=(process.stderr, logger.error), daemon=True
    ).start()

    return process


def wait_for_server(timeout: int = 90, interval: int = 1) -> bool:
    """Wait for the server to be ready."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get("http://127.0.0.1:8000/docs", timeout=10)
            if response.status_code == 200:
                logger.info("Server is ready")
                return True
        except RequestException:
            pass
        time.sleep(interval)
    logger.error(f"Server did not become ready within {timeout} seconds")
    return False


@pytest.fixture(scope="session")
def server_fixture(request: Any) -> Iterator[subprocess.Popen[str]]:
    """Pytest fixture to start and stop the server for testing."""
    logger.info("Starting server process")
    server_process = start_server()
    if not wait_for_server():
        pytest.fail("Server failed to start")
    logger.info("Server process started")

    def stop_server() -> None:
        logger.info("Stopping server process")
        server_process.terminate()
        server_process.wait()
        logger.info("Server process stopped")

    request.addfinalizer(stop_server)
    yield server_process


def test_chat_stream(server_fixture: subprocess.Popen[str]) -> None:
    """Test the chat stream functionality."""
    logger.info("Starting chat stream test")
    # Create session first
    user_id = "test_user_123"
    session_data = {"state": {"preferred_language": "English", "visit_count": 1}}

    session_url = f"{BASE_URL}/apps/app/users/{user_id}/sessions"
    session_response = requests.post(
        session_url,
        headers=HEADERS,
        json=session_data,
        timeout=60,
    )
    assert session_response.status_code == 200
    logger.info(f"Session creation response: {session_response.json()}")
    session_id = session_response.json()["id"]

    # Then send chat message
    data = {
        "app_name": "app",
        "user_id": user_id,
        "session_id": session_id,
        "new_message": {
            "role": "user",
            "parts": [{"text": "Create a short biology quiz about cells."}],
        },
        "streaming": True,
    }
    response = requests.post(
        STREAM_URL, headers=HEADERS, json=data, stream=True, timeout=60
    )
    assert response.status_code == 200

    # Parse SSE events from response
    events = []
    for line in response.iter_lines():
        if line:
            # SSE format is "data: {json}"
            line_str = line.decode("utf-8")
            if line_str.startswith("data: "):
                event_json = line_str[6:]  # Remove "data: " prefix
                event = json.loads(event_json)
                events.append(event)

    assert events, "No events received from stream"
    # Check for valid content in the response
    has_text_content = False
    for event in events:
        content = event.get("content")
        if (
            content is not None
            and content.get("parts")
            and any(part.get("text") for part in content["parts"])
        ):
            has_text_content = True
            break

    assert has_text_content, "Expected at least one event with text content"


def test_chat_stream_error_handling(server_fixture: subprocess.Popen[str]) -> None:
    """Test the chat stream error handling."""
    logger.info("Starting chat stream error handling test")
    data = {
        "input": {"messages": [{"type": "invalid_type", "content": "Cause an error"}]}
    }
    response = requests.post(
        STREAM_URL, headers=HEADERS, json=data, stream=True, timeout=10
    )

    assert response.status_code == 422, (
        f"Expected status code 422, got {response.status_code}"
    )
    logger.info("Error handling test completed successfully")


def test_public_static_assets(server_fixture: subprocess.Popen[str]) -> None:
    """Verify social-preview and favicon assets are packaged and publicly served."""
    social_preview = requests.get(
        BASE_URL + "/static/assets/foxquiz_github_social_preview.jpg", timeout=10
    )
    assert social_preview.status_code == 200
    assert social_preview.headers["content-type"].startswith("image/jpeg")

    favicon_response = requests.get(BASE_URL + "/favicon.ico", timeout=10)
    assert favicon_response.status_code == 200
    assert favicon_response.headers["content-type"].startswith("image/svg+xml")


def test_deployed_version_metadata(server_fixture: subprocess.Popen[str]) -> None:
    """Verify the API, footer, and response header identify the deployed commit."""
    response = requests.get(BASE_URL + "/version", timeout=10)
    assert response.status_code == 200
    assert response.json() == {
        "version": "integration-test",
        "commit_sha": "0123456789abcdef0123456789abcdef01234567",
        "short_commit_sha": "0123456",
        "commit_url": (
            "https://github.com/leomuf/foxquiz/commit/"
            "0123456789abcdef0123456789abcdef01234567"
        ),
        "build_time": "2026-08-10T12:00:00Z",
    }

    root_response = requests.get(BASE_URL, timeout=10)
    assert root_response.status_code == 200
    assert "FoxQuiz vintegration-test" in root_response.text
    assert ">0123456</a>" in root_response.text
    assert root_response.headers["X-FoxQuiz-Version"] == ("integration-test (0123456)")


def test_collect_feedback(server_fixture: subprocess.Popen[str]) -> None:
    """
    Test the feedback collection endpoint (/feedback) to ensure it properly
    logs the received feedback.
    """
    # Create sample feedback data
    feedback_data = {
        "score": 4,
        "user_id": "test-user-456",
        "session_id": "test-session-456",
        "text": "Great response!",
    }

    response = requests.post(
        FEEDBACK_URL, json=feedback_data, headers=HEADERS, timeout=10
    )
    assert response.status_code == 200
