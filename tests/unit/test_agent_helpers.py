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

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.agent import (
    _ALLOWED_INPUT_STATE_KEY,
    CurriculumCompatibility,
    _candidate_ready_event,
    _is_wikipedia_title_relevant,
    _route_after_failed_judge,
    _save_quality_failure_best_effort,
    search_wikipedia,
    security_checkpoint_node,
)
from app.app_utils.typing import QuizContext, QuizQualityFailure
from app.database.firestore_repo import FirestorePersistenceError


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


def test_judge_retries_once_then_fails_closed() -> None:
    assert _route_after_failed_judge(1) == "retry"
    assert _route_after_failed_judge(2) == "quality_failure"


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
