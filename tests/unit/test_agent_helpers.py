# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Deterministic unit tests for workflow helpers and routing safeguards.

Purpose:
    Cover Wikipedia relevance, grounding selection, security-router privacy,
    non-exposure of unvalidated quiz candidates, deterministic validation
    routing and structured event classification, judge retry limits, difficulty
    design contracts, curriculum schema validation, and quality diagnostics.

Regression focus:
    Safe input must reach gather_and_route without becoming client-visible
    output. Blocked PII or malicious input must never enter temporary workflow
    state.

Boundary:
    HTTP, Firestore, and model behavior are mocked. Semantic quiz quality and
    curriculum judgment belong in agents-cli eval or local integration tests.
"""

import json
import random
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.agent import (
    _ALLOWED_INPUT_STATE_KEY,
    CurriculumCompatibility,
    JudgeAssessment,
    _append_judge_history,
    _append_repair_history,
    _build_difficulty_design_guidance,
    _build_judge_prompt,
    _candidate_ready_event,
    _duplicate_option_question_indices,
    _expected_quiz_difficulty,
    _is_wikipedia_title_relevant,
    _judge_route,
    _load_authoritative_previous_quiz,
    _quality_failure_event,
    _resolve_mascot,
    _route_after_failed_deterministic_validation,
    _route_after_failed_judge,
    _save_quality_failure_best_effort,
    _validated_quiz_event,
    _validated_record_matches_context,
    _workflow_event,
    ask_more_node,
    deterministic_quiz_validation,
    gather_and_route,
    llm_as_a_judge,
    quiz_generation,
    quiz_output_node,
    search_wikipedia,
    security_block_node,
    security_checkpoint_node,
    shuffle_question_options,
    shuffle_quiz_options,
)
from app.app_utils.token_usage import TerminalOutcome
from app.app_utils.typing import QuizContext, QuizQualityFailure, UsageSummary
from app.database.firestore_repo import FirestorePersistenceError
from app.domain.difficulty import DifficultyLevel
from app.domain.quiz_provenance import (
    VALIDATION_CONTRACT_VERSION,
    context_fingerprint,
    quiz_fingerprint,
)
from app.domain.quiz_validation import validate_quiz_candidate


def _valid_public_quiz() -> dict:
    """Build a deterministic public quiz fixture for workflow boundary tests."""
    return {
        "title": "Cells",
        "difficulty": "medium",
        "questions": [
            {
                "question": f"Question {index}?",
                "options": [
                    f"Q{index} correct",
                    f"Q{index} distractor A",
                    f"Q{index} distractor B",
                ],
                "correct_option_index": 0,
                "explanation": f"Explanation {index}.",
            }
            for index in range(10)
        ],
    }


def _validated_quiz_record(quiz: dict, validated_quiz_id: str) -> dict:
    """Build a provenance record matching the production trust contract."""
    return {
        "validated_quiz_id": validated_quiz_id,
        "quiz": quiz,
        "quiz_fingerprint": quiz_fingerprint(quiz),
        "context_fingerprint": context_fingerprint(
            grade="Klasse 10",
            subject="Biology",
            topic="Cells",
            preferred_language="en",
        ),
        "validation_contract_version": VALIDATION_CONTRACT_VERSION,
    }


@pytest.mark.parametrize(
    ("mascot_id", "language", "expected"),
    [
        ("fox", "pt", ("fox", "Felix, a Raposa")),
        ("owl", "en", ("owl", "Olivia the Owl")),
        ("dragon", "de", ("dragon", "Dino der Drache")),
        ("tampered", "pt", ("fox", "Felix, a Raposa")),
        (None, "unsupported", ("fox", "Felix the Fox")),
    ],
    ids=[
        "fox-portuguese",
        "owl-english",
        "dragon-german",
        "invalid-id-falls-back-to-fox",
        "missing-id-and-language-fall-back",
    ],
)
def test_mascot_resolution_uses_allowlisted_identity_and_safe_fallback(
    mascot_id: object, language: str, expected: tuple[str, str]
) -> None:
    """Keep the selected identity, falling back to English Felix when invalid."""
    assert _resolve_mascot(mascot_id, language) == expected


def test_wikipedia_title_relevance_rejects_unrelated_legal_it() -> None:
    assert not _is_wikipedia_title_relevant(
        "Inform\u00e1tica jur\u00eddica", "opcoes e certificados"
    )
    assert not _is_wikipedia_title_relevant(
        "Certificado de Dep\u00f3sito Interbanc\u00e1rio", "opcoes e certificados"
    )
    assert _is_wikipedia_title_relevant("Op\u00e7\u00e3o (finan\u00e7as)", "opcoes")


def test_wikipedia_search_skips_irrelevant_first_result() -> None:
    def response(payload: dict) -> MagicMock:
        mock_response = MagicMock()
        mock_response.json.return_value = payload
        return mock_response

    with patch("requests.get") as mock_get:
        mock_get.side_effect = [
            response(
                {
                    "query": {
                        "search": [
                            {"pageid": 1, "title": "Inform\u00e1tica jur\u00eddica"},
                            {"pageid": 2, "title": "Op\u00e7\u00e3o (finan\u00e7as)"},
                        ]
                    }
                }
            ),
            response(
                {
                    "query": {
                        "pages": [
                            {
                                "pageid": 2,
                                "title": "Op\u00e7\u00e3o (finan\u00e7as)",
                                "extract": "Uma op\u00e7\u00e3o \u00e9 um instrumento financeiro.",
                            }
                        ]
                    }
                }
            ),
        ]

        grounding = search_wikipedia(
            "economia opcoes e certificados",
            lang="pt",
            topic="opcoes",
        )

    assert "Op\u00e7\u00e3o (finan\u00e7as)" in grounding
    assert "Inform\u00e1tica jur\u00eddica" not in grounding
    assert mock_get.call_args_list[1].kwargs["params"]["pageids"] == 2


@pytest.mark.asyncio
async def test_security_checkpoint_forwards_original_input_on_allowed_route() -> None:
    """The security router must carry safe input without emitting it to clients."""
    payload = json.dumps({"grade": "Grade 8", "subject": "Biology", "topic": "Cells"})
    original_input = MagicMock()
    original_input.parts = [MagicMock(text=payload)]
    context = MagicMock()
    context.state = {}

    events = [
        event
        async for event in security_checkpoint_node._run_impl(
            ctx=context,
            node_input=original_input,
        )
    ]

    assert len(events) == 1
    assert events[0].actions.route == "allowed"
    assert events[0].output is None
    assert context.state[_ALLOWED_INPUT_STATE_KEY] == payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        "Create a biology quiz.",
        "{not-valid-json}",
        json.dumps({"grade": "Grade 8", "subject": "Biology"}),
        json.dumps(
            {
                "grade": "Grade 8",
                "subject": "Biology",
                "topic": "Cells",
                "unsupported": "value",
            }
        ),
    ],
    ids=["free-form", "malformed", "incomplete", "extra-field"],
)
async def test_gather_rejects_unsupported_request_without_model_or_usage(
    payload: str,
) -> None:
    """Unsupported requests must not reach extractor or mascot model calls."""
    context = MagicMock()
    context.state = {_ALLOWED_INPUT_STATE_KEY: payload}

    with (
        patch("app.agent.Client") as client_class,
        patch("app.agent.record_token_usage") as record_usage,
    ):
        events = [
            event
            async for event in gather_and_route._run_impl(
                ctx=context,
                node_input=None,
            )
        ]

    assert len(events) == 1
    assert events[0].actions.route == "ask_more"
    assert "expected quiz format" in events[0].content.parts[0].text
    client_class.assert_not_called()
    record_usage.assert_not_called()


@pytest.mark.asyncio
async def test_security_checkpoint_does_not_store_blocked_input() -> None:
    """Blocked personal or malicious input must not enter workflow state."""
    context = MagicMock()
    context.state = {"temp:foxquiz_security_block": {"block_type": "PII"}}
    original_input = MagicMock()
    original_input.parts = [MagicMock(text="private user input")]

    events = [
        event
        async for event in security_checkpoint_node._run_impl(
            ctx=context,
            node_input=original_input,
        )
    ]

    assert len(events) == 1
    assert events[0].actions.route == "blocked"
    assert events[0].output is None
    assert context.state[_ALLOWED_INPUT_STATE_KEY] == ""


def test_generation_ready_event_does_not_expose_unvalidated_quiz() -> None:
    event = _candidate_ready_event()

    assert event.output == {"status": "candidate_ready"}
    assert "questions" not in event.output


def test_internal_workflow_event_satisfies_eval_without_visible_text() -> None:
    """Routing metadata must survive SSE without becoming learner-facing text."""
    event = _workflow_event(route="valid", output={"status": "ready"})

    assert event.content is not None
    assert event.content.parts
    assert all(not part.text for part in event.content.parts)
    assert event.actions.route == "valid"
    assert event.output == {"status": "ready"}


def test_validated_quiz_event_is_available_to_frontend_and_eval() -> None:
    """The final safe quiz must survive both output and content-only clients."""
    quiz = {"title": "Ready", "questions": [{"question": "Safe?"}]}

    event = _validated_quiz_event(quiz)

    assert event.output == quiz
    assert json.loads(event.content.parts[0].text) == quiz


def test_validated_provenance_requires_matching_server_record_and_context() -> None:
    """A client-supplied ID cannot authorize a different stored quiz or context."""
    quiz = _valid_public_quiz()
    validated_quiz_id = "validated-quiz-id-with-more-than-forty-characters-123"
    record = _validated_quiz_record(quiz, validated_quiz_id)

    assert (
        _validated_record_matches_context(
            record,
            validated_quiz_id=validated_quiz_id,
            grade="Klasse 10",
            subject="Biology",
            topic="Cells",
            preferred_language="en",
        )
        == quiz
    )
    assert (
        _validated_record_matches_context(
            record,
            validated_quiz_id="another-validated-quiz-id-with-more-than-forty-characters-123",
            grade="Klasse 10",
            subject="Biology",
            topic="Cells",
            preferred_language="en",
        )
        is None
    )
    assert (
        _validated_record_matches_context(
            record,
            validated_quiz_id=validated_quiz_id,
            grade="Klasse 10",
            subject="Chemistry",
            topic="Cells",
            preferred_language="en",
        )
        is None
    )


@pytest.mark.asyncio
async def test_reinforcement_reuses_only_currently_validated_server_quiz() -> None:
    """A valid Firestore source is shuffled and skips generation and Judge calls."""
    quiz = _valid_public_quiz()
    validated_quiz_id = "validated-quiz-id-with-more-than-forty-characters-123"
    context = MagicMock()
    context.state = {
        "grade": "Klasse 10",
        "subject": "Biology",
        "topic": "Cells",
        "preferred_language": "en",
        "previous_score": 2,
        "judge_attempts": 0,
        "validated_quiz_id": validated_quiz_id,
        "previous_quiz_json": {"title": "Client-controlled quiz"},
        "previous_questions": ["Client-controlled question"],
    }

    with patch("app.agent.FirestoreRepository") as repository_class:
        repository_class.return_value.get_validated_quiz.return_value = (
            _validated_quiz_record(quiz, validated_quiz_id)
        )
        _load_authoritative_previous_quiz(context)

    assert context.state["validated_quiz_bypass_allowed"] is True
    assert context.state["authoritative_previous_quiz"] == quiz
    assert context.state["previous_quiz_json"] is None
    assert context.state["previous_questions"] == [
        question["question"] for question in quiz["questions"]
    ]

    with patch("app.agent.Client") as client_class:
        events = [
            event
            async for event in quiz_generation._run_impl(
                ctx=context,
                node_input=None,
            )
        ]
        judge_events = [
            event
            async for event in llm_as_a_judge._run_impl(
                ctx=context,
                node_input=None,
            )
        ]

    assert events[0].output == {"status": "candidate_ready"}
    assert judge_events[0].actions.route == "success"
    client_class.assert_not_called()
    reinforced_quiz = context.state["temp_quiz"]
    assert {question["question"] for question in reinforced_quiz["questions"]} == {
        question["question"] for question in quiz["questions"]
    }
    assert reinforced_quiz["difficulty"] == "easy"
    assert validate_quiz_candidate(reinforced_quiz, grade="Klasse 10").is_valid
    assert context.state["judge_attempts"] == 0


def test_invalid_server_provenance_clears_client_reinforcement_data() -> None:
    """Missing or incompatible provenance forces the normal generation path."""
    context = MagicMock()
    context.state = {
        "grade": "Klasse 10",
        "subject": "Biology",
        "topic": "Cells",
        "preferred_language": "en",
        "previous_score": 2,
        "validated_quiz_id": "validated-quiz-id-with-more-than-forty-characters-123",
        "previous_quiz_json": {"title": "Client-controlled quiz"},
        "previous_questions": ["Client-controlled question"],
    }

    with patch("app.agent.FirestoreRepository") as repository_class:
        repository_class.return_value.get_validated_quiz.return_value = None
        _load_authoritative_previous_quiz(context)

    assert context.state["validated_quiz_bypass_allowed"] is False
    assert context.state["authoritative_previous_quiz"] is None
    assert context.state["previous_quiz_json"] is None
    assert context.state["previous_questions"] is None


@pytest.mark.asyncio
async def test_generation_does_not_send_untrusted_previous_questions_to_model() -> None:
    """Client-provided previous question text is ignored without provenance."""
    response = MagicMock(
        text=json.dumps(
            {
                "title": "New quiz",
                "questions": [
                    {
                        "question": f"Question {index}?",
                        "options": ["A", "B", "C"],
                        "correct_answer": "A",
                        "explanation": "Explanation.",
                    }
                    for index in range(10)
                ],
            }
        )
    )
    context = MagicMock()
    context.state = {
        "grade": "Klasse 10",
        "subject": "Biology",
        "topic": "Cells",
        "preferred_language": "en",
        "previous_score": 9,
        "selected_difficulty": "hard",
        "previous_questions": ["client-only-private-question-marker"],
        "previous_quiz_json": {"title": "Client-controlled quiz"},
    }

    with (
        patch("app.agent.Client") as client_class,
        patch("app.agent.record_token_usage"),
    ):
        client_class.return_value.aio.models.generate_content = AsyncMock(
            return_value=response
        )
        _ = [
            event
            async for event in quiz_generation._run_impl(
                ctx=context,
                node_input=None,
            )
        ]

    prompt = client_class.return_value.aio.models.generate_content.await_args.kwargs[
        "contents"
    ]
    assert "client-only-private-question-marker" not in prompt


def test_each_quiz_repair_kind_retries_once_then_fails_closed() -> None:
    """Deterministic and academic corrections have independent one-use budgets."""
    assert _route_after_failed_deterministic_validation(0) == "retry"
    assert _route_after_failed_deterministic_validation(1) == "quality_failure"
    assert _route_after_failed_judge(0) == "retry"
    assert _route_after_failed_judge(1) == "quality_failure"


@pytest.mark.parametrize(
    ("previous_score", "selected_difficulty", "expected"),
    [
        (None, None, DifficultyLevel.MEDIUM),
        (3, None, DifficultyLevel.EASY),
        (7, None, DifficultyLevel.MEDIUM),
        (9, "medium", DifficultyLevel.MEDIUM),
        (9, "hard", DifficultyLevel.HARD),
        (10, None, DifficultyLevel.HARD),
    ],
)
def test_expected_quiz_difficulty_is_shared_across_adaptive_modes(
    previous_score: int | None,
    selected_difficulty: str | None,
    expected: DifficultyLevel,
) -> None:
    """One deterministic contract keeps generation metadata and review aligned."""
    assert _expected_quiz_difficulty(previous_score, selected_difficulty) == expected


@pytest.mark.parametrize(
    ("difficulty", "required_fragments"),
    [
        (DifficultyLevel.EASY, ("short, concrete", "unnecessarily large numbers")),
        (
            DifficultyLevel.MEDIUM,
            ("balanced standard-grade mix", "estimation, strategy"),
        ),
        (
            DifficultyLevel.HARD,
            (
                "at least four meaningfully different task forms",
                "at most two pure long-form exact calculations",
                "calculator-like busywork",
                "tightly clustered numeric distractors",
            ),
        ),
        ("easy", ("short, concrete", "unnecessarily large numbers")),
        ("🌱 Easy", ("short, concrete", "unnecessarily large numbers")),
        ("hard", ("at least four meaningfully different task forms",)),
        ("🚀 Hard", ("at least four meaningfully different task forms",)),
    ],
)
def test_difficulty_design_guidance_controls_variety_and_workload(
    difficulty: DifficultyLevel | str, required_fragments: tuple[str, ...]
) -> None:
    """Each adaptive level defines task variety and manageable cognitive load."""
    guidance = _build_difficulty_design_guidance(difficulty)
    assert all(fragment in guidance for fragment in required_fragments)
    assert "required multiple-choice schema" in guidance


def test_judge_prompt_treats_hard_as_relative_to_grade() -> None:
    """A Grade 5 hard-mode label must not be mistaken for higher-grade content."""
    prompt = _build_judge_prompt(
        quiz_dict={"difficulty": "hard", "questions": []},
        grade="Klasse 5",
        subject="Ciencias",
        topic="Ciclo de vida de uma planta",
        curriculum_guidance="Stay within the Grade 5 plant-life-cycle scope.",
        previous_score=10,
        selected_difficulty="hard",
    )

    assert "expected difficulty field is exactly 'hard'" in prompt
    assert "relative to the requested grade" in prompt
    assert "Do not reject a quiz merely because 'hard'" in prompt
    assert "required quality criterion" in prompt
    assert "at most two pure long-form exact calculations" in prompt
    assert "calculator-like busywork" in prompt
    assert "within the authoritative curriculum scope" in prompt


def test_judge_prompt_scopes_emoji_and_task_variety_reviews() -> None:
    """Presentation emojis and narrow-topic variety are not rejection triggers."""
    prompt = _build_judge_prompt(
        quiz_dict={"title": "Quiz 🦊", "questions": []},
        grade="Klasse 7",
        subject="Mathematik",
        topic="Zahlenfolgen",
        curriculum_guidance="Stay within the Grade 7 sequence scope.",
        previous_score=None,
        selected_difficulty=None,
    )

    assert (
        "Ignore emojis in the quiz title and explanations; those fields are allowed"
        in prompt
    )
    assert "The quiz title is presentation-only and is intentionally omitted" in prompt
    assert '"title": "Quiz 🦊"' not in prompt
    assert "must not produce an emoji_in_question issue" in prompt
    assert "Task variety is not a rigid numeric minimum" in prompt
    assert (
        "Do not reject a narrow topic solely because it has fewer than four forms"
        in prompt
    )


def test_judge_prompt_includes_prior_structural_repair_history() -> None:
    """The Judge receives compact provenance for defects repaired earlier."""
    prompt = _build_judge_prompt(
        quiz_dict={"difficulty": "medium", "questions": []},
        grade="Klasse 10",
        subject="Chemie",
        topic="Redoxreaktionen",
        curriculum_guidance="Stay within the supplied curriculum scope.",
        previous_score=None,
        selected_difficulty=None,
        repair_history=[
            {
                "repair_kind": "structural",
                "issue_codes": ["duplicate_option"],
                "question_indices": [2],
            }
        ],
    )

    assert "PRIOR REPAIR HISTORY" in prompt
    assert '"issue_codes": ["duplicate_option"]' in prompt
    assert '"question_indices": [2]' in prompt
    assert "Review the complete current quiz" in prompt


def test_judge_prompt_applies_early_primary_contract() -> None:
    """The Judge enforces the same Grade 1 rules as generation and validation."""
    prompt = _build_judge_prompt(
        quiz_dict={"difficulty": "medium", "questions": []},
        grade="Klasse 1",
        subject="Mathematik",
        topic="Zahlen bis 20",
        curriculum_guidance="Use counting and simple addition within 20.",
        previous_score=None,
        selected_difficulty=None,
    )

    assert "exactly 3 answer options" in prompt
    assert "one or two short sentences" in prompt
    assert "hard acceptance requirements" in prompt
    assert "Set passed to false" in prompt
    assert "Do not use negative questions or double negatives" in prompt


def _judge_assessment(code: str, question_indices: list[int]) -> JudgeAssessment:
    return JudgeAssessment(
        passed=False,
        summary="The quiz needs correction.",
        issues=[
            {
                "code": code,
                "question_indices": question_indices,
                "explanation": "A bounded issue was found.",
                "repair_instruction": "Correct the issue.",
            }
        ],
    )


def test_judge_routes_only_addressable_local_issues_to_targeted_repair() -> None:
    assert _judge_route(_judge_assessment("factual_error", [2])) == "targeted"


@pytest.mark.parametrize(
    "code",
    [
        "factual_error",
        "correct_answer_mismatch",
        "negative_question",
        "emoji_in_question",
        "explanation_error",
    ],
)
def test_every_local_judge_issue_code_supports_targeted_repair(code: str) -> None:
    assert _judge_route(_judge_assessment(code, [2])) == "targeted"


@pytest.mark.parametrize(
    "assessment",
    [
        _judge_assessment("grade_scope_violation", []),
        _judge_assessment("other", [2]),
        _judge_assessment("factual_error", []),
        _judge_assessment("factual_error", [10]),
        JudgeAssessment(
            passed=False,
            summary="Mixed issues.",
            issues=[
                {
                    "code": "factual_error",
                    "question_indices": [2],
                    "explanation": "Local.",
                    "repair_instruction": "Fix it.",
                },
                {
                    "code": "language_mismatch",
                    "question_indices": [],
                    "explanation": "Global.",
                    "repair_instruction": "Regenerate it.",
                },
            ],
        ),
    ],
)
def test_judge_routes_global_or_unaddressable_issues_to_full_regeneration(
    assessment: JudgeAssessment,
) -> None:
    assert _judge_route(assessment) == "full_regeneration"


def test_judge_assessment_invariants_fail_closed() -> None:
    with pytest.raises(ValidationError):
        JudgeAssessment(passed=True, summary="Passed", issues=[{}])
    with pytest.raises(ValidationError):
        JudgeAssessment(passed=False, summary="Rejected", issues=[])


@pytest.mark.asyncio
async def test_quiz_generation_prompt_requires_normalized_unique_options() -> None:
    """Every generation attempt must receive the option-uniqueness contract."""
    context = MagicMock()
    context.state = {
        "grade": "Klasse 1",
        "subject": "Biologia",
        "topic": "Herança mendeliana",
        "preferred_language": "pt",
    }
    quiz = {
        "title": "Herança mendeliana",
        "questions": [
            {
                "question": f"Question {number}?",
                "options": ["Option A", "Option B", "Option C"],
                "correct_answer": "Option A",
                "explanation": "An explanation.",
            }
            for number in range(10)
        ],
        "difficulty": "medium",
    }
    response = MagicMock(text=json.dumps(quiz))

    with (
        patch("app.agent.Client") as client_class,
        patch("app.agent.record_token_usage"),
    ):
        generate_content = AsyncMock(return_value=response)
        client_class.return_value.aio.models.generate_content = generate_content

        _ = [
            event
            async for event in quiz_generation._run_impl(
                ctx=context,
                node_input=None,
            )
        ]

    prompt = generate_content.await_args.kwargs["contents"]
    assert "Every option within one question must be meaningfully distinct" in prompt
    assert "unique after Unicode normalization" in prompt
    assert "compare every pair of options" in prompt
    assert "replace repeated or equivalent choices" in prompt
    assert "Do not use any emoji in question text." in prompt
    assert "correct_answer" in prompt
    assert "every explanation must contain no more than two short sentences" in prompt


@pytest.mark.asyncio
async def test_quiz_generation_repairs_only_questions_with_duplicate_options() -> None:
    """A duplicate-only retry preserves every unaffected part of the quiz."""
    quiz = {
        "title": "Herança mendeliana",
        "questions": [
            {
                "question": f"Question {number}?",
                "options": (
                    ["Same option", "same option", "Other option"]
                    if number == 0
                    else ["Option A", "Option B", "Option C"]
                ),
                "correct_option_index": 0,
                "explanation": f"Explanation {number}.",
            }
            for number in range(10)
        ],
        "difficulty": "medium",
    }
    repaired_response = MagicMock(
        text=json.dumps(
            {
                "repairs": [
                    {
                        "question_index": 0,
                        "question": "Question 0?",
                        "options": ["First option", "Second option", "Third option"],
                        "correct_answer": "First option",
                        "explanation": "Explanation 0.",
                    }
                ]
            }
        )
    )
    context = MagicMock()
    context.state = {
        "grade": "Klasse 10",
        "subject": "Biologia",
        "topic": "Herança mendeliana",
        "preferred_language": "pt",
        "generation_attempts": 1,
        "deterministic_repair_attempts": 0,
        "academic_repair_attempts": 0,
        "pending_quiz_repair_kind": "deterministic",
        "temp_quiz": quiz,
        "deterministic_validation_issues": [
            {
                "code": "duplicate_option",
                "question_index": 0,
                "option_index": 1,
            }
        ],
    }

    with (
        patch("app.agent.Client") as client_class,
        patch("app.agent.record_token_usage"),
    ):
        generate_content = AsyncMock(return_value=repaired_response)
        client_class.return_value.aio.models.generate_content = generate_content

        events = [
            event
            async for event in quiz_generation._run_impl(
                ctx=context,
                node_input=None,
            )
        ]

    repaired_quiz = context.state["temp_quiz"]
    assert events[0].output == {"status": "candidate_ready"}
    assert repaired_quiz["questions"][0]["question"] == "Question 0?"
    assert repaired_quiz["questions"][0]["explanation"] == "Explanation 0."
    assert set(repaired_quiz["questions"][0]["options"]) == {
        "First option",
        "Second option",
        "Third option",
    }
    assert (
        repaired_quiz["questions"][0]["options"][
            repaired_quiz["questions"][0]["correct_option_index"]
        ]
        == "First option"
    )
    assert repaired_quiz["questions"][1:] == quiz["questions"][1:]
    assert context.state["generation_attempts"] == 2
    assert context.state["deterministic_repair_attempts"] == 1
    assert context.state["academic_repair_attempts"] == 0
    assert context.state["pending_quiz_repair_kind"] is None
    config = generate_content.await_args.kwargs["config"]
    assert config.response_schema.__name__ == "GeneratedQuestionRepairResponse"
    assert config.temperature == 0.2


def test_duplicate_option_repair_rejects_mixed_validation_issues() -> None:
    """Specialized repair must not handle unrelated structural defects."""
    issues = [
        {"code": "duplicate_option", "question_index": 0, "option_index": 1},
        {"code": "empty_explanation", "question_index": 4},
    ]

    assert _duplicate_option_question_indices(issues) == ()


@pytest.mark.asyncio
async def test_academic_repair_uses_full_generation_after_deterministic_repair() -> (
    None
):
    """A Judge retry remains available after a targeted deterministic repair."""
    response = MagicMock(
        text=json.dumps(
            {
                "title": "Corrected quiz",
                "questions": [
                    {
                        "question": f"Question {number}?",
                        "options": ["Option A", "Option B", "Option C"],
                        "correct_answer": "Option A",
                        "explanation": "An explanation.",
                    }
                    for number in range(10)
                ],
                "difficulty": "medium",
            }
        )
    )
    context = MagicMock()
    context.state = {
        "grade": "Klasse 10",
        "subject": "Biologia",
        "topic": "Herança mendeliana",
        "preferred_language": "pt",
        "generation_attempts": 2,
        "deterministic_repair_attempts": 1,
        "academic_repair_attempts": 0,
        "pending_quiz_repair_kind": "academic",
        "judge_summary": "A factual issue was found.",
        "judge_issues": [
            {
                "code": "factual_error",
                "question_indices": [0],
                "explanation": "Two answer options are factually correct.",
                "repair_instruction": "Correct question 0.",
            }
        ],
        "repair_history": [
            {
                "attempt": 2,
                "kind": "targeted",
                "issue_codes": ["duplicate_option"],
                "question_indices": [0],
                "result": "applied",
            }
        ],
        "deterministic_validation_issues": [
            {
                "code": "duplicate_option",
                "question_index": 0,
                "option_index": 1,
            }
        ],
    }

    with (
        patch("app.agent.Client") as client_class,
        patch("app.agent.record_token_usage"),
    ):
        generate_content = AsyncMock(return_value=response)
        client_class.return_value.aio.models.generate_content = generate_content
        events = [
            event
            async for event in quiz_generation._run_impl(
                ctx=context,
                node_input=None,
            )
        ]

    assert events[0].output == {"status": "candidate_ready"}
    assert context.state["generation_attempts"] == 3
    assert context.state["deterministic_repair_attempts"] == 1
    assert context.state["academic_repair_attempts"] == 1
    assert context.state["pending_quiz_repair_kind"] is None
    config = generate_content.await_args.kwargs["config"]
    assert config.response_schema.__name__ == "GeneratedQuiz"
    prompt = generate_content.await_args.kwargs["contents"]
    assert "A factual issue was found." in prompt
    assert "Two answer options are factually correct." in prompt
    assert "PRIOR REPAIR HISTORY" in prompt
    assert '"question_indices": [0]' in prompt


@pytest.mark.asyncio
async def test_academic_targeted_repair_preserves_unaffected_questions() -> None:
    """Local academic repair sends only unaffected texts and replaces one question."""
    quiz = _valid_public_quiz()
    repaired_response = MagicMock(
        text=json.dumps(
            {
                "repairs": [
                    {
                        "question_index": 2,
                        "question": "Repaired question 2?",
                        "options": ["New correct", "New wrong A", "New wrong B"],
                        "correct_answer": "New correct",
                        "explanation": "A corrected explanation.",
                    }
                ]
            }
        )
    )
    context = MagicMock()
    context.state = {
        "grade": "Klasse 10",
        "subject": "Biology",
        "topic": "Cells",
        "preferred_language": "en",
        "generation_attempts": 1,
        "academic_repair_attempts": 0,
        "pending_quiz_repair_kind": "academic_targeted",
        "temp_quiz": quiz,
        "judge_issues": [
            {
                "code": "factual_error",
                "question_indices": [2],
                "explanation": "The fact is incorrect.",
                "repair_instruction": "Correct question 2.",
            }
        ],
    }

    with (
        patch("app.agent.Client") as client_class,
        patch("app.agent.record_token_usage"),
    ):
        client_class.return_value.aio.models.generate_content = AsyncMock(
            return_value=repaired_response
        )
        events = [
            event
            async for event in quiz_generation._run_impl(
                ctx=context,
                node_input=None,
            )
        ]

    assert events[0].output == {"status": "candidate_ready"}
    repaired_quiz = context.state["temp_quiz"]
    assert repaired_quiz["questions"][0] == quiz["questions"][0]
    assert repaired_quiz["questions"][1] == quiz["questions"][1]
    assert repaired_quiz["questions"][3:] == quiz["questions"][3:]
    assert repaired_quiz["questions"][2]["question"] == "Repaired question 2?"
    assert (
        repaired_quiz["questions"][2]["options"][
            repaired_quiz["questions"][2]["correct_option_index"]
        ]
        == "New correct"
    )
    prompt = client_class.return_value.aio.models.generate_content.await_args.kwargs[
        "contents"
    ]
    assert "Question 0?" in prompt
    assert "Q0 correct" not in prompt
    assert "Q2 correct" in prompt


@pytest.mark.asyncio
async def test_malformed_judge_response_fails_closed() -> None:
    """Malformed structured review output must never reach the learner."""
    context = MagicMock()
    context.state = {
        "temp_quiz": _valid_public_quiz(),
        "grade": "Klasse 10",
        "subject": "Biology",
        "topic": "Cells",
        "preferred_language": "en",
        "judge_attempts": 0,
        "academic_repair_attempts": 0,
    }
    response = MagicMock(text="{ malformed judge response")

    with (
        patch("app.agent.Client") as client_class,
        patch("app.agent.record_token_usage"),
    ):
        client_class.return_value.aio.models.generate_content = AsyncMock(
            return_value=response
        )
        events = [
            event
            async for event in llm_as_a_judge._run_impl(
                ctx=context,
                node_input=None,
            )
        ]

    assert events[0].actions.route == "quality_failure"
    assert context.state["quality_failure_type"] == "judge_exception"
    assert context.state["judge_history"] == [
        {
            "attempt": 1,
            "passed": False,
            "issue_codes": ["judge_exception"],
            "question_indices": [],
            "selected_route": "quality_failure",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("academic_repair_attempts", "expected_route", "expected_pending_kind"),
    [(0, "retry", "academic_targeted"), (1, "quality_failure", None)],
)
async def test_judge_routes_against_its_independent_repair_budget(
    academic_repair_attempts: int,
    expected_route: str,
    expected_pending_kind: str | None,
) -> None:
    """Judge rejection neither consumes nor depends on deterministic repairs."""
    context = MagicMock()
    context.state = {
        "temp_quiz": {"title": "Candidate", "questions": []},
        "grade": "Klasse 10",
        "subject": "Biologia",
        "topic": "Herança mendeliana",
        "judge_attempts": academic_repair_attempts,
        "academic_repair_attempts": academic_repair_attempts,
        "deterministic_repair_attempts": 1,
    }
    response = MagicMock(
        text=json.dumps(
            {
                "passed": False,
                "summary": "A factual issue was found.",
                "issues": [
                    {
                        "code": "factual_error",
                        "question_indices": [0],
                        "explanation": "Two answer options are factually correct.",
                        "repair_instruction": "Correct question 0.",
                    }
                ],
            }
        )
    )

    with (
        patch("app.agent.Client") as client_class,
        patch("app.agent.record_token_usage"),
    ):
        generate_content = AsyncMock(return_value=response)
        client_class.return_value.aio.models.generate_content = generate_content
        events = [
            event
            async for event in llm_as_a_judge._run_impl(
                ctx=context,
                node_input=None,
            )
        ]

    assert events[0].actions.route == expected_route
    assert context.state["pending_quiz_repair_kind"] == expected_pending_kind
    assert context.state["deterministic_repair_attempts"] == 1


@pytest.mark.asyncio
async def test_deterministic_validation_routes_answer_cue_to_retry() -> None:
    """The first invalid candidate emits a failure event and bypasses the judge."""
    context = MagicMock()
    context.state = {
        "generation_attempts": 1,
        "deterministic_repair_attempts": 0,
        "temp_quiz": {
            "title": "Invalid",
            "questions": [
                {
                    "question": f"Question {number}?",
                    "options": ["Correct \u2705", "Wrong A", "Wrong B"],
                    "correct_option_index": 0,
                    "explanation": "An explanation.",
                }
                for number in range(10)
            ],
        },
    }

    with patch("app.agent.emit_quiz_validation_event") as emit_event:
        events = [
            event
            async for event in deterministic_quiz_validation._run_impl(
                ctx=context,
                node_input=None,
            )
        ]

    assert events[0].actions.route == "retry"
    assert context.state["pending_quiz_repair_kind"] == "deterministic"
    assert context.state["quality_failure_type"] == "deterministic_validation_failed"
    assert "Correct" not in context.state["deterministic_retry_guidance"]
    assert context.state.get("repair_history", []) == []
    assert emit_event.call_args.kwargs["event"] == "quiz_validation_failed"
    assert emit_event.call_args.kwargs["generation_attempt"] == 1


@pytest.mark.asyncio
async def test_deterministic_validation_fails_closed_after_retry_budget() -> None:
    """An exhausted retry emits its event and blocks the candidate from learners."""
    context = MagicMock()
    context.state = {
        "generation_attempts": 2,
        "deterministic_repair_attempts": 1,
        "temp_quiz": None,
    }

    with patch("app.agent.emit_quiz_validation_event") as emit_event:
        events = [
            event
            async for event in deterministic_quiz_validation._run_impl(
                ctx=context,
                node_input=None,
            )
        ]

    assert events[0].actions.route == "quality_failure"
    assert context.state["pending_quiz_repair_kind"] is None
    assert emit_event.call_args.kwargs["event"] == "quiz_validation_retry_exhausted"
    assert emit_event.call_args.kwargs["generation_attempt"] == 2


def test_curriculum_compatibility_supports_clarification_gate() -> None:
    assessment = CurriculumCompatibility(
        status="needs_clarification",
        explanation="Multiplication is too broad for Grade 12.",
        clarification_question="Do you mean matrices, polynomials, or complex numbers?",
        suggested_topics=[
            "Matrix multiplication",
            "Polynomial multiplication",
            "Complex-number multiplication",
        ],
    )

    assert assessment.status == "needs_clarification"
    assert assessment.difficulty_guidance == ""
    assert len(assessment.suggested_topics) == 3


def test_curriculum_compatibility_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        CurriculumCompatibility(
            status="maybe",
            explanation="Unknown decision",
        )


def test_quality_failure_persistence_is_best_effort() -> None:
    failure = QuizQualityFailure(
        quiz_context=QuizContext(
            grade="Klasse 12",
            subject="Economia",
            topic="Opcoes e certificados",
            preferred_language="pt",
        ),
        failure_type="judge_exception",
        generation_attempts=1,
        judge_attempts=1,
        academic_repair_attempts=0,
        deterministic_repair_attempts=0,
        usage_summary=UsageSummary(
            model_call_count=0,
            prompt_token_count=0,
            candidate_token_count=0,
            thoughts_token_count=0,
            total_token_count=0,
            stage_total_token_counts={},
        ),
        duration_ms=0,
        service_version="dev",
        deployment_revision="dev",
        grounding_discarded=True,
    )

    with patch("app.agent.FirestoreRepository") as repository_class:
        repository_class.return_value.save_quiz_quality_failure.side_effect = (
            FirestorePersistenceError(
                "save_quiz_quality_failure", "quality_diagnostic_persistence"
            )
        )
        _save_quality_failure_best_effort(failure)


def test_invalid_judge_indices_do_not_break_failure_diagnostics() -> None:
    assessment = _judge_assessment("factual_error", list(range(-1, 12)))
    context = MagicMock()
    context.state = {"preferred_language": "de", "temp_quiz": _valid_public_quiz()}

    assert _judge_route(assessment) == "full_regeneration"
    _append_judge_history(
        context, assessment=assessment, attempt=1, selected_route="full_regeneration"
    )
    _append_repair_history(
        context,
        attempt=2,
        kind="full_regeneration",
        issue_codes=["factual_error"],
        question_indices=assessment.issues[0].question_indices,
        result="applied",
    )
    with patch("app.agent._save_quality_failure_best_effort") as save_failure:
        event = _quality_failure_event(context)

    failure = save_failure.call_args.args[0]
    assert failure.judge_history[0].question_indices == list(range(10))
    assert failure.repair_history[0].question_indices == list(range(10))
    assert "Ich konnte" in (event.content.parts[0].text or "")
    assert context.state["temp_quiz"] is None


@pytest.mark.parametrize("failure_stage", ["construction", "persistence"])
def test_diagnostic_errors_preserve_failure_response_and_privacy(
    failure_stage: str, caplog: pytest.LogCaptureFixture
) -> None:
    context = MagicMock()
    context.state = {"preferred_language": "de", "temp_quiz": _valid_public_quiz()}
    if failure_stage == "construction":
        context.state["judge_history"] = [{"unexpected": "PRIVATE_DIAGNOSTIC"}]

    with patch("app.agent._save_quality_failure_best_effort") as save_failure:
        if failure_stage == "persistence":
            save_failure.side_effect = RuntimeError("PRIVATE_DIAGNOSTIC")
        event = _quality_failure_event(context)

    assert "Ich konnte" in (event.content.parts[0].text or "")
    assert context.state["temp_quiz"] is None
    assert context.state["judge_history"] == []
    assert "PRIVATE_DIAGNOSTIC" not in caplog.text


@pytest.mark.asyncio
async def test_normalization_guidance_reaches_the_retry_generator() -> None:
    generated = _valid_public_quiz()
    for question in generated["questions"]:
        question["correct_answer"] = question["options"][
            question.pop("correct_option_index")
        ]
    valid_response = json.dumps(generated)
    generated["questions"][3]["correct_answer"] = "Missing answer"
    context = MagicMock()
    context.state = {"grade": "Klasse 7", "preferred_language": "en"}
    with (
        patch("app.agent.Client") as client_class,
        patch("app.agent.record_token_usage"),
        patch("app.agent.emit_quiz_validation_event"),
    ):
        generate = AsyncMock(
            side_effect=[
                MagicMock(text=json.dumps(generated)),
                MagicMock(text=valid_response),
            ]
        )
        client_class.return_value.aio.models.generate_content = generate
        for workflow_node in (
            quiz_generation,
            deterministic_quiz_validation,
            quiz_generation,
            deterministic_quiz_validation,
        ):
            events = [
                event
                async for event in workflow_node._run_impl(ctx=context, node_input=None)
            ]

    retry_prompt = generate.call_args_list[1].kwargs["contents"]
    assert "Question 4: correct answer not in options" in retry_prompt
    assert context.state["deterministic_repair_attempts"] == 1
    assert context.state["deterministic_retry_guidance"] == ""
    assert events[0].actions.route == "valid"


@pytest.mark.asyncio
async def test_approved_quiz_is_released_when_provenance_save_fails() -> None:
    quiz = _valid_public_quiz()
    context = MagicMock()
    context.state = {"temp_quiz": quiz, "preferred_language": "en"}
    with patch("app.agent.FirestoreRepository") as repository_class:
        repository_class.return_value.save_validated_quiz.side_effect = (
            FirestorePersistenceError(
                "save_validated_quiz", "validated_quiz_provenance"
            )
        )
        events = [
            event
            async for event in quiz_output_node._run_impl(ctx=context, node_input=None)
        ]

    assert events[-1].output == quiz
    assert "validated_quiz_id" not in events[-1].output


@pytest.mark.asyncio
async def test_terminal_nodes_record_precise_invocation_outcomes() -> None:
    valid_quiz = {
        "title": "Valid",
        "difficulty": "medium",
        "questions": [
            {
                "question": f"Question {number}?",
                "options": ["Option A", "Option B", "Option C"],
                "correct_option_index": 0,
                "explanation": "An explanation.",
            }
            for number in range(10)
        ],
    }
    context = MagicMock()
    context.state = {
        "preferred_language": "en",
        "temp_quiz": valid_quiz,
        "generation_attempts": 1,
        "judge_attempts": 1,
    }

    with (
        patch("app.agent.set_invocation_outcome") as set_outcome,
        patch("app.agent.FirestoreRepository") as repository_class,
    ):
        repository_class.return_value.save_validated_quiz.return_value = (
            "test-validated-quiz-id"
        )
        quiz_events = [
            event
            async for event in quiz_output_node._run_impl(
                ctx=context,
                node_input=None,
            )
        ]

    assert quiz_events[-1].output
    assert quiz_events[-1].output.pop("validated_quiz_id", None)
    assert quiz_events[-1].output == valid_quiz
    set_outcome.assert_called_once_with(context, TerminalOutcome.SUCCESS)

    with patch("app.agent.set_invocation_outcome") as set_outcome:
        _ = [
            event
            async for event in ask_more_node._run_impl(
                ctx=context,
                node_input=None,
            )
        ]
    set_outcome.assert_called_once_with(context, TerminalOutcome.NEEDS_INPUT)

    context.state = {"temp:foxquiz_security_block": {"message": "Blocked"}}
    with patch("app.agent.set_invocation_outcome") as set_outcome:
        _ = [
            event
            async for event in security_block_node._run_impl(
                ctx=context,
                node_input=None,
            )
        ]
    set_outcome.assert_called_once_with(context, TerminalOutcome.BLOCKED)

    context.state = {"preferred_language": "en"}
    with (
        patch("app.agent.set_invocation_outcome") as set_outcome,
        patch("app.agent._save_quality_failure_best_effort"),
    ):
        _quality_failure_event(context)
    set_outcome.assert_called_once_with(context, TerminalOutcome.QUALITY_FAILURE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("generation_attempt", "expected_event"),
    [(1, "quiz_validation_passed"), (2, "quiz_validation_retry_passed")],
)
async def test_deterministic_validation_logs_success_without_candidate(
    generation_attempt: int,
    expected_event: str,
) -> None:
    """First-pass and recovered candidates produce distinct aggregate events."""
    context = MagicMock()
    context.state = {
        "generation_attempts": generation_attempt,
        "temp_quiz": {
            "title": "Valid",
            "difficulty": "medium",
            "questions": [
                {
                    "question": f"Question {number}?",
                    "options": ["Option A", "Option B", "Option C"],
                    "correct_option_index": 0,
                    "explanation": "An explanation.",
                }
                for number in range(10)
            ],
        },
    }

    with patch("app.agent.emit_quiz_validation_event") as emit_event:
        events = [
            event
            async for event in deterministic_quiz_validation._run_impl(
                ctx=context,
                node_input=None,
            )
        ]

    assert events[0].actions.route == "valid"
    assert emit_event.call_args.kwargs["event"] == expected_event
    assert emit_event.call_args.kwargs["generation_attempt"] == generation_attempt


@pytest.mark.parametrize(
    ("option_count", "correct_idx"),
    [
        (3, 0),
        (3, 1),
        (3, 2),
        (4, 0),
        (4, 3),
        (5, 0),
        (5, 4),
    ],
    ids=[
        "3-options-first",
        "3-options-middle",
        "3-options-last",
        "4-options-first",
        "4-options-last",
        "5-options-first",
        "5-options-last",
    ],
)
def test_shuffle_question_options_preserves_correct_answer(
    option_count: int, correct_idx: int
) -> None:
    """Option shuffling preserves semantic correctness across 3, 4, and 5 options."""
    options = [f"Choice {chr(ord('A') + i)}" for i in range(option_count)]
    expected_correct_text = options[correct_idx]
    question = {
        "question": "What is the correct answer?",
        "options": list(options),
        "correct_option_index": correct_idx,
        "explanation": "Explanation here.",
    }

    rng = random.Random(42)
    shuffled = shuffle_question_options(question, rng=rng)

    assert set(shuffled["options"]) == set(options)
    assert len(shuffled["options"]) == option_count
    assert (
        shuffled["options"][shuffled["correct_option_index"]] == expected_correct_text
    )


def test_shuffle_question_options_robust_against_duplicate_texts() -> None:
    """Permutation uses index mapping to avoid corrupting candidate duplicates."""
    question = {
        "question": "Duplicate options test?",
        "options": ["Duplicate", "Duplicate", "Unique"],
        "correct_option_index": 1,
        "explanation": "Second option was chosen.",
    }
    rng = random.Random(123)
    shuffled = shuffle_question_options(question, rng=rng)

    assert len(shuffled["options"]) == 3
    assert 0 <= shuffled["correct_option_index"] < 3
    assert shuffled["options"][shuffled["correct_option_index"]] == "Duplicate"


def test_shuffle_question_options_handles_invalid_data_gracefully() -> None:
    """Non-conforming questions pass through without throwing exceptions."""
    assert shuffle_question_options({}) == {}
    assert shuffle_question_options({"options": "not a list"}) == {
        "options": "not a list"
    }
    assert shuffle_question_options(
        {"options": ["A", "B"], "correct_option_index": 5}
    ) == {"options": ["A", "B"], "correct_option_index": 5}
    assert shuffle_question_options(
        {"options": ["A", "B"], "correct_option_index": -1}
    ) == {"options": ["A", "B"], "correct_option_index": -1}


def test_shuffle_quiz_options_shuffles_all_questions_deterministically() -> None:
    """Quiz-level shuffling applies permutation across every question."""
    quiz = {
        "title": "Grade 1 Math",
        "questions": [
            {
                "question": f"Question {i}?",
                "options": [f"Q{i} Option A", f"Q{i} Option B", f"Q{i} Option C"],
                "correct_option_index": 0,
                "explanation": f"Explanation {i}",
            }
            for i in range(10)
        ],
    }

    rng = random.Random(999)
    shuffled_quiz = shuffle_quiz_options(quiz, rng=rng)

    assert len(shuffled_quiz["questions"]) == 10
    for i, q in enumerate(shuffled_quiz["questions"]):
        expected_text = f"Q{i} Option A"
        assert q["options"][q["correct_option_index"]] == expected_text
        assert set(q["options"]) == {
            f"Q{i} Option A",
            f"Q{i} Option B",
            f"Q{i} Option C",
        }

    # Verify that the correct_option_indices across the 10 questions are not all 0
    indices = [q["correct_option_index"] for q in shuffled_quiz["questions"]]
    assert len(set(indices)) > 1


@pytest.mark.parametrize(
    ("score", "selection", "mode"),
    [
        (None, None, "initial"),
        (0, None, "reinforcement"),
        (3, "hard", "reinforcement"),
        (4, None, "practice"),
        (7, "hard", "practice"),
        (8, None, "progression"),
        (8, "hard", "challenge"),
        (10, None, "challenge"),
        (10, "medium", "progression"),
    ],
)
def test_adaptive_mode_boundaries(score, selection, mode):
    from app.agent import _adaptive_mode

    assert _adaptive_mode(score, selection) == mode


def test_quiz_state_reset_isolates_invocations_and_preserves_output():
    from app.agent import _reset_quiz_state

    context = MagicMock()
    candidate = _valid_public_quiz()
    old_history = [{"result": "failed"}]
    context.state = {
        "temp_quiz": candidate,
        "grade": "Grade 7",
        "repair_history": old_history,
        "pending_quiz_repair_kind": "academic_targeted",
        "validated_quiz_bypass_allowed": True,
    }
    _reset_quiz_state(context, source_id="approved-source")
    assert context.state["temp_quiz"] is candidate
    assert context.state["grade"] == "Grade 7"
    assert context.state["validated_quiz_source_id"] == "approved-source"
    assert context.state["validated_quiz_bypass_allowed"] is False
    assert context.state["pending_quiz_repair_kind"] is None
    context.state["repair_history"].append({"result": "applied"})
    _reset_quiz_state(context)
    assert context.state["repair_history"] == []
    assert old_history == [{"result": "failed"}]
    assert context.state["validated_quiz_source_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["deterministic", "academic_targeted"])
async def test_shared_repair_failure_keeps_source_and_fails_closed(source):
    from app.agent import _execute_targeted_repair, _targeted_repair_plan

    context = MagicMock()
    candidate = _valid_public_quiz()
    context.state = {
        "temp_quiz": candidate,
        "deterministic_validation_issues": [
            {"code": "duplicate_option", "question_index": 0}
        ],
        "judge_issues": [{"code": "factual_error", "question_indices": [0]}],
    }
    plan = _targeted_repair_plan(context, source)
    assert plan is not None
    with patch(
        "app.agent._repair_targeted_questions", new=AsyncMock(side_effect=ValueError)
    ):
        await _execute_targeted_repair(
            context, plan, candidate, 2, DifficultyLevel.MEDIUM
        )
    assert context.state["temp_quiz"] is None
    assert context.state["repair_failure"] == "targeted_repair_failed"
    assert context.state["quality_failure_type"] == (
        "deterministic_validation_failed"
        if source == "deterministic"
        else "judge_rejected"
    )
    assert context.state["repair_history"][-1]["result"] == "failed"


@pytest.mark.asyncio
async def test_quiz_output_node_fails_closed_when_difficulty_mismatches() -> None:
    quiz = _valid_public_quiz()
    quiz["difficulty"] = "hard"  # Mismatch: state expects medium
    context = MagicMock()
    context.state = {
        "preferred_language": "en",
        "temp_quiz": quiz,
        "difficulty": "medium",
    }
    events = [
        event
        async for event in quiz_output_node._run_impl(ctx=context, node_input=None)
    ]
    assert context.state["quality_failure_type"] == "final_invariant_failed"
    assert len(events) == 1


@pytest.mark.asyncio
async def test_quiz_output_node_fails_closed_when_quiz_schema_fails() -> None:
    quiz = _valid_public_quiz()
    quiz["questions"][0].pop("options")  # Schema violation
    context = MagicMock()
    context.state = {
        "preferred_language": "en",
        "temp_quiz": quiz,
        "difficulty": "medium",
    }
    events = [
        event
        async for event in quiz_output_node._run_impl(ctx=context, node_input=None)
    ]
    assert context.state["quality_failure_type"] == "final_invariant_failed"
    assert len(events) == 1


@pytest.mark.asyncio
async def test_quiz_output_node_releases_valid_quiz_with_quiz_boundary() -> None:
    quiz = _valid_public_quiz()
    context = MagicMock()
    context.state = {
        "preferred_language": "en",
        "temp_quiz": quiz,
        "difficulty": "medium",
    }
    with patch("app.agent.FirestoreRepository") as mock_repo:
        mock_repo.return_value.save_validated_quiz.return_value = "validated-quiz-123"
        events = [
            event
            async for event in quiz_output_node._run_impl(ctx=context, node_input=None)
        ]
    assert len(events) == 2
    terminal_event = events[1]
    assert terminal_event.output["difficulty"] == "medium"
    assert terminal_event.output["title"] == "Cells"
    assert terminal_event.output["validated_quiz_id"] == "validated-quiz-123"
