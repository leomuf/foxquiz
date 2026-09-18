# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the FastAPI /share and /quiz endpoints.

Validates:
- 400 Bad Request on missing quiz_data
- 422 Unprocessable Entity on schema violations
- Server-side canonicalization of difficulty ('🚀 Hard' -> 'hard')
- Persistence and retrieval via public quiz contracts
"""

import pytest
from starlette.testclient import TestClient

from app.fast_api_app import app


@pytest.fixture
def client():
    return TestClient(app)


def _valid_quiz_payload(difficulty: str = "medium") -> dict:
    return {
        "title": "Cells and DNA",
        "difficulty": difficulty,
        "questions": [
            {
                "question": f"Question {i}?",
                "options": ["Option A", "Option B", "Option C"],
                "correct_option_index": 0,
                "explanation": f"Explanation {i}",
            }
            for i in range(10)
        ],
    }


def test_share_endpoint_rejects_missing_quiz_data(client: TestClient) -> None:
    response = client.post("/share", json={})
    assert response.status_code == 400
    assert "Missing required quiz_data payload" in response.json()["detail"]


def test_share_endpoint_rejects_malformed_quiz_schema(client: TestClient) -> None:
    # Missing questions list
    response = client.post(
        "/share",
        json={"quiz_data": {"title": "Incomplete", "difficulty": "medium"}},
    )
    assert response.status_code == 422
    assert "Invalid quiz payload for sharing" in response.json()["detail"]


@pytest.mark.parametrize(
    "quiz_data",
    [
        {**_valid_quiz_payload(), "questions": _valid_quiz_payload()["questions"][:1]},
        _valid_quiz_payload(difficulty="banana"),
        _valid_quiz_payload(difficulty="not hard"),
    ],
)
def test_share_endpoint_rejects_invalid_public_contract(
    client: TestClient, quiz_data: dict
) -> None:
    response = client.post("/share", json={"quiz_data": quiz_data})
    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid quiz payload for sharing."


def test_share_endpoint_canonicalizes_decorated_difficulty(client: TestClient) -> None:
    payload = {
        "quiz_id": "test-canonical-hard-1",
        "quiz_data": _valid_quiz_payload(difficulty="🚀 Hard"),
    }
    response = client.post("/share", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["quiz_id"] == "test-canonical-hard-1"

    # Verify retrieval returns normalized difficulty
    get_res = client.get("/quiz/test-canonical-hard-1")
    assert get_res.status_code == 200
    quiz_body = get_res.json()
    assert quiz_body["status"] == "success"
    assert quiz_body["quiz_data"]["difficulty"] == "hard"


def test_share_endpoint_canonicalizes_localized_difficulty(client: TestClient) -> None:
    payload = {
        "quiz_id": "test-canonical-easy-1",
        "quiz_data": _valid_quiz_payload(difficulty="🌱 Einfach"),
    }
    response = client.post("/share", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Verify retrieval
    get_res = client.get("/quiz/test-canonical-easy-1")
    assert get_res.status_code == 200
    assert get_res.json()["quiz_data"]["difficulty"] == "easy"
