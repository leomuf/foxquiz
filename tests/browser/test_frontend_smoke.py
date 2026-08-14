"""Browser-level tests for the main FoxQuiz user journeys.

Purpose:
    Protect the learner-visible workflow: changing language and mascot,
    displaying accurate license information, submitting grade/subject/topic,
    completing a quiz, sending negative feedback, and receiving a
    security-block response.

Boundary:
    A real browser drives the local FastAPI app, while session, Server-Sent
    Events (SSE), and persistence responses are mocked. These tests need no
    Google credentials. Visual appearance and non-deterministic LLM quality
    remain manual or evaluation concerns.
"""

import json
import os
import re
import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest
import requests
from playwright.sync_api import Page, expect
from requests.exceptions import RequestException


@pytest.fixture(scope="session")
def frontend_base_url() -> Iterator[str]:
    """Run the app in its credential-free integration mode on a free port."""
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]

    env = os.environ.copy()
    env.update(
        {
            "INTEGRATION_TEST": "TRUE",
            "AGENT_VERSION": "browser-test",
            "COMMIT_SHA": "0123456789abcdef0123456789abcdef01234567",
            "BUILD_TIME": "2026-08-10T12:00:00Z",
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.fast_api_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if requests.get(f"{base_url}/version", timeout=1).status_code == 200:
                break
        except RequestException:
            time.sleep(0.1)
    else:
        process.terminate()
        process.wait(timeout=10)
        pytest.fail("Browser-test server did not become ready within 30 seconds")

    try:
        yield base_url
    finally:
        process.terminate()
        process.wait(timeout=10)


def _quiz_fixture(
    *, title: str = "Cells", difficulty: str = "Medium", subject: str = "Biology"
) -> dict:
    return {
        "title": title,
        "difficulty": difficulty,
        "questions": [
            {
                "question": f"{subject} question {index}? \U0001f4a1",
                "options": [
                    "Correct <strong>answer</strong>",
                    "Incorrect A",
                    "Incorrect B",
                    "Incorrect C",
                ],
                "correct_option_index": 0,
                "explanation": f"Explanation {index}.",
            }
            for index in range(1, 11)
        ],
    }


def _mock_quiz_generation(page: Page, captured_requests: list[dict]) -> None:
    def fulfill_session(route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"id": "browser-session"}),
        )

    def fulfill_stream(route) -> None:
        captured_requests.append(route.request.post_data_json)
        route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=f"data: {json.dumps({'output': _quiz_fixture()})}\n\n",
        )

    page.route("**/apps/app/users/anonymous_student/sessions", fulfill_session)
    page.route("**/run_sse", fulfill_stream)


def test_language_switching_and_mascot_selection(
    page: Page, frontend_base_url: str
) -> None:
    """Verify translation coverage and visible language and mascot updates."""
    page.goto(f"{frontend_base_url}/?lang=en")

    missing_translations = page.evaluate(
        """() => {
            const languages = ["de", "en", "pt"];
            const keys = Array.from(document.querySelectorAll("[data-translate]"))
                .map((element) => element.dataset.translate);
            return languages.flatMap((language) =>
                keys
                    .filter((key) => !translations[language][key])
                    .map((key) => language + ":" + key)
            );
        }"""
    )
    assert missing_translations == []

    expect(page.locator('[data-translate="choose_buddy"]')).to_have_text(
        "Choose your FoxQuiz Companion!"
    )
    expect(page.locator('label[for="input-subject"]')).to_have_text("School Subject")
    expect(page.locator("#input-topic")).to_have_attribute(
        "placeholder", "e.g., Photosynthesis"
    )
    english_license = page.locator('[data-translate="footer_opensource"]')
    expect(english_license).to_contain_text("Apache License 2.0")
    expect(english_license).to_contain_text("CC BY 4.0")
    expect(english_license).to_contain_text("CC0 1.0")

    page.get_by_role("button", name="PT").click()
    expect(page.locator('label[for="input-grade"]')).to_have_text("Ano Escolar")
    expect(page.locator('[data-translate="btn_start"]')).to_have_text("Criar Quiz!")
    portuguese_license = page.locator('[data-translate="footer_opensource"]')
    expect(portuguese_license).to_contain_text("Apache License 2.0")
    expect(portuguese_license).to_contain_text("CC BY 4.0")
    expect(portuguese_license).to_contain_text("CC0 1.0")

    page.locator('.mascot-option[data-mascot="owl"]').click()
    expect(page.locator('.mascot-option[data-mascot="owl"]')).to_have_class(
        re.compile(r"\bactive\b")
    )
    expect(page.locator("#welcome-bubble")).to_contain_text("Olivia")


