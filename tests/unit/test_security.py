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

import datetime
from unittest.mock import ANY, MagicMock, patch

import pytest

from app.app_utils import callbacks
from app.app_utils.callbacks import SecurityBlockException, before_agent_callback
from app.app_utils.request_context import (
    anonymous_id_ctx,
    client_ip_ctx,
    client_locale_ctx,
)
from app.database.firestore_repo import FirestoreRepository


@pytest.fixture(autouse=True)
def reset_global_cache():
    """Fixture to reset the global cached configuration between test runs."""
    callbacks._cached_config = None
    callbacks._cached_time = None
    callbacks._local_banned_cache.clear()


@pytest.fixture
def mock_repo():
    """Initializes FirestoreRepository in forced mock mode."""
    return FirestoreRepository(force_mock=True)


def _semantic_classifier_context() -> MagicMock:
    context = MagicMock()
    part = MagicMock()
    part.text = "Create a biology quiz."
    context.user_content.parts = [part]
    context.state = {}
    return context


def _configure_semantic_classifier_repo(mock_repo_inst: MagicMock) -> None:
    mock_repo_inst.is_signature_banned.return_value = False
    mock_repo_inst.get_token_budget.return_value = {
        "tokens_used": 0,
        "last_reset_date": datetime.date.today().isoformat(),
    }
    mock_repo_inst.get_security_config.return_value = {
        "classification_prompt": "Return SAFE, OFF_TOPIC, or MALICIOUS.",
        "blocklist_keywords": [],
        "injection_regexes": [],
        "responses": {},
    }


def test_firestore_repo_quiz(mock_repo):
    """Verify storing, retrieving, and expiration behavior for shared quizzes."""
    quiz_id = "test_quiz_123"
    quiz_data = {"questions": [{"question": "What is 2+2?", "answer": "4"}]}

    # Save shared quiz (TTL 30 days)
    success = mock_repo.save_shared_quiz(quiz_id, quiz_data)
    assert success is True

    stored_quiz = mock_repo._get_mock_doc("quizzes", quiz_id)
    expires_at = datetime.datetime.fromisoformat(stored_quiz["expires_at"])
    remaining = expires_at - datetime.datetime.now(datetime.UTC)
    assert datetime.timedelta(days=29, hours=23) < remaining
    assert remaining <= datetime.timedelta(days=30)

    # Retrieve shared quiz
    retrieved = mock_repo.get_shared_quiz(quiz_id)
    assert retrieved == quiz_data

    # Save a quiz with a negative TTL (already expired)
    mock_repo.save_shared_quiz("expired_quiz", quiz_data, ttl_days=-1)
    expired = mock_repo.get_shared_quiz("expired_quiz")
    assert expired is None


def test_firestore_repo_budgets(mock_repo):
    """Verify getting, incrementing, and automatic daily resets for token budgets."""
    budget_id = "test_user_budget"

    # Get initial budget (should initialize to 0)
    budget = mock_repo.get_token_budget(budget_id)
    assert budget["tokens_used"] == 0
    assert budget["last_reset_date"] == datetime.date.today().isoformat()

    # Increment token budget
    mock_repo.increment_token_budget(budget_id, 1500)
    budget = mock_repo.get_token_budget(budget_id)
    assert budget["tokens_used"] == 1500

    # Simulate calendar day advancement by manually overwriting reset date
    mock_repo._set_mock_doc(
        "budgets", budget_id, {"last_reset_date": "2020-01-01", "tokens_used": 5000}
    )
    budget = mock_repo.get_token_budget(budget_id)
    # Getting budget now should reset it back to 0
    assert budget["tokens_used"] == 0
    assert budget["last_reset_date"] == datetime.date.today().isoformat()


def test_transient_budget_expires_after_seven_days(mock_repo):
    """Verify transient budgets expire while the global budget remains permanent."""
    budget = mock_repo.get_token_budget("budget_transient_test")
    expires_at = datetime.datetime.fromisoformat(budget["expires_at"])
    remaining = expires_at - datetime.datetime.now(datetime.UTC)

    assert datetime.timedelta(days=6, hours=23) < remaining
    assert remaining <= datetime.timedelta(days=7)
    assert "expires_at" not in mock_repo.get_token_budget("global")


