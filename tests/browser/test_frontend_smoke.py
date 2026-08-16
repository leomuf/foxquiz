"""Browser-level tests for the main FoxQuiz user journeys.

Purpose:
    Protect the learner-visible workflow: changing language and mascot,
    displaying accurate license information, submitting grade/subject/topic,
    completing a quiz, keeping newly displayed mobile content in view,
    preserving curriculum clarification context, recovering one missing ADK
    session, sending negative feedback, and receiving a security-block
    response.

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


def test_quiz_creation_reveals_the_loading_indicator(
    page: Page, frontend_base_url: str
) -> None:
    """Keep the quiz-generation progress indicator visible on mobile.

    A learner may submit the setup form after scrolling down the page. This
    test verifies that quiz creation moves the newly displayed loading card to
    the top of the viewport and gives it focus before results are displayed.
    """
    page.set_viewport_size({"width": 390, "height": 844})
    page.emulate_media(reduced_motion="reduce")
    page.add_init_script(
        """(() => {
            window.revealedElements = [];
            Element.prototype.scrollIntoView = function(options) {
                window.revealedElements.push({ id: this.id, options });
            };
        })();"""
    )
    _mock_quiz_generation(page, [])

    page.goto(f"{frontend_base_url}/?lang=en")
    page.locator("#input-grade").select_option("Klasse 5")
    page.locator("#input-subject").fill("Mathematics")
    page.locator("#input-topic").fill("Multiplication")
    page.locator("#start-btn").click()

    page.wait_for_function(
        """() => window.revealedElements.some(
            ({ id, options }) => id === "loading-screen" && options.block === "start"
        )"""
    )
    loading_reveal = page.evaluate(
        """() => window.revealedElements.find(
            ({ id }) => id === "loading-screen"
        )"""
    )
    assert loading_reveal["options"]["behavior"] == "auto"


def test_complete_quiz_and_negative_feedback_flow(
    page: Page, frontend_base_url: str
) -> None:
    """Exercise the complete quiz and feedback journey with mocked APIs.

    The mobile-sized run also verifies that every newly rendered question is
    scrolled into view and receives programmatic focus.
    """
    page.set_viewport_size({"width": 390, "height": 844})
    page.emulate_media(reduced_motion="reduce")
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
        "mascot_id": "fox",
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
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.locator("#next-btn").click()
        if question_number < 10:
            page.wait_for_function(
                """() => {
                    const question = document.getElementById("question-text");
                    const bounds = question.getBoundingClientRect();
                    return document.activeElement === question
                        && bounds.top >= 0
                        && bounds.bottom <= window.innerHeight;
                }"""
            )

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


def test_clarification_stays_visible_and_keeps_the_original_topic(
    page: Page, frontend_base_url: str
) -> None:
    """Keep a curriculum clarification visible without losing the original topic.

    The first mocked SSE response requests clarification. The second submission
    must combine the learner's answer with the original grade, subject, and
    topic instead of treating the answer as a new standalone topic.
    """
    page.set_viewport_size({"width": 390, "height": 844})
    page.emulate_media(reduced_motion="reduce")
    generated_requests: list[dict] = []
    quiz = _quiz_fixture(title="As Grandes Navegacoes", subject="Historia")

    page.route(
        "**/apps/app/users/anonymous_student/sessions",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"id": "clarification-session"}),
        ),
    )

    def fulfill_stream(route) -> None:
        generated_requests.append(route.request.post_data_json)
        if len(generated_requests) == 1:
            event = {
                "status": "clarification_required",
                "message": "Que parte deste tema voce gostaria de estudar?",
            }
        else:
            event = {"output": quiz}
        route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=f"data: {json.dumps(event)}\n\n",
        )

    page.route("**/run_sse", fulfill_stream)
    page.goto(f"{frontend_base_url}/?lang=pt")
    page.locator("#input-grade").select_option("Klasse 5")
    page.locator("#input-subject").fill("Historia")
    page.locator("#input-topic").fill("As Grandes Navegacoes")
    page.locator("#start-btn").click()

    expect(page.locator("#setup-screen")).to_be_visible()
    expect(page.locator("#welcome-bubble")).to_contain_text(
        "Que parte deste tema voce gostaria de estudar?"
    )
    page.wait_for_function(
        """() => {
            const bubble = document.getElementById("welcome-bubble");
            const bounds = bubble.getBoundingClientRect();
            return document.activeElement === bubble
                && bounds.top >= 0
                && bounds.bottom <= window.innerHeight;
        }"""
    )

    page.locator("#input-topic").fill("informacoes gerais")
    page.locator("#start-btn").click()
    expect(page.locator("#quiz-screen")).to_be_visible()

    follow_up = json.loads(generated_requests[1]["new_message"]["parts"][0]["text"])
    assert follow_up == {
        "grade": "Klasse 5",
        "subject": "Historia",
        "topic": "As Grandes Navegacoes",
        "preferred_language": "pt",
        "mascot_id": "fox",
        "clarification_response": "informacoes gerais",
    }


def test_hard_follow_up_recreates_a_missing_session_once(
    page: Page, frontend_base_url: str
) -> None:
    """Recover a hard follow-up once when its in-memory ADK session is missing.

    The first follow-up request receives HTTP 404. The browser must create one
    replacement session, resend the identical adaptive prompt exactly once,
    and display the returned hard quiz instead of the generic error screen.
    """
    session_ids: list[str] = []
    generated_requests: list[dict] = []
    quizzes = [
        _quiz_fixture(title="Multiplication Medium", difficulty="Medium"),
        _quiz_fixture(
            title="Multiplication Hard",
            difficulty="Hard",
            subject="Mathematics",
        ),
    ]

    def fulfill_session(route) -> None:
        session_id = f"browser-session-{len(session_ids) + 1}"
        session_ids.append(session_id)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"id": session_id}),
        )

    def fulfill_stream(route) -> None:
        generated_requests.append(route.request.post_data_json)
        if len(generated_requests) == 2:
            route.fulfill(status=404, body="Session not found")
            return

        quiz = quizzes[0] if len(generated_requests) == 1 else quizzes[1]
        route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=f"data: {json.dumps({'output': quiz})}\n\n",
        )

    page.route("**/apps/app/users/anonymous_student/sessions", fulfill_session)
    page.route("**/run_sse", fulfill_stream)
    page.goto(f"{frontend_base_url}/?lang=en")
    page.locator("#input-grade").select_option("Klasse 5")
    page.locator("#input-subject").fill("Mathematics")
    page.locator("#input-topic").fill("Multiplication")
    page.locator("#start-btn").click()

    for _ in range(10):
        page.locator(".option-btn").first.click()
        page.locator("#next-btn").click()

    page.locator("#btn-more-questions").click()
    page.locator(".choice-btn-hard").click()
    expect(page.locator("#quiz-screen")).to_be_visible()
    expect(page.locator("#question-text")).to_contain_text("Mathematics question 1")

    assert session_ids == ["browser-session-1", "browser-session-2"]
    assert len(generated_requests) == 3
    failed_request = generated_requests[1]
    retried_request = generated_requests[2]
    assert failed_request["session_id"] == "browser-session-1"
    assert retried_request["session_id"] == "browser-session-2"
    assert failed_request["new_message"] == retried_request["new_message"]
    adaptive_prompt = json.loads(retried_request["new_message"]["parts"][0]["text"])
    assert adaptive_prompt["selected_difficulty"] == "hard"
    assert adaptive_prompt["topic"] == "Multiplication"


def test_selected_mascot_is_sent_with_the_quiz_request(
    page: Page, frontend_base_url: str
) -> None:
    """Send the active mascot ID so backend dialogue keeps the selected identity."""
    generated_requests: list[dict] = []
    _mock_quiz_generation(page, generated_requests)

    page.goto(f"{frontend_base_url}/?lang=en")
    page.locator('.mascot-option[data-mascot="owl"]').click()
    page.locator("#input-grade").select_option("Klasse 5")
    page.locator("#input-subject").fill("Mathematics")
    page.locator("#input-topic").fill("Functions")
    page.locator("#start-btn").click()

    expect(page.locator("#quiz-screen")).to_be_visible()
    prompt = json.loads(generated_requests[0]["new_message"]["parts"][0]["text"])
    assert prompt["mascot_id"] == "owl"
