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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.agent import (
    _ALLOWED_INPUT_STATE_KEY,
    CurriculumCompatibility,
    _build_difficulty_design_guidance,
    _build_judge_prompt,
    _candidate_ready_event,
    _expected_quiz_difficulty,
    _is_wikipedia_title_relevant,
    _quality_failure_event,
    _resolve_mascot,
    _route_after_failed_judge,
    _save_quality_failure_best_effort,
    _validated_quiz_event,
    _workflow_event,
    ask_more_node,
    deterministic_quiz_validation,
    quiz_generation,
    quiz_output_node,
    search_wikipedia,
    security_block_node,
    security_checkpoint_node,
)
from app.app_utils.token_usage import TerminalOutcome
from app.app_utils.typing import QuizContext, QuizQualityFailure
from app.database.firestore_repo import FirestorePersistenceError


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
    original_input = MagicMock()
    original_input.parts = [MagicMock(text="Create a biology quiz.")]
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
    assert context.state[_ALLOWED_INPUT_STATE_KEY] == "Create a biology quiz."


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


def test_judge_retries_once_then_fails_closed() -> None:
    assert _route_after_failed_judge(1) == "retry"
    assert _route_after_failed_judge(2) == "quality_failure"


@pytest.mark.parametrize(
    ("previous_score", "selected_difficulty", "expected"),
    [
        (None, None, "⭐ Medium"),
        (3, None, "🌱 Easy"),
        (7, None, "⭐ Medium"),
        (9, "medium", "⭐ Medium"),
        (9, "hard", "🚀 Hard"),
        (10, None, "🚀 Hard"),
    ],
)
def test_expected_quiz_difficulty_is_shared_across_adaptive_modes(
    previous_score: int | None,
    selected_difficulty: str | None,
    expected: str,
) -> None:
    """One deterministic contract keeps generation metadata and review aligned."""
    assert _expected_quiz_difficulty(previous_score, selected_difficulty) == expected


@pytest.mark.parametrize(
    ("difficulty", "required_fragments"),
    [
        ("🌱 Easy", ("short, concrete", "unnecessarily large numbers")),
        ("⭐ Medium", ("balanced standard-grade mix", "estimation, strategy")),
        (
            "🚀 Hard",
            (
                "at least four meaningfully different task forms",
                "at most two pure long-form exact calculations",
                "calculator-like busywork",
                "tightly clustered numeric distractors",
            ),
        ),
    ],
)
def test_difficulty_design_guidance_controls_variety_and_workload(
    difficulty: str, required_fragments: tuple[str, ...]
) -> None:
    """Each adaptive level defines task variety and manageable cognitive load."""
    guidance = _build_difficulty_design_guidance(difficulty)
    assert all(fragment in guidance for fragment in required_fragments)
    assert "required multiple-choice schema" in guidance


def test_judge_prompt_treats_hard_as_relative_to_grade() -> None:
    """A Grade 5 hard-mode label must not be mistaken for higher-grade content."""
    prompt = _build_judge_prompt(
        quiz_dict={"difficulty": "🚀 Hard", "questions": []},
        grade="Klasse 5",
        subject="Ciencias",
        topic="Ciclo de vida de uma planta",
        curriculum_guidance="Stay within the Grade 5 plant-life-cycle scope.",
        previous_score=10,
        selected_difficulty="hard",
    )

    assert "expected difficulty field is exactly '🚀 Hard'" in prompt
    assert "relative to the requested grade" in prompt
    assert "Do not reject a quiz merely because '🚀 Hard'" in prompt
    assert "required quality criterion" in prompt
    assert "at most two pure long-form exact calculations" in prompt
    assert "calculator-like busywork" in prompt
    assert "within the authoritative curriculum scope" in prompt


@pytest.mark.asyncio
async def test_quiz_generation_prompt_requires_normalized_unique_options() -> None:
    """Every generation attempt must receive the option-uniqueness contract."""
    context = MagicMock()
    context.state = {
        "grade": "Klasse 10",
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
                "correct_option_index": 0,
                "explanation": "An explanation.",
            }
            for number in range(10)
        ],
        "difficulty": "⭐ Medium",
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


@pytest.mark.asyncio
async def test_deterministic_validation_routes_answer_cue_to_retry() -> None:
    """The first invalid candidate emits a failure event and bypasses the judge."""
    context = MagicMock()
    context.state = {
        "generation_attempts": 1,
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
    assert context.state["quality_failure_type"] == "deterministic_validation_failed"
    assert "Correct" not in context.state["deterministic_retry_guidance"]
    assert emit_event.call_args.kwargs["event"] == "quiz_validation_failed"
    assert emit_event.call_args.kwargs["generation_attempt"] == 1


@pytest.mark.asyncio
async def test_deterministic_validation_fails_closed_after_retry_budget() -> None:
    """An exhausted retry emits its event and blocks the candidate from learners."""
    context = MagicMock()
    context.state = {"generation_attempts": 2, "temp_quiz": None}

    with patch("app.agent.emit_quiz_validation_event") as emit_event:
        events = [
            event
            async for event in deterministic_quiz_validation._run_impl(
                ctx=context,
                node_input=None,
            )
        ]

    assert events[0].actions.route == "quality_failure"
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
        judge_attempts=1,
        judge_reasons=["Judge unavailable: TimeoutError"],
        grounding_discarded=True,
    )

    with patch("app.agent.FirestoreRepository") as repository_class:
        repository_class.return_value.save_quiz_quality_failure.side_effect = (
            FirestorePersistenceError(
                "save_quiz_quality_failure", "quality_diagnostic_persistence"
            )
        )
        _save_quality_failure_best_effort(failure)


@pytest.mark.asyncio
async def test_terminal_nodes_record_precise_invocation_outcomes() -> None:
    valid_quiz = {
        "title": "Valid",
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

    with patch("app.agent.set_invocation_outcome") as set_outcome:
        quiz_events = [
            event
            async for event in quiz_output_node._run_impl(
                ctx=context,
                node_input=None,
            )
        ]

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
