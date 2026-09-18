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

from uuid import UUID

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Import and exercise the application with cloud integrations disabled."""
    monkeypatch.setenv("INTEGRATION_TEST", "TRUE")
    from app.fast_api_app import app

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


def test_share_endpoint_canonicalizes_decorated_difficulty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    generated_id = "00000000-0000-4000-8000-000000000001"
    monkeypatch.setattr("app.fast_api_app.uuid.uuid4", lambda: UUID(generated_id))
    payload = {
        "quiz_data": _valid_quiz_payload(difficulty="🚀 Hard"),
    }
    response = client.post("/share", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["quiz_id"] == generated_id

    # Verify retrieval returns normalized difficulty
    get_res = client.get(f"/quiz/{generated_id}")
    assert get_res.status_code == 200
    quiz_body = get_res.json()
    assert quiz_body["status"] == "success"
    assert quiz_body["quiz_data"]["difficulty"] == "hard"


def test_share_endpoint_canonicalizes_localized_difficulty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    generated_id = "00000000-0000-4000-8000-000000000002"
    monkeypatch.setattr("app.fast_api_app.uuid.uuid4", lambda: UUID(generated_id))
    payload = {
        "quiz_data": _valid_quiz_payload(difficulty="🌱 Einfach"),
    }
    response = client.post("/share", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Verify retrieval
    get_res = client.get(f"/quiz/{generated_id}")
    assert get_res.status_code == 200
    assert get_res.json()["quiz_data"]["difficulty"] == "easy"


def test_share_endpoint_rejects_client_selected_identifier(client: TestClient) -> None:
    response = client.post(
        "/share",
        json={
            "quiz_id": "existing-shared-quiz",
            "quiz_data": _valid_quiz_payload(),
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid quiz payload for sharing."


@pytest.mark.parametrize(
    "quiz_data",
    [
        {**_valid_quiz_payload(), "title": "T" * 201},
        {
            **_valid_quiz_payload(),
            "questions": [
                {
                    **_valid_quiz_payload()["questions"][0],
                    "question": "Q" * 1_001,
                },
                *_valid_quiz_payload()["questions"][1:],
            ],
        },
        {**_valid_quiz_payload(), "topic": "T" * 501},
    ],
)
def test_share_endpoint_rejects_oversized_public_fields(
    client: TestClient, quiz_data: dict
) -> None:
    response = client.post("/share", json={"quiz_data": quiz_data})

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid quiz payload for sharing."