def test_complete_quiz_and_negative_feedback_flow(
    page: Page, frontend_base_url: str
) -> None:
    """Exercise the complete quiz and feedback journey with mocked APIs."""
    generated_requests: list[dict] = []
    feedback_requests: list[dict] = []
    _mock_quiz_generation(page, generated_requests)

    def fulfill_feedback(route) -> None:
        feedback_requests.append(route.request.post_data_json)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"status": "success", "log_id": "feedback-1"}),
        )

    page.route("**/feedback", fulfill_feedback)
    page.goto(f"{frontend_base_url}/?lang=en")
    page.locator("#input-grade").select_option("Klasse 6")
    page.locator("#input-subject").fill("Biology")
    page.locator("#input-topic").fill("Cells")
    page.locator("#start-btn").click()

    expect(page.locator("#quiz-screen")).to_be_visible()
    expect(page.locator("#quiz-progress-counter")).to_have_text("1 / 10")
    assert len(generated_requests) == 1
    prompt = json.loads(generated_requests[0]["new_message"]["parts"][0]["text"])
    assert prompt == {
        "grade": "Klasse 6",
        "subject": "Biology",
        "topic": "Cells",
        "preferred_language": "en",
    }

    for question_number in range(1, 11):
        expect(page.locator("#question-text")).to_have_text(
            f"Biology question {question_number}? \U0001f4a1"
        )
        indicators = page.locator(".option-indicator")
        expect(indicators).to_have_count(4)
        assert all(text == "" for text in indicators.all_inner_texts())
        expect(page.locator(".option-btn").first).to_contain_text(
            "Correct <strong>answer</strong>"
        )
        expect(page.locator(".option-btn strong")).to_have_count(0)
        page.locator(".option-btn").first.click()
        expect(page.locator("#explanation-card")).to_be_visible()
        page.locator("#next-btn").click()

    expect(page.locator("#summary-screen")).to_be_visible()
    expect(page.locator("#score-text")).to_have_text("10 / 10")
    expect(page.locator('[data-translate="btn_share"]')).to_have_text("Share Quiz")

    page.locator("#fb-down").click()
    expect(page.locator("#toast-text")).to_contain_text(
        "we will check the quiz and try to improve FoxQuiz"
    )
    assert len(feedback_requests) == 1
    assert feedback_requests[0]["score"] == 0
    assert feedback_requests[0]["quiz_context"] == {
        "grade": "Klasse 6",
        "subject": "Biology",
        "topic": "Cells",
        "preferred_language": "en",
    }
    assert feedback_requests[0]["quiz_data"]["title"] == "Cells"


def test_adaptive_quiz_sharing_freezes_the_new_quiz_snapshot(
    page: Page, frontend_base_url: str
) -> None:
    """Sharing a follow-up quiz must not reuse the first quiz's frozen link."""
    generated_requests: list[dict] = []
    shared_requests: list[dict] = []
    quizzes = [
        _quiz_fixture(title="Multiplication Medium", difficulty="⭐ Medium"),
        _quiz_fixture(
            title="Multiplication Hard",
            difficulty="🚀 Hard",
            subject="Mathematics",
        ),
    ]

    page.route(
        "**/apps/app/users/anonymous_student/sessions",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"id": "browser-session"}),
        ),
    )

    def fulfill_stream(route) -> None:
        generated_requests.append(route.request.post_data_json)
        quiz = quizzes[len(generated_requests) - 1]
        route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=f"data: {json.dumps({'output': quiz})}\n\n",
        )

    def fulfill_share(route) -> None:
        shared_requests.append(route.request.post_data_json)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {"status": "success", "quiz_id": f"quiz-{len(shared_requests)}"}
            ),
        )

    page.route("**/run_sse", fulfill_stream)
    page.route("**/share", fulfill_share)
    page.goto(f"{frontend_base_url}/?lang=en")
    page.locator("#input-grade").select_option("Klasse 5")
    page.locator("#input-subject").fill("Mathematics")
    page.locator("#input-topic").fill("Multiplication")
    page.locator("#start-btn").click()

    for _ in range(10):
        page.locator(".option-btn").first.click()
        page.locator("#next-btn").click()

    page.locator('button[onclick="shareCurrentQuiz()"]').click()
    expect(page.locator("#summary-screen")).to_be_visible()
    assert len(shared_requests) == 1
    assert shared_requests[0]["quiz_data"]["difficulty"] == "⭐ Medium"

    page.locator("#btn-more-questions").click()
    expect(page.locator("#choice-modal")).to_be_visible()
    page.locator(".choice-btn-hard").click()
    expect(page.locator("#quiz-screen")).to_be_visible()

    for _ in range(10):
        page.locator(".option-btn").first.click()
        page.locator("#next-btn").click()

    page.locator('button[onclick="shareCurrentQuiz()"]').click()
    assert len(shared_requests) == 2
    assert shared_requests[1]["quiz_data"]["title"] == "Multiplication Hard"
    assert shared_requests[1]["quiz_data"]["difficulty"] == "🚀 Hard"


def test_blocked_generation_never_displays_a_quiz(
    page: Page, frontend_base_url: str
) -> None:
    """Ensure a safety-blocked response displays no quiz to the user."""
    page.route(
        "**/apps/app/users/anonymous_student/sessions",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"id": "browser-session"}),
        ),
    )
    page.route(
        "**/run_sse",
        lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=(
                "data: "
                + json.dumps(
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "status": "blocked",
                                            "block_type": "PII",
                                            "message": "Please do not share personal data.",
                                        }
                                    )
                                }
                            ],
                        }
                    }
                )
                + "\n\n"
            ),
        ),
    )

    page.goto(f"{frontend_base_url}/?lang=en")
    page.locator("#input-subject").fill("Biology")
    page.locator("#input-topic").fill("Cells")
    page.locator("#start-btn").click()

    expect(page.locator("#block-screen")).to_be_visible()
    expect(page.locator("#block-message")).to_have_text(
        "Please do not share personal data."
    )
    expect(page.locator("#quiz-screen")).to_be_hidden()
