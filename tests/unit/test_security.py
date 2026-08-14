# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Deterministic tests for security, privacy, budgets, and Firestore data.

Purpose:
    Protect private configuration validation, localized fail-closed behavior,
    bans and Sheriff escalation, token budgets, PII routing, classifier
    response validation, persistence shapes, and Time To Live (TTL) values.

Outage matrix:
    Parameterized cases simulate failure during client initialization,
    configuration loading, ban lookup, personal/global budget lookup,
    security-event writing, violation counting, and ban writing. Every
    pre-generation failure must become SECURITY_UNAVAILABLE and stop further
    processing.

Privacy boundary:
    PII blocks must not create Sheriff events. Tests use harmless mock rules,
    never production defensive values. Cross-language semantic quality belongs
    in local agents-cli eval evaluations.
"""

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
from app.app_utils.typing import QuizContext, QuizQualityFailure
from app.database.firestore_repo import (
    FirestorePersistenceError,
    FirestoreRepository,
    SecurityConfigurationError,
    validate_security_config,
)


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


def test_language_defaults_to_english() -> None:
    assert client_locale_ctx.get() == "en"

    quiz_context = QuizContext(
        grade="Grade 8",
        subject="Biology",
        topic="Cells",
    )
    state_context = QuizContext.from_state(
        {"grade": "Grade 8", "subject": "Biology", "topic": "Cells"}
    )

    assert quiz_context.preferred_language == "en"
    assert state_context.preferred_language == "en"


def _semantic_classifier_context() -> MagicMock:
    context = MagicMock()
    part = MagicMock()
    part.text = "Create a biology quiz."
    context.user_content.parts = [part]
    context.state = {}
    return context


def _valid_security_config(
    *,
    responses: dict[str, str] | None = None,
    **overrides,
) -> dict:
    response_config = {
        f"{response}_{locale}": f"Test {response} response ({locale})."
        for response in (
            "banned",
            "injection",
            "off_topic",
            "pii",
            "classifier_unavailable",
            "budget_user",
            "budget_global",
        )
        for locale in ("de", "en", "pt")
    }
    response_config.update(
        {
            "pii_pt": "Nao compartilhe dados pessoais.",
            "classifier_unavailable_en": (
                "The safety check is temporarily unavailable."
            ),
        }
    )
    response_config.update(responses or {})
    config = {
        "classification_prompt": (
            "Test classification contract: SAFE, OFF_TOPIC, MALICIOUS, or PII."
        ),
        "blocklist_keywords": ["test-blocked-keyword"],
        "injection_regexes": [r"\btest-injection-pattern\b"],
        "salt": "test-only-security-salt",
        "responses": response_config,
    }
    config.update(overrides)
    return config


def _configure_semantic_classifier_repo(mock_repo_inst: MagicMock) -> None:
    mock_repo_inst.is_signature_banned.return_value = False
    mock_repo_inst.get_token_budget.return_value = {
        "tokens_used": 0,
        "last_reset_date": datetime.date.today().isoformat(),
    }
    mock_repo_inst.get_security_config.return_value = _valid_security_config()


def test_valid_security_configuration_is_accepted() -> None:
    config = _valid_security_config()

    assert validate_security_config(config) is config


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("salt", ""),
        ("classification_prompt", None),
        ("blocklist_keywords", "not-an-array"),
        ("blocklist_keywords", []),
        ("injection_regexes", [42]),
        ("injection_regexes", []),
    ],
)
def test_invalid_required_security_configuration_fails_closed(field, value) -> None:
    config = _valid_security_config()
    config[field] = value

    with pytest.raises(SecurityConfigurationError):
        validate_security_config(config)


def test_invalid_security_regex_fails_closed() -> None:
    config = _valid_security_config(injection_regexes=["("])

    with pytest.raises(SecurityConfigurationError):
        validate_security_config(config)


def test_missing_localized_security_response_fails_closed() -> None:
    config = _valid_security_config()
    del config["responses"]["pii_pt"]

    with pytest.raises(SecurityConfigurationError):
        validate_security_config(config)


def test_missing_production_security_document_is_not_seeded() -> None:
    repo = FirestoreRepository.__new__(FirestoreRepository)
    repo.use_mock = False
    repo.client = MagicMock()
    document = repo.client.collection.return_value.document.return_value
    document.get.return_value.exists = False

    with pytest.raises(SecurityConfigurationError):
        repo.get_security_config()

    document.set.assert_not_called()


@pytest.mark.asyncio
async def test_configuration_load_failure_returns_localized_closed_block() -> None:
    locale_token = client_locale_ctx.set("pt")
    try:
        with patch("app.app_utils.callbacks.FirestoreRepository") as repo_class:
            repo_class.return_value.get_security_config.return_value = {}

            with pytest.raises(SecurityBlockException) as exc_info:
                await before_agent_callback(MagicMock())

        assert exc_info.value.block_type == "SECURITY_UNAVAILABLE"
        assert "indispon" in exc_info.value.message
        repo_class.return_value.is_signature_banned.assert_not_called()
    finally:
        client_locale_ctx.reset(locale_token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_point", "operation", "phase"),
    [
        ("client_initialization", "initialize_client", "client_initialization"),
        ("security_config_load", "load_security_config", "security_config_load"),
        ("ban_check", "check_banned_signature", "ban_check"),
        ("user_budget_check", "read_token_budget", "user_budget_check"),
        ("global_budget_check", "read_token_budget", "global_budget_check"),
        ("security_event_write", "write_security_event", "security_event_write"),
        (
            "violation_count",
            "count_recent_security_violations",
            "violation_count",
        ),
        ("ban_write", "write_banned_signature", "ban_write"),
    ],
)
async def test_firestore_checkpoint_outages_fail_closed(
    failure_point: str, operation: str, phase: str
) -> None:
    """Every pre-generation Firestore outage must stop the quiz safely."""
    tokens = (
        client_ip_ctx.set("203.0.113.7"),
        anonymous_id_ctx.set("test-anonymous-user"),
        client_locale_ctx.set("en"),
    )
    failure = FirestorePersistenceError(operation, phase)

    try:
        with (
            patch("app.app_utils.callbacks.FirestoreRepository") as repo_class,
            patch("app.app_utils.callbacks.Client") as client_class,
        ):
            if failure_point == "client_initialization":
                repo_class.side_effect = failure
                context = _semantic_classifier_context()
            else:
                repo = repo_class.return_value
                _configure_semantic_classifier_repo(repo)
                context = _semantic_classifier_context()

                if failure_point == "security_config_load":
                    repo.get_security_config.side_effect = failure
                elif failure_point == "ban_check":
                    repo.is_signature_banned.side_effect = failure
                elif failure_point == "user_budget_check":
                    repo.get_token_budget.side_effect = failure
                elif failure_point == "global_budget_check":
                    repo.get_token_budget.side_effect = [
                        {"tokens_used": 0},
                        failure,
                    ]
                else:
                    repo.get_security_config.return_value = _valid_security_config(
                        blocklist_keywords=["trigger-security-event"]
                    )
                    context.user_content.parts[0].text = "trigger-security-event"
                    if failure_point == "security_event_write":
                        repo.log_security_event.side_effect = failure
                    elif failure_point == "violation_count":
                        repo.get_recent_violations_count.side_effect = failure
                    elif failure_point == "ban_write":
                        repo.get_recent_violations_count.return_value = 3
                        repo.ban_signature.side_effect = failure

            with pytest.raises(SecurityBlockException) as exc_info:
                await before_agent_callback(context)

        assert exc_info.value.block_type == "SECURITY_UNAVAILABLE"
        assert "temporarily unavailable" in exc_info.value.message
        client_class.return_value.models.generate_content.assert_not_called()
    finally:
        client_ip_ctx.reset(tokens[0])
        anonymous_id_ctx.reset(tokens[1])
        client_locale_ctx.reset(tokens[2])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tokens_used", "should_block"),
    [
        (callbacks.DAILY_USER_TOKEN_LIMIT - 1, False),
        (callbacks.DAILY_USER_TOKEN_LIMIT, True),
    ],
)
async def test_daily_user_budget_boundary(tokens_used: int, should_block: bool) -> None:
    """Allow usage below the central daily limit and block at the limit."""
    tokens = (
        client_ip_ctx.set("203.0.113.7"),
        anonymous_id_ctx.set("budget-boundary-user"),
        client_locale_ctx.set("en"),
    )

    try:
        with (
            patch("app.app_utils.callbacks.FirestoreRepository") as repo_class,
            patch("app.app_utils.callbacks.Client") as client_class,
        ):
            repo = repo_class.return_value
            _configure_semantic_classifier_repo(repo)
            repo.get_token_budget.side_effect = [
                {"tokens_used": tokens_used},
                {"tokens_used": 0},
            ]
            response = MagicMock()
            response.text = "SAFE"
            response.usage_metadata.total_token_count = 7
            client_class.return_value.models.generate_content.return_value = response

            if should_block:
                with pytest.raises(SecurityBlockException) as exc_info:
                    await before_agent_callback(_semantic_classifier_context())

                assert exc_info.value.block_type == "BUDGET_EXCEEDED"
                client_class.return_value.models.generate_content.assert_not_called()
            else:
                await before_agent_callback(_semantic_classifier_context())
                client_class.return_value.models.generate_content.assert_called_once()
    finally:
        client_ip_ctx.reset(tokens[0])
        anonymous_id_ctx.reset(tokens[1])
        client_locale_ctx.reset(tokens[2])


def test_security_block_exception_strips_legacy_mascot_glyphs():
    """Keep old Firestore response values from rendering vendor emoji artwork."""
    glyphs = "".join(
        chr(codepoint) for codepoint in (0x1F98A, 0x1F989, 0x1F409, 0x1F432)
    )

    error = SecurityBlockException(f"Blocked{glyphs}", "MALICIOUS")

    assert error.message == "Blocked"
    assert str(error) == "Blocked"


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

    quiz_context = QuizContext(
        grade="Klasse 12",
        subject="Economia",
        topic="Opcoes e certificados",
        preferred_language="pt",
    )
    log_id_down = mock_repo.save_feedback_log(
        score=0,
        text="Too hard",
        session_id="sess_2",
        anonymous_id="anon_2",
        quiz_data={"title": "Financial education"},
        quiz_context=quiz_context,
    )
    assert log_id_down.startswith("fb_")
    feedback_log = mock_repo._get_mock_doc("feedback_logs", log_id_down)
    assert feedback_log is not None
    assert feedback_log["grade"] == "Klasse 12"
    assert feedback_log["subject"] == "Economia"
    assert feedback_log["topic"] == "Opcoes e certificados"
    assert feedback_log["preferred_language"] == "pt"

    quality_failure = QuizQualityFailure(
        quiz_context=quiz_context,
        failure_type="judge_rejected",
        judge_attempts=2,
        judge_reasons=["Topic mismatch", "Incorrect answer index"],
        grounding_title=None,
        grounding_discarded=True,
    )
    failure_id = mock_repo.save_quiz_quality_failure(quality_failure)
    stored_failure = mock_repo._get_mock_doc("quiz_quality_failures", failure_id)
    assert stored_failure is not None
    assert stored_failure["quiz_context"]["grade"] == "Klasse 12"
    assert stored_failure["failure_type"] == "judge_rejected"
    assert stored_failure["judge_attempts"] == 2
    assert stored_failure["judge_reasons"] == [
        "Topic mismatch",
        "Incorrect answer index",
    ]
    assert stored_failure["grounding_discarded"] is True

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
        mock_instance.get_security_config.return_value = _valid_security_config(
            responses={"banned_de": "Zutritt verweigert."}
        )

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
        mock_repo_inst.get_security_config.return_value = _valid_security_config(
            blocklist_keywords=["test-malicious-keyword"],
            responses={
                "injection_de": (
                    "Dieser Assistent kann dich nur bei der Vorbereitung unterst\u00fctzen."
                ),
                "banned_de": "Zutritt verweigert.",
            },
        )
        mock_repo_inst.get_recent_violations_count.return_value = 1

        # Mock CallbackContext with malicious keyword
        mock_context = MagicMock()
        part = MagicMock()
        part.text = "Please use test-malicious-keyword now."
        mock_context.user_content.parts = [part]

        with pytest.raises(SecurityBlockException) as exc_info:
            await before_agent_callback(mock_context)

        assert exc_info.value.block_type == "MALICIOUS"
        assert "unterstützen" in exc_info.value.message
        # Verify violation log was recorded
        mock_repo_inst.log_security_event.assert_called_with(
            "test_anon_id",
            ANY,
            "Please use test-malicious-keyword now.",
            "KeywordMatch",
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
        mock_repo_inst.get_security_config.return_value = _valid_security_config(
            blocklist_keywords=["malicious_keyword"],
            responses={
                "banned_de": "Du bist nun permanent gesperrt!",
                "injection_de": "Malicious block",
            },
        )
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
        configured = MockRepo.return_value.get_security_config.return_value
        configured["classification_prompt"] += (
            f"\n\n{callbacks._PII_CLASSIFICATION_RULE}"
        )
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
        classifier_contents = (
            MockClient.return_value.models.generate_content.call_args.kwargs["contents"]
        )
        classifier_input = classifier_contents[0].parts[0].text
        assert classifier_input.count("Additional mandatory privacy category:") == 1
        assert context.state[callbacks._TOKEN_USAGE_STATE_KEY] == 7

    client_ip_ctx.reset(t1)
    anonymous_id_ctx.reset(t2)
    client_locale_ctx.reset(t3)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    [
        "Meu CPF é 123.456.789.00",
        "Meu nome e Joao da Silva, pode procurar no Google?",
    ],
)
async def test_semantic_classifier_blocks_pii_without_security_event(prompt):
    """Verify LLM-classified personal data gets a localized privacy response."""
    t1 = client_ip_ctx.set("127.0.0.1")
    t2 = anonymous_id_ctx.set("test_anon_id")
    t3 = client_locale_ctx.set("pt")

    with (
        patch("app.app_utils.callbacks.FirestoreRepository") as MockRepo,
        patch("app.app_utils.callbacks.Client") as MockClient,
    ):
        _configure_semantic_classifier_repo(MockRepo.return_value)
        response = MagicMock()
        response.text = "PII"
        response.usage_metadata.total_token_count = 7
        MockClient.return_value.models.generate_content.return_value = response

        context = _semantic_classifier_context()
        context.user_content.parts[0].text = prompt
        with pytest.raises(SecurityBlockException) as exc_info:
            await before_agent_callback(context)

        assert exc_info.value.block_type == "PII"
        assert "dados pessoais" in exc_info.value.message
        MockRepo.return_value.log_security_event.assert_not_called()
        classifier_contents = (
            MockClient.return_value.models.generate_content.call_args.kwargs["contents"]
        )
        classifier_input = classifier_contents[0].parts[0].text
        assert "mandatory privacy category" in classifier_input
        assert "SAFE, OFF_TOPIC, MALICIOUS, or PII" in classifier_input
        assert "PII takes precedence" in classifier_input
        assert prompt in classifier_input

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