def test_firestore_repo_feedback(mock_repo):
    """Verify detailed feedback logging and global atomic metrics increments."""
    log_id = mock_repo.save_feedback_log(
        score=1, text="Great quiz!", session_id="sess_1", anonymous_id="anon_1"
    )
    assert log_id.startswith("fb_")

    log_id_down = mock_repo.save_feedback_log(
        score=0, text="Too hard", session_id="sess_2", anonymous_id="anon_2"
    )
    assert log_id_down.startswith("fb_")

    # Fetch aggregated metrics
    metrics = mock_repo.get_satisfaction_metrics()
    assert metrics["thumbs_up_count"] == 1
    assert metrics["thumbs_down_count"] == 1


def test_firestore_repo_banned_signatures(mock_repo):
    """Verify checking, setting, and expiring active bans."""
    hashed_ip = "dummy_hashed_ip_abc"

    assert mock_repo.is_signature_banned(hashed_ip) is False

    # Ban signature
    mock_repo.ban_signature(hashed_ip, duration_hours=24)
    assert mock_repo.is_signature_banned(hashed_ip) is True


@pytest.mark.asyncio
async def test_before_agent_callback_banned():
    """Verify that banned IP signatures are blocked instantly at zero cost."""
    # Set request ContextVars
    t1 = client_ip_ctx.set("1.2.3.4")
    t2 = anonymous_id_ctx.set("test_anon_id")
    t3 = client_locale_ctx.set("de")

    # Force mock repository mode inside callback
    with patch("app.app_utils.callbacks.FirestoreRepository") as MockRepo:
        mock_instance = MockRepo.return_value
        mock_instance.is_signature_banned.return_value = True
        mock_instance.get_security_config.return_value = {
            "classification_prompt": "Classifier instruction",
            "blocklist_keywords": [],
            "injection_regexes": [],
            "responses": {"banned_de": "Zutritt verweigert."},
        }

        mock_context = MagicMock()
        mock_context.user_content = MagicMock()

        with pytest.raises(SecurityBlockException) as exc_info:
            await before_agent_callback(mock_context)

        assert exc_info.value.block_type == "BANNED"
        assert "Zutritt" in exc_info.value.message

    # Reset ContextVars
    client_ip_ctx.reset(t1)
    anonymous_id_ctx.reset(t2)
    client_locale_ctx.reset(t3)


@pytest.mark.asyncio
async def test_before_agent_callback_keyword_violation():
    """Verify that blocklisted keywords trigger safety events, Sheriff checks, and blocks."""
    t1 = client_ip_ctx.set("127.0.0.1")
    t2 = anonymous_id_ctx.set("test_anon_id")
    t3 = client_locale_ctx.set("de")

    with patch("app.app_utils.callbacks.FirestoreRepository") as MockRepo:
        mock_repo_inst = MockRepo.return_value
        mock_repo_inst.is_signature_banned.return_value = False
        mock_repo_inst.get_token_budget.return_value = {
            "tokens_used": 0,
            "last_reset_date": datetime.date.today().isoformat(),
        }
        mock_repo_inst.get_security_config.return_value = {
            "classification_prompt": "Classifier instruction",
            "blocklist_keywords": ["drop database", "nuclear launch"],
            "injection_regexes": [],
            "responses": {
                "injection_de": "Dieser Assistent kann dich nur bei der Vorbereitung unterstützen.",
                "banned_de": "Zutritt verweigert.",
            },
        }
        mock_repo_inst.get_recent_violations_count.return_value = 1

        # Mock CallbackContext with malicious keyword
        mock_context = MagicMock()
        part = MagicMock()
        part.text = "Please drop database now."
        mock_context.user_content.parts = [part]

        with pytest.raises(SecurityBlockException) as exc_info:
            await before_agent_callback(mock_context)

        assert exc_info.value.block_type == "MALICIOUS"
        assert "unterstützen" in exc_info.value.message
        # Verify violation log was recorded
        mock_repo_inst.log_security_event.assert_called_with(
            "test_anon_id", ANY, "Please drop database now.", "KeywordMatch"
        )

    client_ip_ctx.reset(t1)
    anonymous_id_ctx.reset(t2)
    client_locale_ctx.reset(t3)


@pytest.mark.asyncio
async def test_before_agent_callback_sheriff_auto_ban():
    """Verify that a 3rd safety violation triggers an automatic Sheriff ban."""
    t1 = client_ip_ctx.set("1.1.1.1")
    t2 = anonymous_id_ctx.set("test_anon_id")
    t3 = client_locale_ctx.set("de")

    with patch("app.app_utils.callbacks.FirestoreRepository") as MockRepo:
        mock_repo_inst = MockRepo.return_value
        mock_repo_inst.is_signature_banned.return_value = False
        mock_repo_inst.get_token_budget.return_value = {
            "tokens_used": 0,
            "last_reset_date": datetime.date.today().isoformat(),
        }
        mock_repo_inst.get_security_config.return_value = {
            "classification_prompt": "Classifier instruction",
            "blocklist_keywords": ["malicious_keyword"],
            "injection_regexes": [],
            "responses": {
                "banned_de": "Du bist nun permanent gesperrt! 🤠",
                "injection_de": "Malicious block",
            },
        }
        # Simulate that this user now has 3 violations in the last hour
        mock_repo_inst.get_recent_violations_count.return_value = 3

        mock_context = MagicMock()
        part = MagicMock()
        part.text = "This input contains malicious_keyword."
        mock_context.user_content.parts = [part]

        with pytest.raises(SecurityBlockException) as exc_info:
            await before_agent_callback(mock_context)

        assert exc_info.value.block_type == "BANNED"
        assert "permanent gesperrt" in exc_info.value.message
        # Verify ban_signature was automatically triggered
        mock_repo_inst.ban_signature.assert_called_once()

    client_ip_ctx.reset(t1)
    anonymous_id_ctx.reset(t2)
    client_locale_ctx.reset(t3)


@pytest.mark.asyncio
async def test_semantic_classifier_safe_response_uses_bounded_thinking():
    """Verify a valid SAFE decision uses a bounded thinking budget."""
    t1 = client_ip_ctx.set("127.0.0.1")
    t2 = anonymous_id_ctx.set("test_anon_id")
    t3 = client_locale_ctx.set("en")

    with (
        patch("app.app_utils.callbacks.FirestoreRepository") as MockRepo,
        patch("app.app_utils.callbacks.Client") as MockClient,
    ):
        _configure_semantic_classifier_repo(MockRepo.return_value)
        response = MagicMock()
        response.text = "SAFE"
        response.usage_metadata.total_token_count = 7
        MockClient.return_value.models.generate_content.return_value = response

        context = _semantic_classifier_context()
        await before_agent_callback(context)

        generate_config = (
            MockClient.return_value.models.generate_content.call_args.kwargs["config"]
        )
        assert generate_config.max_output_tokens == 512
        assert generate_config.thinking_config.thinking_budget == 256
        assert context.state[callbacks._TOKEN_USAGE_STATE_KEY] == 7

    client_ip_ctx.reset(t1)
    anonymous_id_ctx.reset(t2)
    client_locale_ctx.reset(t3)


@pytest.mark.asyncio
@pytest.mark.parametrize("classifier_text", [None, "", "UNKNOWN"])
async def test_semantic_classifier_invalid_response_fails_closed(classifier_text):
    """Verify missing or invalid classifier output never receives safe passage."""
    t1 = client_ip_ctx.set("127.0.0.1")
    t2 = anonymous_id_ctx.set("test_anon_id")
    t3 = client_locale_ctx.set("en")

    with (
        patch("app.app_utils.callbacks.FirestoreRepository") as MockRepo,
        patch("app.app_utils.callbacks.Client") as MockClient,
    ):
        _configure_semantic_classifier_repo(MockRepo.return_value)
        response = MagicMock()
        response.text = classifier_text
        response.usage_metadata.total_token_count = 7
        MockClient.return_value.models.generate_content.return_value = response

        with pytest.raises(SecurityBlockException) as exc_info:
            await before_agent_callback(_semantic_classifier_context())

        assert exc_info.value.block_type == "CLASSIFIER_UNAVAILABLE"
        assert "temporarily unavailable" in exc_info.value.message

    client_ip_ctx.reset(t1)
    anonymous_id_ctx.reset(t2)
    client_locale_ctx.reset(t3)
