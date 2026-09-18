# ruff: noqa
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

# ==============================================================================
# Modified and extended by Leonardo Muffato (AUTOSOFT Engineering - www.autosoft-engineering.de) 2026.
# Copyright (c) 2026 Leonardo Muffato (AUTOSOFT Engineering - www.autosoft-engineering.de).
# All custom application additions, upfront curriculum validations, and mascot guides
# are licensed under CC BY 4.0. See global LICENSE file for details.
# ==============================================================================

import datetime
import json
import logging
import os
import random
import re
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Any, Dict, List, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

import google.auth
from google.genai import Client, types
from google.adk.models import Gemini
from google.adk.workflow import Workflow, START, node, FunctionNode, Edge
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.adk.agents.context import Context
from google.adk.apps import App

from app.app_utils.callbacks import (
    FoxQuizSecurityPlugin,
    _INVOCATION_START_STATE_KEY,
    SECURITY_BLOCK_STATE_KEY,
    _TOKEN_USAGE_STATE_KEY,
    record_token_usage,
    set_invocation_outcome,
)
from app.app_utils.build_info import get_build_info
from app.app_utils.operational_logging import emit_quiz_validation_event
from app.app_utils.request_context import get_client_locale
from app.app_utils.token_usage import CallStage, InvocationTokenUsage, TerminalOutcome
from app.app_utils.typing import QuizContext, QuizQualityFailure, UsageSummary
from app.database.firestore_repo import FirestorePersistenceError, FirestoreRepository
from app.domain.quiz_request import (
    QuizRequestValidationError,
    invalid_request_message,
    parse_quiz_request,
)
from app.domain.grade_policy import (
    PedagogicalStage,
    build_grade_prompt_guidance,
    get_grade_policy,
)
from app.domain.quiz_validation import (
    build_retry_guidance,
    validate_quiz_candidate,
)
from app.domain.difficulty import DifficultyLevel
from app.domain.quiz_generation import (
    GeneratedQuiz,
    GeneratedQuizQuestion,
    QuizNormalizationCode,
    QuizNormalizationError,
    normalize_generated_question,
    normalize_generated_quiz,
)
from app.domain.quiz_provenance import (
    VALIDATION_CONTRACT_VERSION,
    context_fingerprint,
    quiz_fingerprint,
)

# Setup project configuration
try:
    _, project_id = google.auth.default()
except Exception as e:
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "mock-project-id")

os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

logger = logging.getLogger(__name__)
DEFAULT_MASCOT_ID = "fox"
MASCOT_NAMES = {
    "fox": {
        "de": "Felix der Fuchs",
        "pt": "Felix, a Raposa",
        "en": "Felix the Fox",
    },
    "owl": {
        "de": "Olivia die Eule",
        "pt": "Olivia, a Coruja",
        "en": "Olivia the Owl",
    },
    "dragon": {
        "de": "Dino der Drache",
        "pt": "Dino, o Dragão",
        "en": "Dino the Dragon",
    },
}


def _workflow_event(*, route: str | None = None, output: Any = None) -> Event:
    """Build an eval-compatible internal event without user-visible text."""
    kwargs = {
        "content": types.Content(role="model", parts=[types.Part.from_text(text="")])
    }
    if route is not None:
        kwargs["actions"] = EventActions(route=route)
    if output is not None:
        kwargs["output"] = output
    return Event(**kwargs)


def _validated_quiz_event(quiz: Any, *, validated_quiz_id: str | None = None) -> Event:
    """Publish a validated quiz through both workflow and content contracts."""
    if hasattr(quiz, "model_dump"):
        public_output = quiz.model_dump(mode="json")
    else:
        public_output = dict(quiz)
        if "difficulty" in public_output:
            public_output["difficulty"] = DifficultyLevel.from_raw(
                public_output["difficulty"]
            ).value

    if validated_quiz_id and isinstance(validated_quiz_id, str):
        public_output["validated_quiz_id"] = validated_quiz_id
    return Event(
        content=types.Content(
            role="model",
            parts=[
                types.Part.from_text(text=json.dumps(public_output, ensure_ascii=False))
            ],
        ),
        output=public_output,
    )


def _resolve_mascot(mascot_id: Any, language: str) -> tuple[str, str]:
    """Return an allowlisted mascot ID and its localized display name."""
    normalized_id = (
        mascot_id
        if isinstance(mascot_id, str) and mascot_id in MASCOT_NAMES
        else DEFAULT_MASCOT_ID
    )
    normalized_language = language if language in {"de", "pt", "en"} else "en"
    return normalized_id, MASCOT_NAMES[normalized_id][normalized_language]


def _validated_record_matches_context(
    record: Any,
    *,
    validated_quiz_id: str,
    grade: Any,
    subject: Any,
    topic: Any,
    preferred_language: Any,
) -> dict[str, Any] | None:
    """Return a trusted public quiz only when every provenance invariant holds."""
    if not isinstance(record, dict):
        return None
    quiz = record.get("quiz")
    if not isinstance(quiz, dict):
        return None
    questions = quiz.get("questions")
    if not isinstance(questions, list):
        return None
    if record.get("validated_quiz_id") != validated_quiz_id:
        return None
    if record.get("validation_contract_version") != VALIDATION_CONTRACT_VERSION:
        return None
    if record.get("quiz_fingerprint") != quiz_fingerprint(quiz):
        return None
    if record.get("context_fingerprint") != context_fingerprint(
        grade=grade,
        subject=subject,
        topic=topic,
        preferred_language=preferred_language,
    ):
        return None
    if any(
        isinstance(question, dict) and "correct_answer" in question
        for question in questions
    ):
        return None
    if not validate_quiz_candidate(quiz, grade=grade).is_valid:
        return None
    return quiz


def _load_authoritative_previous_quiz(ctx: Context) -> None:
    """Load adaptive source data from Firestore without trusting client quiz JSON."""
    ctx.state["authoritative_previous_quiz"] = None
    ctx.state["validated_quiz_bypass_allowed"] = False
    ctx.state["validated_quiz_source_id"] = None
    # Client-provided quiz JSON is never used as provenance or sent to the model.
    ctx.state["previous_quiz_json"] = None
    ctx.state["previous_questions"] = None

    validated_quiz_id = ctx.state.get("validated_quiz_id")
    if not isinstance(validated_quiz_id, str) or not validated_quiz_id:
        return

    try:
        record = FirestoreRepository().get_validated_quiz(validated_quiz_id)
    except FirestorePersistenceError:
        logger.warning("Validated quiz provenance lookup was unavailable.")
        return

    quiz = _validated_record_matches_context(
        record,
        validated_quiz_id=validated_quiz_id,
        grade=ctx.state.get("grade"),
        subject=ctx.state.get("subject"),
        topic=ctx.state.get("topic"),
        preferred_language=ctx.state.get("preferred_language") or "en",
    )
    if quiz is None:
        logger.info("Validated quiz provenance was missing or incompatible.")
        return

    ctx.state["authoritative_previous_quiz"] = json.loads(json.dumps(quiz))
    ctx.state["validated_quiz_source_id"] = validated_quiz_id
    ctx.state["previous_questions"] = [
        question.get("question")
        for question in quiz.get("questions", [])
        if isinstance(question, dict) and isinstance(question.get("question"), str)
    ]
    if (ctx.state.get("previous_score") is not None) and int(
        ctx.state.get("previous_score")
    ) <= 3:
        ctx.state["validated_quiz_bypass_allowed"] = True


def shuffle_question_options(
    question: dict[str, Any], *, rng: random.Random | None = None
) -> dict[str, Any]:
    """Deterministically permute answer choices and update correct_option_index."""
    options = question.get("options")
    correct_idx = question.get("correct_option_index")
    if (
        not isinstance(options, list)
        or not isinstance(correct_idx, int)
        or isinstance(correct_idx, bool)
        or not 0 <= correct_idx < len(options)
    ):
        return question

    permutation = list(range(len(options)))
    if rng is not None:
        rng.shuffle(permutation)
    else:
        random.shuffle(permutation)

    question["options"] = [options[i] for i in permutation]
    question["correct_option_index"] = permutation.index(correct_idx)
    return question


def shuffle_quiz_options(
    quiz_dict: dict[str, Any], *, rng: random.Random | None = None
) -> dict[str, Any]:
    """Permute options for all questions in a generated quiz dict."""
    questions = quiz_dict.get("questions")
    if isinstance(questions, list):
        for question in questions:
            if isinstance(question, dict):
                shuffle_question_options(question, rng=rng)
    return quiz_dict


def shuffle_quiz_questions(
    quiz_dict: dict[str, Any], *, rng: random.Random | None = None
) -> dict[str, Any]:
    """Shuffle question order while preserving each question's option index."""
    questions = quiz_dict.get("questions")
    if isinstance(questions, list):
        if rng is not None:
            rng.shuffle(questions)
        else:
            random.shuffle(questions)
    return quiz_dict


# --- Pydantic Models for Quiz and Safety Structures ---


class QuizQuestion(BaseModel):
    question: str = Field(description="The question text without emojis.")
    options: List[str] = Field(
        description="List of 3 to 5 neutral text-only choices without emojis or answer cues."
    )
    correct_option_index: int = Field(
        description="0-based index of the correct option."
    )
    explanation: str = Field(
        description="A friendly, encouraging, and educational explanation of the answer."
    )


class Quiz(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str = Field(description="A fun and engaging title for the quiz.")
    questions: List[QuizQuestion] = Field(description="List of exactly 10 questions.")
    difficulty: DifficultyLevel = Field(
        description="The semantic difficulty level of the quiz ('easy', 'medium', or 'hard').",
    )

    @field_validator("difficulty", mode="before")
    @classmethod
    def _coerce_difficulty(cls, value: Any) -> DifficultyLevel:
        if value is None:
            raise ValueError("Difficulty is required and cannot be None.")
        return DifficultyLevel.from_raw(value)


class JudgeIssueCode(StrEnum):
    """Allowlisted academic-review issue categories."""

    FACTUAL_ERROR = "factual_error"
    CORRECT_ANSWER_MISMATCH = "correct_answer_mismatch"
    NEGATIVE_QUESTION = "negative_question"
    EMOJI_IN_QUESTION = "emoji_in_question"
    GRADE_SCOPE_VIOLATION = "grade_scope_violation"
    DIFFICULTY_MISMATCH = "difficulty_mismatch"
    LANGUAGE_MISMATCH = "language_mismatch"
    TASK_VARIETY_FAILURE = "task_variety_failure"
    EXPLANATION_ERROR = "explanation_error"
    OTHER = "other"


class JudgeIssue(BaseModel):
    """One structured academic-review issue."""

    model_config = ConfigDict(extra="forbid")

    code: JudgeIssueCode
    # Missing indices are represented as an empty list so routing can safely
    # choose full regeneration rather than attempting a local repair.
    question_indices: list[StrictInt] = Field(default_factory=list)
    explanation: str = Field(description="Readable explanation of the issue.")
    repair_instruction: str = Field(
        description="Concrete instruction for correcting this issue."
    )


class JudgeAssessment(BaseModel):
    """Structured academic Judge result with fail-closed invariants."""

    model_config = ConfigDict(extra="forbid")

    passed: StrictBool = Field(
        description="True if the quiz meets all criteria: 10 questions, appropriate grade difficulty, exactly one correct option per question, and factually accurate."
    )
    summary: str = Field(description="Readable overall review summary.")
    issues: list[JudgeIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result_invariants(self) -> "JudgeAssessment":
        if self.passed and self.issues:
            raise ValueError("A passed Judge assessment cannot contain issues.")
        if not self.passed and not self.issues:
            raise ValueError("A rejected Judge assessment must contain issues.")
        return self


class GeneratedQuestionRepair(BaseModel):
    """One complete generated question returned by targeted repair."""

    model_config = ConfigDict(extra="forbid", strict=True)

    question_index: int = Field(description="0-based question index to repair.")
    question: str
    options: list[str]
    correct_answer: str
    explanation: str


class GeneratedQuestionRepairResponse(BaseModel):
    """Complete internal questions returned by a targeted repair call."""

    model_config = ConfigDict(extra="forbid", strict=True)

    repairs: list[GeneratedQuestionRepair] = Field(
        description="Exactly one complete repair for every requested question index."
    )


class CurriculumCompatibility(BaseModel):
    status: Literal["compatible", "needs_clarification", "incompatible"] = Field(
        description="Whether the request is ready for generation, needs a narrower scope, or is incompatible with the grade and subject."
    )
    explanation: str = Field(
        description="A concise, friendly, user-facing explanation in the requested language."
    )
    difficulty_guidance: str = Field(
        default="",
        description="Concrete scope, workload, task variety, concepts, and exclusions needed to keep the quiz aligned with the requested grade and difficulty. Required when status is compatible.",
    )
    clarification_question: str = Field(
        default="",
        description="A short localized question that resolves a genuinely ambiguous or unintelligible topic.",
    )
    suggested_topics: List[str] = Field(
        default_factory=list,
        description="Two or three localized scopes or alternative topics when clarification is needed or the request is incompatible.",
    )


# --- Helper Function for Curriculum Search Skill ---


_WIKIPEDIA_TOPIC_STOP_WORDS = {
    "and",
    "das",
    "der",
    "die",
    "ein",
    "eine",
    "for",
    "the",
    "und",
}
MIN_MEANINGFUL_TOPIC_WORD_LENGTH = 3
MIN_PARTIAL_WORD_MATCH_LENGTH = 4
MIN_TOPIC_TITLE_SIMILARITY_RATIO = 0.72
MAX_WIKIPEDIA_SEARCH_RESULTS_TO_EVALUATE = 5
WIKIPEDIA_REQUEST_TIMEOUT_SECONDS = 5


def _normalized_words(value: str) -> list[str]:
    """Return lowercase, accent-insensitive words for relevance comparisons."""
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return re.findall(r"[a-z0-9]+", without_accents.casefold())


def _is_wikipedia_title_relevant(title: str, topic: str) -> bool:
    """Require every meaningful topic term to match the article title."""
    topic_words = [
        word
        for word in _normalized_words(topic)
        if len(word) >= MIN_MEANINGFUL_TOPIC_WORD_LENGTH
        and word not in _WIKIPEDIA_TOPIC_STOP_WORDS
    ]
    title_words = _normalized_words(title)
    return bool(topic_words) and all(
        any(
            topic_word == title_word
            or (
                min(len(topic_word), len(title_word)) >= MIN_PARTIAL_WORD_MATCH_LENGTH
                and (
                    topic_word in title_word
                    or title_word in topic_word
                    or SequenceMatcher(None, topic_word, title_word).ratio()
                    >= MIN_TOPIC_TITLE_SIMILARITY_RATIO
                )
            )
            for title_word in title_words
        )
        for topic_word in topic_words
    )


def search_wikipedia(query: str, lang: str = "en", topic: str | None = None) -> str:
    """Real live Wikipedia search API call to gather localized curriculum context (GDPR-safe, zero model cost)."""
    try:
        import requests

        url = f"https://{lang}.wikipedia.org/w/api.php"
        headers = {
            "User-Agent": "FoxQuizBot/1.0 (https://github.com/leomuf/foxquiz; support@foxquiz.app) requests-python"
        }

        # Step 1: Search for matches
        search_params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": query,
            "utf8": 1,
            "formatversion": 2,
        }
        r = requests.get(
            url,
            params=search_params,
            headers=headers,
            timeout=WIKIPEDIA_REQUEST_TIMEOUT_SECONDS,
        )
        r.raise_for_status()
        data = r.json()
        search_results = data.get("query", {}).get("search", [])
        if not search_results:
            return ""

        relevant_result = next(
            (
                result
                for result in search_results[:MAX_WIKIPEDIA_SEARCH_RESULTS_TO_EVALUATE]
                if not topic
                or _is_wikipedia_title_relevant(result.get("title", ""), topic)
            ),
            None,
        )
        if relevant_result is None:
            logger.warning(
                "Discarding Wikipedia grounding because no result title matched."
            )
            return ""

        # Step 2: Extract article intro
        page_id = relevant_result["pageid"]
        title = relevant_result["title"]
        extract_params = {
            "action": "query",
            "format": "json",
            "prop": "extracts",
            "pageids": page_id,
            "exintro": 1,
            "explaintext": 1,
            "formatversion": 2,
        }
        r = requests.get(
            url,
            params=extract_params,
            headers=headers,
            timeout=WIKIPEDIA_REQUEST_TIMEOUT_SECONDS,
        )
        r.raise_for_status()
        page_data = r.json().get("query", {}).get("pages", [{}])[0]
        extract = page_data.get("extract", "")
        if not extract:
            return (
                f"Wikipedia article found: '{title}', but no text intro was available."
            )
        return f"Grounding facts from Wikipedia page '{title}':\n{extract}"
    except Exception as e:
        logger.warning(
            "Wikipedia search failed (%s). Proceeding with internal LLM knowledge.",
            type(e).__name__,
        )
        return ""


# --- Graph Nodes ---

_ALLOWED_INPUT_STATE_KEY = "temp:foxquiz_allowed_input"


def _text_from_node_input(node_input: Any) -> str:
    """Extract the text request passed between workflow nodes."""
    if isinstance(node_input, str):
        return node_input
    if hasattr(node_input, "parts"):
        return "".join(
            part.text for part in node_input.parts if getattr(part, "text", None)
        ).strip()
    if isinstance(node_input, dict):
        return node_input.get("text", "")
    return ""


def _reset_quiz_state(ctx: Context, *, source_id: str | None = None) -> None:
    """Reset per-invocation repair and provenance state using fresh containers.

    Call after recording terminal diagnostics. Candidate output and request
    fields remain available to their owners.
    """
    ctx.state["judge_attempts"] = 0
    ctx.state["generation_attempts"] = 0
    ctx.state["deterministic_repair_attempts"] = 0
    ctx.state["academic_repair_attempts"] = 0
    ctx.state["pending_quiz_repair_kind"] = None
    ctx.state[_REPAIR_FAILURE_STATE_KEY] = None
    ctx.state["deterministic_retry_guidance"] = ""
    ctx.state["deterministic_validation_issues"] = []
    ctx.state["judge_history"] = []
    ctx.state["judge_issues"] = []
    ctx.state["judge_summary"] = ""
    ctx.state["repair_history"] = []
    ctx.state["normalization_failures"] = []
    ctx.state["authoritative_previous_quiz"] = None
    ctx.state["validated_quiz_bypass_allowed"] = False
    ctx.state["validated_quiz_source_id"] = source_id


@node
async def gather_and_route(ctx: Context, node_input: Any) -> Event:
    """Load a validated structured request and perform curriculum routing."""
    prompt = _text_from_node_input(node_input)
    if not prompt:
        prompt = ctx.state.get(_ALLOWED_INPUT_STATE_KEY, "") or ""
    ctx.state[_ALLOWED_INPUT_STATE_KEY] = ""

    logger.info("Gather and Route started.")

    try:
        request = parse_quiz_request(prompt)
    except QuizRequestValidationError:
        logger.warning("Rejected a request that did not match the quiz contract.")
        lang = get_client_locale()
        return Event(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text=invalid_request_message(lang))],
            ),
            actions=EventActions(route="ask_more"),
        )

    request_state = request.model_dump()
    for field_name, field_value in request_state.items():
        ctx.state[field_name] = field_value
    logger.info("Loaded validated structured quiz parameters.")

    # Reset quality diagnostics on any fresh start or new turn.
    _reset_quiz_state(ctx)
    ctx.state["curriculum_status"] = None
    ctx.state["curriculum_guidance"] = ""
    ctx.state["quality_failure_type"] = None
    ctx.state["grounding_title"] = None
    ctx.state["grounding_discarded"] = False
    _load_authoritative_previous_quiz(ctx)

    grade = ctx.state.get("grade")
    subject = ctx.state.get("subject")
    topic = ctx.state.get("topic")
    lang = ctx.state.get("preferred_language") or "en"
    mascot_id, mascot_name = _resolve_mascot(ctx.state.get("mascot_id"), lang)
    ctx.state["mascot_id"] = mascot_id
    clarification_response = ctx.state.get("clarification_response")

    if grade and subject and topic:
        expected_difficulty = _expected_quiz_difficulty(
            ctx.state.get("previous_score"), ctx.state.get("selected_difficulty")
        )
        difficulty_design_guidance = _build_difficulty_design_guidance(
            expected_difficulty
        )
        grade_policy = get_grade_policy(grade)
        grade_guidance = build_grade_prompt_guidance(grade_policy)
        grade_label = grade_policy.localized_label(lang)
        primary_scope_guidance = ""
        if grade_policy.stage in {
            PedagogicalStage.PRIMARY_EARLY,
            PedagogicalStage.PRIMARY_LATE,
        }:
            primary_scope_guidance = (
                "For Grades 1-4, request clarification when an umbrella topic "
                "would span materially different foundational skills and no "
                "specific learning goal was supplied. For example, Grade 1 "
                "Mathematics plus 'Arithmetic' should clarify whether to focus "
                "on counting, addition, subtraction, or another concrete skill. "
                "Do not clarify a topic that already names a concrete, "
                "age-appropriate skill.\n"
            )

        # Perform Upfront Curriculum Validation Check to prevent mismatched/inappropriate topics
        logger.info("Performing upfront curriculum validation check.")
        client = Client()
        try:
            validation_prompt = (
                "You are a strict but supportive school curriculum scope evaluator.\n"
                f"Target Language: '{lang}'.\n"
                f"Grade Level: {grade_label} (Grade {int(grade_policy.grade)}, ages {grade_policy.minimum_age}-{grade_policy.maximum_age})\n"
                f"Subject: {subject}\nTopic: {topic}\n\n"
                "Additional scope supplied after a clarification question: "
                f"{clarification_response or 'none'}\n\n"
                f"Requested adaptive level: {expected_difficulty}.\n"
                "Apply this generator-wide difficulty design contract:\n"
                f"{difficulty_design_guidance}\n\n"
                "Apply this authoritative age-appropriate design contract:\n"
                f"{grade_guidance}\n\n"
                "Decide whether this exact combination is ready for quiz generation.\n"
                "Use status='compatible' only when the topic has a clear interpretation at the requested grade level without silently changing the requested topic. "
                "Except for the explicit Grades 1-4 foundational-skill rule below, a recognizable school topic is compatible even when it is broad: when no narrower scope is supplied, interpret it as a balanced general overview of the topic. "
                "Treat an answer such as 'general information' as an explicit request for that overview. "
                "Secondary and upper-secondary students (Grades 5-12) can explore standard school subjects, sciences, introductory economics, and humanities at an age-appropriate conceptual level. "
                "Provide difficulty_guidance with concrete grade-level concepts, reasonable workload and task types to include, plus elementary or overly advanced concepts and repetitive task patterns to exclude. Translate the design contract into topic-specific guidance rather than weakening it.\n"
                "Use status='needs_clarification' only when the topic is genuinely ambiguous, unintelligible, or level-dependent in a way that would produce materially different quizzes and no safe conventional school interpretation exists. "
                "Do not request clarification merely because a valid school topic covers many facts or subtopics. "
                f"{primary_scope_guidance}"
                "For example, Grade 12 Mathematics plus 'Multiplication' needs clarification between matrix multiplication, polynomial multiplication, complex-number multiplication, or another advanced scope; it must not generate elementary multiplication questions. "
                "Provide a short clarification_question and two or three suggested_topics/scopes.\n"
                "Use status='incompatible' when the topic is fundamentally outside the subject, cognitively inappropriate for the grade, or not a suitable school-learning topic. "
                "Provide two or three age-appropriate alternatives.\n"
                "Do not accept a combination merely because the topic could be simplified or made harder. First require enough scope to produce a genuinely grade-aligned quiz.\n"
                "When additional clarification is present, interpret it together with the original topic rather than replacing the original topic.\n"
                f"CRITICAL LANGUAGE RULE: Write explanation, clarification_question, suggested_topics, and difficulty_guidance entirely in language '{lang}'. All suggested_topics must be localized educational topic titles strictly in language '{lang}' (never suggest German topics or words when language is '{lang}').\n"
                "Return structured JSON matching CurriculumCompatibility."
            )
            response = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=validation_prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=CurriculumCompatibility,
                    temperature=0.0,
                ),
            )
            record_token_usage(
                ctx,
                response,
                call_stage=CallStage.CURRICULUM_EVALUATOR,
            )
            compatibility = CurriculumCompatibility.model_validate_json(
                response.text.strip()
            )
            logger.info(
                "Upfront curriculum check completed with status=%s.",
                compatibility.status,
            )
            ctx.state["curriculum_status"] = compatibility.status
            ctx.state["curriculum_guidance"] = compatibility.difficulty_guidance

            if compatibility.status == "compatible":
                ctx.state["pending_topic"] = None
                ctx.state["clarification_response"] = None
                return _workflow_event(route="generate_quiz")
            elif compatibility.status == "needs_clarification":
                ctx.state["pending_topic"] = topic
                msg_text = (
                    compatibility.clarification_question or compatibility.explanation
                )
                clarification_payload = json.dumps(
                    {
                        "status": "clarification_required",
                        "message": msg_text,
                    },
                    ensure_ascii=False,
                )
                return Event(
                    content=types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=clarification_payload)],
                    ),
                    actions=EventActions(route="ask_more"),
                )
            else:
                # Clear incompatible topic from state so they can enter a new one
                ctx.state["topic"] = None
                ctx.state["pending_topic"] = None
                ctx.state["clarification_response"] = None

                mascot_prompt = (
                    f"You are {mascot_name}, a friendly, encouraging school learning companion mascot speaking directly to a child.\n"
                    f"If you introduce yourself, use exactly the name '{mascot_name}' and never claim to be another mascot.\n"
                    f"The child asked for a quiz about '{topic}' in {grade_label} and Subject '{subject}', but this topic is too complex or not appropriate (Explanation: {compatibility.explanation}).\n"
                    f"In a playful, extremely encouraging, and kind tone, explain entirely in language '{lang}' that this topic is usually learned by older students, and suggest these age-appropriate alternatives: {', '.join(compatibility.suggested_topics)}.\n"
                    f"CRITICAL LANGUAGE RULE: Speak exclusively in language '{lang}'. Never mix in German or other language words, grade labels, or topic terms when language is '{lang}'.\n"
                    f"Ask them which of these cool topics they would like to do instead, or if they want to choose a different grade/topic. Keep the response short, clear, and full of positive energy!"
                )
                mascot_resp = await client.aio.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=f"Playful mascot explanation in language '{lang}' to child why '{topic}' is not suitable for {grade_label} and suggest: {', '.join(compatibility.suggested_topics)}",
                    config=types.GenerateContentConfig(
                        system_instruction=mascot_prompt,
                        temperature=0.7,
                    ),
                )
                record_token_usage(
                    ctx,
                    mascot_resp,
                    call_stage=CallStage.MASCOT_PROMPT,
                )
                msg_text = mascot_resp.text.strip()
                return Event(
                    content=types.Content(
                        role="model", parts=[types.Part.from_text(text=msg_text)]
                    ),
                    actions=EventActions(route="ask_more"),
                )
        except Exception:
            logger.error(
                "Upfront curriculum check failed. Blocking generation until "
                "the request can be evaluated."
            )
            unavailable_messages = {
                "de": "Ich konnte die Klassenstufe und das Thema gerade nicht zuverl\u00e4ssig pr\u00fcfen. Bitte versuche es gleich noch einmal.",
                "pt": "N\u00e3o consegui verificar com seguran\u00e7a o ano escolar e o tema agora. Tente novamente em instantes.",
                "en": "I could not reliably verify the grade and topic right now. Please try again shortly.",
            }
            return Event(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part.from_text(
                            text=unavailable_messages.get(
                                lang, unavailable_messages["en"]
                            )
                        )
                    ],
                ),
                actions=EventActions(route="ask_more"),
            )


@node
async def decision_and_search(ctx: Context, node_input: Any) -> Event:
    """Autonomous Curriculum Search Skill. Dynamically gathers actual curriculum standards and facts from Wikipedia."""
    subject = ctx.state.get("subject")
    topic = ctx.state.get("topic")
    lang = ctx.state.get("preferred_language") or "en"

    # Optimization: if search_context is already present in state, reuse it to avoid duplicate network queries.
    if "search_context" in ctx.state:
        logger.info(
            "Search context already present in session state, skipping Wikipedia query."
        )
        return _workflow_event()

    logger.info("Curriculum Search Skill invoked.")
    search_query = f"{subject} {topic}"
    wikipedia_data = search_wikipedia(search_query, lang=lang, topic=topic)
    title_match = re.match(
        r"Grounding facts from Wikipedia page '([^']+)':", wikipedia_data
    )

    ctx.state["search_context"] = wikipedia_data
    ctx.state["grounding_title"] = title_match.group(1) if title_match else None
    ctx.state["grounding_discarded"] = not bool(wikipedia_data)
    return _workflow_event()


MAX_DETERMINISTIC_REPAIR_ATTEMPTS = 1
MAX_ACADEMIC_REPAIR_ATTEMPTS = 1
_HARD_DIFFICULTY_SELECTION = "hard"
_DETERMINISTIC_REPAIR_KIND = "deterministic"
_ACADEMIC_REPAIR_KIND = "academic"
_ACADEMIC_TARGETED_REPAIR_KIND = "academic_targeted"
_REPAIR_FAILURE_STATE_KEY = "repair_failure"

_LOCAL_JUDGE_ISSUE_CODES = frozenset(
    {
        JudgeIssueCode.FACTUAL_ERROR,
        JudgeIssueCode.CORRECT_ANSWER_MISMATCH,
        JudgeIssueCode.NEGATIVE_QUESTION,
        JudgeIssueCode.EMOJI_IN_QUESTION,
        JudgeIssueCode.EXPLANATION_ERROR,
    }
)
_GLOBAL_JUDGE_ISSUE_CODES = frozenset(
    {
        JudgeIssueCode.GRADE_SCOPE_VIOLATION,
        JudgeIssueCode.DIFFICULTY_MISMATCH,
        JudgeIssueCode.LANGUAGE_MISMATCH,
        JudgeIssueCode.TASK_VARIETY_FAILURE,
    }
)


def _judge_route(assessment: JudgeAssessment) -> str:
    """Select a repair route without interpreting free text."""
    if assessment.passed:
        return "success"

    for issue in assessment.issues:
        if issue.code in _GLOBAL_JUDGE_ISSUE_CODES:
            return "full_regeneration"
        if issue.code not in _LOCAL_JUDGE_ISSUE_CODES:
            return "full_regeneration"
        if (
            not issue.question_indices
            or len(issue.question_indices) != len(set(issue.question_indices))
            or any(index < 0 or index >= 10 for index in issue.question_indices)
        ):
            return "full_regeneration"
    return "targeted"


def _judge_issue_history_entry(
    *, assessment: JudgeAssessment, attempt: int, selected_route: str
) -> dict[str, Any]:
    """Build a privacy-safe Judge history entry."""
    return {
        "attempt": attempt,
        "passed": assessment.passed,
        "issue_codes": sorted({issue.code.value for issue in assessment.issues}),
        "question_indices": sorted(
            {
                index
                for issue in assessment.issues
                for index in issue.question_indices
                if isinstance(index, int)
                and not isinstance(index, bool)
                and 0 <= index < 10
            }
        ),
        "selected_route": selected_route,
    }


def _append_judge_history(
    ctx: Context, *, assessment: JudgeAssessment, attempt: int, selected_route: str
) -> None:
    history = list(ctx.state.get("judge_history") or [])
    history.append(
        _judge_issue_history_entry(
            assessment=assessment,
            attempt=attempt,
            selected_route=selected_route,
        )
    )
    ctx.state["judge_history"] = history


def _append_repair_history(
    ctx: Context,
    *,
    attempt: int,
    kind: str,
    issue_codes: list[str],
    question_indices: list[int],
    result: str,
) -> None:
    history = list(ctx.state.get("repair_history") or [])
    history.append(
        {
            "attempt": attempt,
            "kind": kind,
            "issue_codes": sorted(set(issue_codes)),
            "question_indices": sorted(
                {
                    index
                    for index in question_indices
                    if isinstance(index, int)
                    and not isinstance(index, bool)
                    and 0 <= index < 10
                }
            ),
            "result": result,
        }
    )
    ctx.state["repair_history"] = history


def _expected_quiz_difficulty(
    previous_score: int | None,
    selected_difficulty: str | None,
) -> DifficultyLevel:
    """Return the authoritative adaptive difficulty level for a quiz request."""
    if previous_score is not None and previous_score <= 3:
        return DifficultyLevel.EASY
    if previous_score is not None and previous_score >= 8:
        normalized_selection = (
            selected_difficulty.strip().casefold()
            if isinstance(selected_difficulty, str)
            else ""
        )
        if normalized_selection == _HARD_DIFFICULTY_SELECTION:
            return DifficultyLevel.HARD
        return (
            DifficultyLevel.HARD
            if previous_score == 10 and not normalized_selection
            else DifficultyLevel.MEDIUM
        )
    return DifficultyLevel.MEDIUM


def _adaptive_mode(previous_score: int | None, selected_difficulty: str | None) -> str:
    """Resolve the request's generation mode before building its prompt."""
    if previous_score is None:
        return "initial"
    if previous_score <= 3:
        return "reinforcement"
    if previous_score < 8:
        return "practice"
    return (
        "challenge"
        if _expected_quiz_difficulty(previous_score, selected_difficulty)
        == DifficultyLevel.HARD
        else "progression"
    )


def _build_adaptation_instructions(
    mode: str,
    previous_score: int | None,
    grade: int,
    previous_questions: list[str] | None,
) -> str:
    adaptation_instructions = ""
    if mode != "initial":
        if mode == "reinforcement":
            adaptation_instructions = (
                "\n--- ADAPTIVE REINFORCEMENT MODE ---\n"
                f"The student scored {previous_score}/10 on the previous quiz.\n"
                "No trusted server-side previous quiz was available for direct "
                "reinforcement reuse. Generate a complete Easy quiz and keep the "
                "questions clear and within the requested scope.\n"
                f"Set the 'difficulty' field to exactly: '{DifficultyLevel.EASY.value}'.\n"
            )
        elif mode in {"progression", "challenge"}:
            # Score >= 8/10: User-Choice Progression Mode (choose between Medium and Hard)
            if mode == "challenge":
                adaptation_instructions = (
                    f"\n--- ADAPTIVE PROGRESSION MODE (CHALLENGE) ---\n"
                    f"The student scored {previous_score}/10 on the previous quiz and selected the DIFFICULT (Advanced) level.\n"
                    f"You must significantly SCALE UP the cognitive depth of this new quiz while staying inside Grade {grade}. Use varied reasoning, application, strategy, estimation, comparison, or error-analysis tasks when they fit the topic. Do not create difficulty mainly through larger numbers, calculator-like manual work, or tightly clustered answer choices.\n"
                    f"Set the 'difficulty' field to exactly: '{DifficultyLevel.HARD.value}'.\n"
                )
            else:
                adaptation_instructions = (
                    f"\n--- ADAPTIVE PROGRESSION MODE (NEXT LEVEL) ---\n"
                    f"The student scored {previous_score}/10 on the previous quiz and selected the MEDIUM (Standard) level.\n"
                    f"Maintain standard Grade {grade} difficulty, but generate a completely fresh set of questions.\n"
                    f"Set the 'difficulty' field to exactly: '{DifficultyLevel.MEDIUM.value}'.\n"
                )

            # Strict Avoid Duplication rules
            adaptation_instructions += (
                f"\nCRITICAL COMPLIANCE RULES:\n"
                f"1. You MUST STRICTLY AVOID duplicating any previously asked questions to encourage learning progression.\n"
                f"2. Compare your new questions with the previous questions. Do not generate questions that are similar or duplicate the old ones.\n"
            )
            if previous_questions:
                adaptation_instructions += (
                    f"Do NOT use any of these questions from the previous quiz:\n"
                    + "\n".join(f"- {q}" for q in previous_questions)
                    + "\n"
                )
        else:
            # Score 4 to 7: Practice Mode (Medium)
            # Keep standard difficulty, generate a new set of questions.
            adaptation_instructions = (
                f"\n--- STANDARD PRACTICE MODE ---\n"
                f"The student scored {previous_score}/10 on the previous quiz.\n"
                f"Keep standard difficulty for Grade {grade}. Generate a new set of questions to continue practice on the topic.\n"
                f"Set the 'difficulty' field to exactly: '{DifficultyLevel.MEDIUM.value}'.\n"
                f"Note: It is fine to reuse some questions or concepts if they are central, as duplication avoidance is not strictly enforced for scores below 8/10.\n"
            )
    else:
        # First time quiz generation or no score available:
        # Set difficulty to 'medium'
        adaptation_instructions = f"\nSet the 'difficulty' field to exactly: '{DifficultyLevel.MEDIUM.value}'.\n"

    return adaptation_instructions


def _build_difficulty_design_guidance(
    expected_difficulty: DifficultyLevel | str,
) -> str:
    """Define varied, age-appropriate challenge without rewarding busywork."""
    common = (
        "Use varied cognitive task forms that fit the subject instead of repeating "
        "one question template with different facts or numbers. All ten questions "
        "must still use the required multiple-choice schema; variety refers to the "
        "cognitive demand and problem pattern, not a different response format. "
        "Distractors should "
        "represent different plausible misconceptions; for numeric answers, do not "
        "create difficulty only by clustering every option around the correct value. "
    )
    diff = (
        DifficultyLevel.from_raw(expected_difficulty)
        if not isinstance(expected_difficulty, DifficultyLevel)
        else expected_difficulty
    )
    if diff == DifficultyLevel.EASY:
        return common + (
            "Keep questions short, concrete, mostly one-step, and focused on core "
            "understanding. Keep arithmetic and reading load manageable, and avoid "
            "unnecessarily large numbers. Reinforcement may reuse prior concepts, "
            "so clarity matters more than novelty."
        )
    if diff == DifficultyLevel.HARD:
        return common + (
            "Create challenge through deeper reasoning while remaining strictly "
            "inside the requested grade. When the topic permits, use at least four "
            "meaningfully different task forms across the ten questions, such as "
            "application, multi-step reasoning, estimation or reasonableness, "
            "strategy choice, comparison, and error analysis. For mathematics or "
            "other quantitative topics, use at most two pure long-form exact "
            "calculations when conceptual alternatives exist. Do not open with an "
            "unusually laborious calculation, require calculator-like busywork, "
            "move into a higher-grade curriculum, or simulate difficulty merely "
            "with larger operands and tightly clustered numeric distractors. Do not "
            "manufacture task forms that do not naturally fit a narrow topic."
        )
    return common + (
        "Provide a balanced standard-grade mix of recall, understanding, application, "
        "and reasoning. When the topic permits, use at least four meaningfully "
        "different task forms across the ten questions. For mathematics or other "
        "quantitative topics, balance computation with estimation, strategy, and "
        "short applications, and keep manual calculation proportionate to the "
        "learning objective. For a narrow topic, use the strongest natural variety "
        "available rather than forcing artificial task forms."
    )


def _build_judge_prompt(
    *,
    quiz_dict: dict[str, Any],
    grade: Any,
    subject: Any,
    topic: Any,
    curriculum_guidance: str,
    previous_score: int | None,
    selected_difficulty: str | None,
    repair_history: list[dict[str, Any]] | None = None,
) -> str:
    """Build the academic-review contract shared with the LLM judge."""
    expected_difficulty = _expected_quiz_difficulty(previous_score, selected_difficulty)
    difficulty_design_guidance = _build_difficulty_design_guidance(expected_difficulty)
    grade_policy = get_grade_policy(grade)
    judge_quiz_dict = {key: value for key, value in quiz_dict.items() if key != "title"}
    grade_guidance = build_grade_prompt_guidance(grade_policy)
    repair_history_guidance = ""
    if repair_history:
        repair_history_guidance = (
            "\n--- PRIOR REPAIR HISTORY ---\n"
            "Earlier candidates required these bounded corrections; review the "
            "complete current quiz and do not reintroduce them. Review the complete "
            "current quiz before deciding:\n"
            f"{json.dumps(repair_history, ensure_ascii=False, sort_keys=True)}\n"
        )
    exact_primary_constraints = ""
    if grade_policy.stage is PedagogicalStage.PRIMARY_EARLY:
        exact_primary_constraints = (
            "For Grades 1-2, exactly three options and no more than two short "
            "sentences in every explanation are hard acceptance requirements, "
            "not minor style preferences. Set passed to false if either is "
            "violated.\n"
        )
    return (
        "You are a strict, professional school academic reviewer (LLM-as-a-judge).\n"
        "Assess if the following generated quiz JSON satisfies all standards:\n"
        f"1. Is the difficulty and content exactly aligned with school standards for Grade {int(grade_policy.grade)}?\n"
        f"2. Does it cover the subject '{subject}' and topic '{topic}' accurately?\n"
        "3. Does the quiz contain exactly 10 questions?\n"
        f"4. Does each question contain {grade_policy.option_count_instruction}, with exactly ONE correct choice?\n"
        "5. Is the 'correct_option_index' mathematically and factually correct? "
        "CRITICAL: For each question, you MUST independently determine the factually correct answer (whether it is a mathematical calculation, a historical date, a biological definition, etc.). Then, verify that the 'correct_option_index' points EXACTLY to that correct answer inside the 0-based options array. "
        "If there is any mismatch between the factually correct answer, the option at 'correct_option_index', or the correct answer described in your explanation, you MUST set passed to false.\n"
        "6. Are all answer options neutral and free of emojis or visual correctness cues, and are question texts completely emoji-free? If not, you MUST set passed to false. Ignore emojis in the quiz title and explanations; those fields are allowed and must not produce an emoji_in_question issue.\n"
        "For every rejected quiz, return one structured issue for each material defect. Use a valid 0-based question_indices list for local defects when possible. Use an empty list for quiz-wide defects. Never rely on the summary or issue explanations to communicate routing metadata.\n"
        "Return structured JSON matching JudgeAssessment with passed, summary, and issues.\n\n"
        "--- AUTHORITATIVE AGE-APPROPRIATE DESIGN CONTRACT ---\n"
        f"{grade_guidance}\n"
        f"{exact_primary_constraints}"
        "Reject the quiz when it materially violates this contract.\n\n"
        "--- AUTHORITATIVE ADAPTIVE DIFFICULTY CONTRACT ---\n"
        f"The expected difficulty field is exactly '{expected_difficulty.value}'.\n"
        f"Previous score: {previous_score if previous_score is not None else 'not available'}/10.\n"
        f"User-selected progression difficulty: {selected_difficulty or 'not selected'}.\n"
        "Difficulty labels are relative to the requested grade, never permission to use content from a higher grade. "
        "In particular, 'hard' means a deeper, more demanding challenge for a high-achieving student within the authoritative curriculum scope for the requested grade. "
        "Do not reject a quiz merely because 'hard' is used for a younger grade when that is the expected user-selected label. "
        "Instead, verify that its content is meaningfully challenging while remaining age-appropriate and inside the supplied grade-level scope. "
        "Reject when the label differs from the expected label, when the content is too easy for the selected mode, or when it exceeds or contradicts the grade-level scope.\n\n"
        "Apply the following task-design contract as a required quality criterion, but reject a quiz that materially violates it only when the topic naturally supports additional distinct cognitive forms:\n"
        f"{difficulty_design_guidance}\n\n"
        "Task variety is not a rigid numeric minimum. Only report task_variety_failure when the topic naturally supports additional distinct cognitive forms and the quiz materially repeats one form. Do not reject a narrow topic solely because it has fewer than four forms, and do not require artificial questions outside the requested scope.\n\n"
        "The upfront curriculum evaluator supplied this authoritative grade-level scope. The quiz must comply with it:\n"
        f"{curriculum_guidance or 'No additional scope guidance was available.'}\n\n"
        f"{repair_history_guidance}"
        "The quiz title is presentation-only and is intentionally omitted from this review payload.\n"
        f"Quiz JSON (questions and reviewable metadata only):\n{json.dumps(judge_quiz_dict)}\n"
    )


def _duplicate_option_question_indices(issues: Any) -> tuple[int, ...]:
    """Return affected questions only when every issue is a duplicate option."""
    if not isinstance(issues, list) or not issues:
        return ()

    question_indices: set[int] = set()
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("code") != "duplicate_option":
            return ()
        question_index = issue.get("question_index")
        if isinstance(question_index, bool) or not isinstance(question_index, int):
            return ()
        question_indices.add(question_index)
    return tuple(sorted(question_indices))


class TargetedRepairError(ValueError):
    """Raised when a targeted repair response cannot be safely assembled."""


async def _repair_targeted_questions(
    *,
    ctx: Context,
    quiz_dict: dict[str, Any],
    question_indices: tuple[int, ...],
    issue_records: list[dict[str, Any]],
    generation_attempt: int,
) -> dict[str, Any]:
    """Regenerate and normalize only the requested complete questions."""
    questions = quiz_dict.get("questions")
    if not isinstance(questions, list) or any(
        index < 0 or index >= len(questions) for index in question_indices
    ):
        raise TargetedRepairError(
            "Targeted repair requested an invalid question index."
        )

    repair_input = [
        {
            "question_index": index,
            "question": questions[index],
            "issues": [
                issue
                for issue in issue_records
                if index
                in (issue.get("question_indices") or [issue.get("question_index")])
            ],
        }
        for index in question_indices
    ]
    unaffected_questions = [
        {"question_index": index, "question": question.get("question")}
        for index, question in enumerate(questions)
        if index not in question_indices
        and isinstance(question, dict)
        and isinstance(question.get("question"), str)
    ]
    grade_policy = get_grade_policy(ctx.state.get("grade"))
    lang = ctx.state.get("preferred_language") or "en"
    expected_difficulty = _expected_quiz_difficulty(
        ctx.state.get("previous_score"), ctx.state.get("selected_difficulty")
    )
    issue_json = json.dumps(issue_records, ensure_ascii=False, sort_keys=True)
    prompt = (
        "Repair only the supplied quiz questions. Return one complete generated "
        "question for every supplied 0-based question_index and no other indices. "
        "Each response question must contain question, options, correct_answer, "
        "and explanation. The correct_answer must exactly identify one option "
        "after Unicode normalization and whitespace collapsing; application code "
        "will derive the public shuffled index. Use "
        f"{grade_policy.option_count_instruction}. Every "
        "option must be meaningfully distinct after Unicode normalization, trimming "
        "or collapsing whitespace while preserving meaningful capitalization, "
        "such as genotype notation. Never use emojis in question text or answer "
        "options. Preserve the requested language, grade level, subject, topic, "
        "and expected difficulty. Correct every supplied issue and do not make "
        "unrequested changes to the unaffected questions.\n\n"
        f"Target language: {lang}\n"
        f"Grade: {ctx.state.get('grade')}\n"
        f"Subject: {ctx.state.get('subject')}\n"
        f"Topic: {ctx.state.get('topic')}\n"
        f"Expected difficulty: {expected_difficulty.value}\n"
        f"Previous score: {ctx.state.get('previous_score', 'not available')}\n"
        f"Selected progression difficulty: {ctx.state.get('selected_difficulty') or 'not selected'}\n"
        f"Curriculum guidance:\n{ctx.state.get('curriculum_guidance') or 'None available.'}\n"
        f"Relevant grounding context:\n{ctx.state.get('search_context') or 'None available.'}\n"
        f"General generation rules:\n{build_grade_prompt_guidance(grade_policy)}\n"
        f"{_build_difficulty_design_guidance(expected_difficulty)}\n"
        f"Structured issues:\n{issue_json}\n"
        f"Questions to repair:\n{json.dumps(repair_input, ensure_ascii=False)}\n"
        f"Unaffected question texts for duplicate prevention only:\n"
        f"{json.dumps(unaffected_questions, ensure_ascii=False)}"
    )

    response = await Client().aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=GeneratedQuestionRepairResponse,
            temperature=0.2,
        ),
    )
    record_token_usage(
        ctx,
        response,
        call_stage=CallStage.QUIZ_GENERATOR,
        generation_attempt=generation_attempt,
    )
    repairs = GeneratedQuestionRepairResponse.model_validate_json(response.text.strip())
    returned_indices = [repair.question_index for repair in repairs.repairs]
    requested_indices = set(question_indices)
    if (
        len(returned_indices) != len(question_indices)
        or len(set(returned_indices)) != len(returned_indices)
        or set(returned_indices) != requested_indices
    ):
        raise TargetedRepairError(
            "Targeted repair response did not contain exactly the requested indices."
        )

    repaired_quiz = json.loads(json.dumps(quiz_dict))
    for repair in repairs.repairs:
        index = repair.question_index
        repaired_question = normalize_generated_question(
            repair.model_dump(exclude={"question_index"}),
            grade=ctx.state.get("grade"),
            question_index=index,
        )
        repaired_quiz["questions"][index] = repaired_question
    logger.info(
        "Applied targeted question repairs to %s question(s).",
        len(repairs.repairs),
    )
    return repaired_quiz


@dataclass(frozen=True)
class TargetedRepairPlan:
    """A local correction with explicit source and failure attribution."""

    source: str
    question_indices: tuple[int, ...]
    issues: list[dict[str, Any]]
    failure_type: str


def _targeted_repair_plan(
    ctx: Context, repair_kind: str | None
) -> TargetedRepairPlan | None:
    if repair_kind == _DETERMINISTIC_REPAIR_KIND:
        issues = list(ctx.state.get("deterministic_validation_issues") or [])
        indices = _duplicate_option_question_indices(issues)
        if not indices or not isinstance(ctx.state.get("temp_quiz"), dict):
            return None  # Structural issues without a usable local repair regenerate.
        return TargetedRepairPlan(
            repair_kind, indices, issues, "deterministic_validation_failed"
        )
    if repair_kind == _ACADEMIC_TARGETED_REPAIR_KIND:
        issues = list(ctx.state.get("judge_issues") or [])
        indices = tuple(
            sorted(
                {
                    index
                    for issue in issues
                    for index in (issue.get("question_indices") or [])
                    if isinstance(index, int) and not isinstance(index, bool)
                }
            )
        )
        return TargetedRepairPlan(repair_kind, indices, issues, "judge_rejected")
    return None


async def _execute_targeted_repair(
    ctx: Context,
    repair: TargetedRepairPlan,
    candidate: Any,
    attempt: int,
    difficulty: str,
) -> Event:
    result = "failed"
    try:
        if not repair.question_indices or not isinstance(candidate, dict):
            raise TargetedRepairError(
                "Targeted repair had no usable candidate or indices."
            )
        repaired = await _repair_targeted_questions(
            ctx=ctx,
            quiz_dict=candidate,
            question_indices=repair.question_indices,
            issue_records=repair.issues,
            generation_attempt=attempt,
        )
        repaired["difficulty"] = (
            difficulty.value if isinstance(difficulty, DifficultyLevel) else difficulty
        )
        ctx.state["temp_quiz"] = repaired
        result = "applied"
    except Exception as error:
        ctx.state[_REPAIR_FAILURE_STATE_KEY] = "targeted_repair_failed"
        ctx.state["quality_failure_type"] = repair.failure_type
        ctx.state["temp_quiz"] = None
        logger.error(
            "%s targeted repair failed (%s).", repair.source, type(error).__name__
        )
    _append_repair_history(
        ctx,
        attempt=attempt,
        kind="targeted",
        issue_codes=[str(issue.get("code")) for issue in repair.issues],
        question_indices=list(repair.question_indices),
        result=result,
    )
    return _candidate_ready_event()


def _normalization_retry_guidance(error: QuizNormalizationError) -> str:
    """Build privacy-safe retry guidance for an internal answer failure."""
    location = (
        f"Question {error.question_index + 1}: "
        if error.question_index is not None
        else ""
    )
    return (
        "The previous generated candidate failed answer normalization.\n"
        f"- {location}{error.code.value.replace('_', ' ')}.\n"
        "Return a correct_answer that matches exactly one option after Unicode "
        "normalization and whitespace collapsing. Do not return a correct option "
        "index. Regenerate the complete quiz."
    )


def _begin_generation_attempt(ctx: Context) -> tuple[int, str | None]:
    """Consume the pending repair allowance once, at generation entry."""
    attempt = int(ctx.state.get("generation_attempts") or 0) + 1
    ctx.state["generation_attempts"] = attempt
    repair_kind = ctx.state.get("pending_quiz_repair_kind")
    if repair_kind == _DETERMINISTIC_REPAIR_KIND:
        ctx.state["deterministic_repair_attempts"] = (
            int(ctx.state.get("deterministic_repair_attempts") or 0) + 1
        )
    elif repair_kind in {_ACADEMIC_REPAIR_KIND, _ACADEMIC_TARGETED_REPAIR_KIND}:
        ctx.state["academic_repair_attempts"] = (
            int(ctx.state.get("academic_repair_attempts") or 0) + 1
        )
    ctx.state["pending_quiz_repair_kind"] = None

    return attempt, repair_kind


@node
async def quiz_generation(ctx: Context, node_input: Any) -> Event:
    """Uses LLM structured generation to build a highly tailored, fun multiple-choice quiz of 10 questions."""
    grade = ctx.state.get("grade")
    subject = ctx.state.get("subject")
    topic = ctx.state.get("topic")
    lang = ctx.state.get("preferred_language") or "en"
    search_context = ctx.state.get("search_context", "")
    attempt, repair_kind = _begin_generation_attempt(ctx)

    previous_score = ctx.state.get("previous_score")
    # Only server-loaded question text may be used for duplicate prevention.
    # Gather-and-route clears client-provided values, and this guard keeps the
    # generation node safe when exercised independently in tests or tools.
    previous_questions = (
        ctx.state.get("previous_questions")
        if isinstance(ctx.state.get("validated_quiz_source_id"), str)
        else None
    )
    selected_difficulty = ctx.state.get("selected_difficulty")
    mode = _adaptive_mode(previous_score, selected_difficulty)
    expected_difficulty = _expected_quiz_difficulty(previous_score, selected_difficulty)
    difficulty_design_guidance = _build_difficulty_design_guidance(expected_difficulty)
    grade_policy = get_grade_policy(grade)
    grade_guidance = build_grade_prompt_guidance(grade_policy)
    emoji_guidance = (
        "Decorative emojis may appear in titles or explanations, but never in "
        "question text or answer options."
    )
    question_emoji_rule = "Do not use any emoji in question text."
    explanation_length_rule = (
        "For Grades 1-2, every explanation must contain no more than two short "
        "sentences."
        if grade_policy.maximum_explanation_sentences == 2
        else "Keep each explanation concise and appropriate for the selected grade."
    )
    curriculum_guidance = ctx.state.get("curriculum_guidance", "")
    judge_issues = list(ctx.state.get("judge_issues") or [])
    judge_summary = ctx.state.get("judge_summary") or ""
    deterministic_retry_guidance = ctx.state.get("deterministic_retry_guidance", "")
    repair_history = list(ctx.state.get("repair_history") or [])

    logger.info("Generating quiz attempt %s.", attempt)

    previous_candidate = ctx.state.get("temp_quiz")

    if (
        repair_kind is None
        and mode == "reinforcement"
        and ctx.state.get("validated_quiz_bypass_allowed")
        and isinstance(ctx.state.get("authoritative_previous_quiz"), dict)
    ):
        reinforced_quiz = json.loads(
            json.dumps(ctx.state["authoritative_previous_quiz"], ensure_ascii=False)
        )
        shuffle_quiz_questions(reinforced_quiz)
        shuffle_quiz_options(reinforced_quiz)
        reinforced_quiz["difficulty"] = expected_difficulty.value
        ctx.state["temp_quiz"] = reinforced_quiz
        logger.info("Reusing and shuffling the server-validated reinforcement quiz.")
        return _candidate_ready_event()

    repair = _targeted_repair_plan(ctx, repair_kind)
    if repair is not None:
        return await _execute_targeted_repair(
            ctx, repair, previous_candidate, attempt, expected_difficulty
        )

    grade_label = grade_policy.localized_label(lang)
    lang_name = {
        "de": "Deutsch",
        "pt": "Português",
        "en": "English",
    }.get(lang, "English")
    prompt = (
        f"Create an interactive multiple-choice quiz with exactly 10 questions.\n"
        f"Target Audience: School students in {grade_label} (Grade {int(grade_policy.grade)}).\n"
        f"Subject: {subject}\n"
        f"Topic: {topic}\n"
        f"Preferred Language: Entire quiz MUST be written strictly in {lang_name} ('{lang}').\n"
        "\n--- AUTHORITATIVE AGE-APPROPRIATE DESIGN CONTRACT ---\n"
        f"{grade_guidance}\n"
        f"{emoji_guidance}\n"
    )

    prompt += (
        "\n--- REQUIRED DIFFICULTY AND TASK-DESIGN CONTRACT ---\n"
        f"{difficulty_design_guidance}\n"
        "Follow this contract across the complete quiz; it is part of the acceptance criteria.\n"
    )

    if curriculum_guidance:
        prompt += (
            "\n--- AUTHORITATIVE CURRICULUM SCOPE ---\n"
            f"{curriculum_guidance}\n"
            "Every question must follow this grade-level scope. Do not replace it with a simpler interpretation of the topic.\n"
        )

    if judge_issues or judge_summary:
        prompt += (
            "\n--- REQUIRED RETRY CORRECTION ---\n"
            f"The previous academic review summary was: {judge_summary}\n"
            f"Structured academic issues:\n{json.dumps(judge_issues, ensure_ascii=False, sort_keys=True)}\n"
            "Generate a materially corrected quiz that resolves this feedback. Do not repeat the rejected difficulty, scope, or factual issue.\n"
        )

    if deterministic_retry_guidance:
        prompt += (
            "\n--- REQUIRED STRUCTURAL CORRECTION ---\n"
            f"{deterministic_retry_guidance}\n"
            "Correct every listed problem before returning the complete quiz.\n"
        )

    if repair_history:
        prompt += (
            "\n--- PRIOR REPAIR HISTORY ---\n"
            "Earlier candidates required the following bounded corrections:\n"
            f"{json.dumps(repair_history, ensure_ascii=False, sort_keys=True)}\n"
            "Do not reintroduce these defect types, especially at the listed "
            "0-based question indices.\n"
        )

    if search_context:
        prompt += (
            "\nThe requested Subject and Topic above are authoritative. Never replace "
            "them with a different subject or topic from the reference material. Use only "
            f"directly relevant facts from this Wikipedia grounding:\n{search_context}\n"
        )

    prompt += (
        "\nRules & Schema requirements:\n"
        "1. Create exactly 10 questions.\n"
        f"2. Each question has {grade_policy.option_count_instruction}.\n"
        "3. EXACTLY one option must be correct.\n"
        "4. Every option within one question must be meaningfully distinct and unique after Unicode normalization and trimming or collapsing whitespace. Preserve capitalization when it carries scientific meaning, such as genotype notation. Before returning the JSON, compare every pair of options in each question and replace repeated or equivalent choices with genuinely different distractors.\n"
        "5. Set 'correct_answer' to the exact text of the one correct option. Do not return a correct_option_index; application code will normalize the answer and derive the public index after shuffling.\n"
        "6. Every answer option must be neutral, text-only content. Never put emojis, check marks, crosses, stars, labels such as 'correct', or any other visual answer cue in an option. "
        f"{question_emoji_rule}\n"
        "7. Keep the explanations warm, educational, clear, and highly encouraging (explain why the correct answer is right and why others are wrong in a child-friendly mascot way). "
        f"{explanation_length_rule} CRITICAL: Do NOT start explanations with affirmative or congratulatory words like 'Parabéns!', 'Isso mesmo!', 'Congratulations!', 'Exactly!', 'Herzlichen Glückwunsch!', or 'Richtig!', because these explanations are shown even when the student chooses the wrong answer. Start directly with the factual explanation (e.g. 'Células-tronco são...' instead of 'Isso mesmo! Células-tronco são...').\n"
    )

    adaptation_instructions = _build_adaptation_instructions(
        mode, previous_score, int(grade_policy.grade), previous_questions
    )

    prompt += adaptation_instructions

    client = Client()
    try:
        response = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=GeneratedQuiz,
                temperature=0.6,
            ),
        )
        record_token_usage(
            ctx,
            response,
            call_stage=CallStage.QUIZ_GENERATOR,
            generation_attempt=attempt,
        )
        generated_quiz = GeneratedQuiz.model_validate_json(response.text.strip())
        quiz_dict = normalize_generated_quiz(
            generated_quiz,
            grade=grade,
        )
        # Keep user-visible metadata deterministic and consistent with the
        # adaptive mode reviewed by the academic judge.
        quiz_dict["difficulty"] = expected_difficulty.value
        ctx.state["temp_quiz"] = quiz_dict
        if repair_kind in {_DETERMINISTIC_REPAIR_KIND, _ACADEMIC_REPAIR_KIND}:
            issue_records = (
                ctx.state.get("deterministic_validation_issues") or []
                if repair_kind == _DETERMINISTIC_REPAIR_KIND
                else judge_issues
            )
            _append_repair_history(
                ctx,
                attempt=attempt,
                kind="full_regeneration",
                issue_codes=[str(issue.get("code")) for issue in issue_records],
                question_indices=[
                    index
                    for issue in issue_records
                    for index in (
                        issue.get("question_indices") or [issue.get("question_index")]
                    )
                    if isinstance(index, int) and not isinstance(index, bool)
                ],
                result="applied",
            )
        return _candidate_ready_event()
    except QuizNormalizationError as e:
        failures = list(ctx.state.get("normalization_failures") or [])
        failures.append(e.as_dict())
        ctx.state["normalization_failures"] = failures
        ctx.state["deterministic_retry_guidance"] = _normalization_retry_guidance(e)
        ctx.state["temp_quiz"] = None
        if repair_kind in {_ACADEMIC_TARGETED_REPAIR_KIND}:
            ctx.state[_REPAIR_FAILURE_STATE_KEY] = "targeted_repair_failed"
        logger.warning("Generated quiz normalization failed (%s).", e.code.value)
        return _candidate_ready_event()
    except Exception as e:
        failures = list(ctx.state.get("normalization_failures") or [])
        failures.append({"code": QuizNormalizationCode.INVALID_GENERATED_QUIZ.value})
        ctx.state["normalization_failures"] = failures
        ctx.state["deterministic_retry_guidance"] = (
            "The previous model response could not be normalized into the required "
            "quiz schema. Return exactly 10 questions with a correct_answer matching "
            "exactly one option in every question."
        )
        ctx.state["temp_quiz"] = None
        logger.error("Quiz generation failed (%s).", type(e).__name__)
        return _candidate_ready_event()


def _candidate_ready_event() -> Event:
    """Signal the judge without exposing unvalidated quiz JSON to clients."""
    # A non-empty output traverses the unconditional workflow edge
    # Edge(from_node=quiz_generation, to_node=llm_as_a_judge).
    return _workflow_event(output={"status": "candidate_ready"})


def _route_after_failed_deterministic_validation(
    deterministic_repair_attempts: int,
) -> str:
    """Allow at most one correction for deterministic validation defects."""
    return (
        "quality_failure"
        if deterministic_repair_attempts >= MAX_DETERMINISTIC_REPAIR_ATTEMPTS
        else "retry"
    )


def _route_after_failed_judge(academic_repair_attempts: int) -> str:
    """Allow at most one full regeneration after academic rejection."""
    return (
        "quality_failure"
        if academic_repair_attempts >= MAX_ACADEMIC_REPAIR_ATTEMPTS
        else "retry"
    )


@node
async def deterministic_quiz_validation(ctx: Context, node_input: Any) -> Event:
    """Reject structural defects and answer cues before the expensive LLM judge."""
    if ctx.state.get(_REPAIR_FAILURE_STATE_KEY):
        result = validate_quiz_candidate(None, grade=ctx.state.get("grade"))
        ctx.state["deterministic_validation_issues"] = [
            issue.as_dict() for issue in result.issues
        ]
        emit_quiz_validation_event(
            event="quiz_validation_retry_exhausted",
            generation_attempt=int(ctx.state.get("generation_attempts") or 0),
            result=result,
        )
        return _workflow_event(route="quality_failure")

    result = validate_quiz_candidate(
        ctx.state.get("temp_quiz"), grade=ctx.state.get("grade")
    )
    ctx.state["deterministic_validation_issues"] = [
        issue.as_dict() for issue in result.issues
    ]
    generation_attempts = int(ctx.state.get("generation_attempts") or 0)
    if result.is_valid:
        emit_quiz_validation_event(
            event=(
                "quiz_validation_retry_passed"
                if generation_attempts > 1
                else "quiz_validation_passed"
            ),
            generation_attempt=generation_attempts,
            result=result,
        )
        ctx.state["deterministic_retry_guidance"] = ""
        return _workflow_event(route="valid")

    guidance = (
        ctx.state.get("deterministic_retry_guidance")
        if ctx.state.get("temp_quiz") is None
        else None
    ) or build_retry_guidance(result)
    ctx.state["deterministic_retry_guidance"] = guidance
    ctx.state["quality_failure_type"] = "deterministic_validation_failed"
    route = _route_after_failed_deterministic_validation(
        int(ctx.state.get("deterministic_repair_attempts") or 0)
    )
    ctx.state["pending_quiz_repair_kind"] = (
        _DETERMINISTIC_REPAIR_KIND if route == "retry" else None
    )
    emit_quiz_validation_event(
        event=(
            "quiz_validation_retry_exhausted"
            if route == "quality_failure"
            else "quiz_validation_failed"
        ),
        generation_attempt=generation_attempts,
        result=result,
    )
    logger.warning(
        "Deterministic quiz validation failed with %s issue(s). Routing to %s.",
        len(result.issues),
        route,
    )
    return _workflow_event(route=route)


@node
async def llm_as_a_judge(ctx: Context, node_input: Any) -> Event:
    """Review the complete assembled quiz and route structured failures safely."""
    quiz_dict = ctx.state.get("temp_quiz")

    if not quiz_dict:
        failure_route = _route_after_failed_judge(
            int(ctx.state.get("academic_repair_attempts") or 0)
        )
        ctx.state["pending_quiz_repair_kind"] = (
            _ACADEMIC_REPAIR_KIND if failure_route == "retry" else None
        )
        return _workflow_event(route=failure_route)

    previous_score = ctx.state.get("previous_score")
    if (
        previous_score is not None
        and previous_score <= 3
        and ctx.state.get("validated_quiz_bypass_allowed")
    ):
        logger.info(
            "Reinforcement mode: skipping Judge for current deterministic validation "
            "of a server-validated quiz."
        )
        return _workflow_event(route="success")

    attempts = int(ctx.state.get("judge_attempts") or 0) + 1
    ctx.state["judge_attempts"] = attempts

    grade = ctx.state.get("grade")
    subject = ctx.state.get("subject")
    topic = ctx.state.get("topic")
    curriculum_guidance = ctx.state.get("curriculum_guidance", "")

    judge_prompt = _build_judge_prompt(
        quiz_dict=quiz_dict,
        grade=grade,
        subject=subject,
        topic=topic,
        curriculum_guidance=curriculum_guidance,
        previous_score=previous_score,
        selected_difficulty=ctx.state.get("selected_difficulty"),
        repair_history=list(ctx.state.get("repair_history") or []),
    )

    client = Client()
    try:
        response = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=judge_prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=JudgeAssessment,
                temperature=0.1,
            ),
        )
        record_token_usage(
            ctx,
            response,
            call_stage=CallStage.ACADEMIC_JUDGE,
            judge_attempt=attempts,
        )
        assessment = JudgeAssessment.model_validate_json(response.text.strip())
        logger.info(
            "LLM Judge quality review attempt %s completed: passed=%s.",
            attempts,
            assessment.passed,
        )

        if assessment.passed:
            _append_judge_history(
                ctx,
                assessment=assessment,
                attempt=attempts,
                selected_route="success",
            )
            return _workflow_event(route="success")

        requested_route = _judge_route(assessment)
        failure_route = _route_after_failed_judge(
            int(ctx.state.get("academic_repair_attempts") or 0)
        )
        selected_route = (
            requested_route if failure_route == "retry" else "quality_failure"
        )
        _append_judge_history(
            ctx,
            assessment=assessment,
            attempt=attempts,
            selected_route=selected_route,
        )
        ctx.state["judge_summary"] = assessment.summary
        ctx.state["judge_issues"] = [
            issue.model_dump(mode="json") for issue in assessment.issues
        ]
        ctx.state["quality_failure_type"] = "judge_rejected"
        if failure_route == "retry":
            ctx.state["pending_quiz_repair_kind"] = (
                _ACADEMIC_TARGETED_REPAIR_KIND
                if requested_route == "targeted"
                else _ACADEMIC_REPAIR_KIND
            )
        else:
            ctx.state["pending_quiz_repair_kind"] = None
        logger.warning(
            "Judge failed validation. Requested route=%s, selected route=%s.",
            requested_route,
            selected_route,
        )
        return _workflow_event(route=failure_route)
    except Exception as e:
        history = list(ctx.state.get("judge_history") or [])
        history.append(
            {
                "attempt": attempts,
                "passed": False,
                "issue_codes": ["judge_exception"],
                "question_indices": [],
                "selected_route": "quality_failure",
            }
        )
        ctx.state["judge_history"] = history
        ctx.state["judge_summary"] = ""
        ctx.state["judge_issues"] = []
        ctx.state["quality_failure_type"] = "judge_exception"
        logger.error(
            "LLM Judge failed (%s). Blocking release of unvalidated quiz.",
            type(e).__name__,
        )
        return _workflow_event(route="quality_failure")


@node
async def quiz_output_node(ctx: Context, node_input: Any) -> Event:
    """Prepares and releases the validated quiz. Returns friendly message and frozen quiz JSON."""
    quiz_dict = ctx.state.get("temp_quiz")
    lang = ctx.state.get("preferred_language") or "en"

    final_validation = validate_quiz_candidate(quiz_dict, grade=ctx.state.get("grade"))
    if not final_validation.is_valid:
        ctx.state["deterministic_validation_issues"] = [
            issue.as_dict() for issue in final_validation.issues
        ]
        ctx.state["quality_failure_type"] = "final_invariant_failed"
        emit_quiz_validation_event(
            event="quiz_final_invariant_failed",
            generation_attempt=int(ctx.state.get("generation_attempts") or 0),
            result=final_validation,
        )
        logger.error("Final quiz invariant failed; blocking quiz output.")
        yield _quality_failure_event(ctx)
        return

    expected_difficulty = _expected_quiz_difficulty(
        ctx.state.get("previous_score"),
        ctx.state.get("selected_difficulty"),
    )
    if (
        not isinstance(quiz_dict, dict)
        or quiz_dict.get("difficulty") != expected_difficulty.value
    ):
        logger.error(
            "Quiz difficulty mismatch on output boundary: expected %s, got %s",
            expected_difficulty.value,
            quiz_dict.get("difficulty") if isinstance(quiz_dict, dict) else None,
        )
        ctx.state["quality_failure_type"] = "final_invariant_failed"
        yield _quality_failure_event(ctx)
        return

    try:
        validated_quiz_model = Quiz.model_validate(quiz_dict)
    except Exception as e:
        logger.error("Quiz schema validation failed on output boundary: %s", e)
        ctx.state["quality_failure_type"] = "final_invariant_failed"
        yield _quality_failure_event(ctx)
        return

    validated_quiz_id = _save_validated_quiz_best_effort(
        ctx, validated_quiz_model.model_dump(mode="json")
    )

    _reset_quiz_state(ctx, source_id=validated_quiz_id)

    logger.info("Finalizing validated quiz.")
    set_invocation_outcome(ctx, TerminalOutcome.SUCCESS)

    if lang == "de":
        msg = "🎉 **Dein personalisiertes Quiz ist fertig!**\n\nKlicke unten auf den Knopf, um loszulegen! Ich drücke dir ganz fest die Pfoten! ✨"
    elif lang == "pt":
        msg = "🎉 **Seu quiz personalizado está pronto!**\n\nClique no botão abaixo para começar a jogar! Boa sorte! ✨"
    else:
        msg = "🎉 **Your customized quiz is ready!**\n\nClick the button below to start solving! Good luck! ✨"

    # Stream friendly greeting to user chat
    yield Event(
        content=types.Content(role="model", parts=[types.Part.from_text(text=msg)])
    )

    # Return structured Quiz object as the workflow's terminal output
    yield _validated_quiz_event(
        validated_quiz_model,
        validated_quiz_id=validated_quiz_id,
    )


@node
async def ask_more_node(ctx: Context, node_input: Any) -> Event:
    """Terminal node for the 'ask_more' route. Gracefully ends the branch."""
    logger.info("Mascot prompt asking for more information.")
    set_invocation_outcome(ctx, TerminalOutcome.NEEDS_INPUT)
    return _workflow_event()


def _quality_usage_summary(ctx: Context) -> UsageSummary:
    """Build the bounded usage shape required by quality diagnostics."""
    usage = InvocationTokenUsage.from_state(ctx.state.get(_TOKEN_USAGE_STATE_KEY))
    summary = usage.as_summary_fields()
    return UsageSummary(
        model_call_count=summary["model_call_count"],
        prompt_token_count=summary["prompt_token_count"],
        candidate_token_count=summary["candidates_token_count"],
        thoughts_token_count=summary["thoughts_token_count"],
        total_token_count=summary["total_token_count"],
        stage_total_token_counts=summary["stage_total_token_counts"],
    )


def _quality_duration_ms(ctx: Context) -> int:
    """Return a bounded invocation duration for diagnostics."""
    started_at = ctx.state.get(_INVOCATION_START_STATE_KEY)
    if not isinstance(started_at, (int, float)):
        return 0
    return min(max(int((time.perf_counter() - started_at) * 1000), 0), 3_600_000)


def _save_validated_quiz_best_effort(
    ctx: Context, quiz_dict: dict[str, Any]
) -> str | None:
    """Persist approved quiz provenance without blocking approved output."""
    source_id = ctx.state.get("validated_quiz_source_id")
    if ctx.state.get("validated_quiz_bypass_allowed") and isinstance(source_id, str):
        return source_id

    build_info = get_build_info()
    try:
        return FirestoreRepository().save_validated_quiz(
            quiz_dict,
            QuizContext.from_state(ctx.state),
            validation_contract_version=VALIDATION_CONTRACT_VERSION,
            service_version=build_info["version"] or "dev",
        )
    except FirestorePersistenceError:
        logger.warning(
            "Could not persist validated quiz provenance; future reinforcement "
            "requests will use normal generation and Judge review."
        )
        return None


def _save_quality_failure_best_effort(failure: QuizQualityFailure) -> None:
    """Persist diagnostics without replacing the user-facing failure response."""
    try:
        FirestoreRepository().save_quiz_quality_failure(failure)
        logger.info("Saved quiz quality failure diagnostic.")
    except FirestorePersistenceError:
        logger.warning("Could not persist quiz quality failure diagnostic.")


def _build_quality_failure(ctx: Context) -> QuizQualityFailure:
    """Validate the bounded diagnostic record independently of error output."""
    build_info = get_build_info()
    failure_type = ctx.state.get("quality_failure_type")
    if failure_type not in {
        "deterministic_validation_failed",
        "final_invariant_failed",
        "judge_rejected",
        "judge_exception",
    }:
        failure_type = "judge_rejected"
    return QuizQualityFailure(
        quiz_context=QuizContext.from_state(ctx.state),
        failure_type=failure_type,
        generation_attempts=int(ctx.state.get("generation_attempts") or 0),
        judge_attempts=int(ctx.state.get("judge_attempts") or 0),
        academic_repair_attempts=int(ctx.state.get("academic_repair_attempts") or 0),
        deterministic_repair_attempts=int(
            ctx.state.get("deterministic_repair_attempts") or 0
        ),
        judge_history=list(ctx.state.get("judge_history") or []),
        repair_history=list(ctx.state.get("repair_history") or []),
        normalization_failures=list(ctx.state.get("normalization_failures") or []),
        usage_summary=_quality_usage_summary(ctx),
        duration_ms=_quality_duration_ms(ctx),
        service_version=build_info["version"] or "dev",
        deployment_revision=build_info["short_commit_sha"] or "dev",
        grounding_title=ctx.state.get("grounding_title"),
        grounding_discarded=bool(ctx.state.get("grounding_discarded", False)),
    )


def _quality_failure_event(ctx: Context) -> Event:
    """Persist diagnostics and build the localized fail-closed response."""
    set_invocation_outcome(ctx, TerminalOutcome.QUALITY_FAILURE)
    lang = ctx.state.get("preferred_language") or "en"
    try:
        _save_quality_failure_best_effort(_build_quality_failure(ctx))
    except Exception:
        # Diagnostics must never prevent the response or state cleanup. Do not
        # log exception details: validation errors can include generated content.
        logger.warning("Could not construct or persist quiz quality diagnostics.")

    ctx.state["temp_quiz"] = None
    _reset_quiz_state(ctx)

    messages = {
        "de": "Ich konnte dieses Quiz diesmal nicht zuverlässig prüfen. Bitte versuche es noch einmal – ich möchte dir nur ein fachlich passendes Quiz zeigen.",
        "pt": "Não consegui verificar este quiz com segurança desta vez. Tente novamente — quero mostrar apenas um quiz que corresponda ao seu tema.",
        "en": "I could not reliably verify this quiz this time. Please try again — I only want to show you a quiz that matches your topic.",
    }
    return Event(
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text=messages.get(lang, messages["en"]))],
        )
    )


@node
async def quality_failure_node(ctx: Context, node_input: Any) -> Event:
    """Fail closed when deterministic or LLM review cannot pass."""
    return _quality_failure_event(ctx)


@node
async def security_checkpoint_node(ctx: Context, node_input: Any) -> Event:
    """Route an expected plugin block away from every quiz-processing node."""
    route = "blocked" if ctx.state.get(SECURITY_BLOCK_STATE_KEY) else "allowed"
    ctx.state[_ALLOWED_INPUT_STATE_KEY] = (
        _text_from_node_input(node_input) if route == "allowed" else ""
    )
    return _workflow_event(route=route)


@node
async def security_block_node(ctx: Context, node_input: Any) -> Event:
    """Return the structured block envelope produced by the security plugin."""
    set_invocation_outcome(ctx, TerminalOutcome.BLOCKED)
    block_event = ctx.state.get(SECURITY_BLOCK_STATE_KEY)
    if not isinstance(block_event, dict):
        logger.error("Security block route reached without a block response.")
        block_event = {
            "status": "blocked",
            "block_type": "SECURITY_UNAVAILABLE",
            "message": "The safety check is temporarily unavailable. Please try again shortly.",
        }
    return Event(
        content=types.Content(
            role="model",
            parts=[
                types.Part.from_text(text=json.dumps(block_event, ensure_ascii=False))
            ],
        )
    )


# --- ADK 2.0 Workflow Definition ---

root_agent = Workflow(
    name="root_agent",
    description="Interactive School Exam Preparation Companion (FoxQuiz)",
    edges=[
        Edge(from_node=START, to_node=security_checkpoint_node),
        Edge(
            from_node=security_checkpoint_node,
            to_node=gather_and_route,
            route="allowed",
        ),
        Edge(
            from_node=security_checkpoint_node,
            to_node=security_block_node,
            route="blocked",
        ),
        Edge(
            from_node=gather_and_route,
            to_node=decision_and_search,
            route="generate_quiz",
        ),
        Edge(
            from_node=gather_and_route,
            to_node=ask_more_node,
            route="ask_more",
        ),
        Edge(from_node=decision_and_search, to_node=quiz_generation),
        Edge(from_node=quiz_generation, to_node=deterministic_quiz_validation),
        Edge(
            from_node=deterministic_quiz_validation,
            to_node=llm_as_a_judge,
            route="valid",
        ),
        Edge(
            from_node=deterministic_quiz_validation,
            to_node=quiz_generation,
            route="retry",
        ),
        Edge(
            from_node=deterministic_quiz_validation,
            to_node=quality_failure_node,
            route="quality_failure",
        ),
        Edge(from_node=llm_as_a_judge, to_node=quiz_generation, route="retry"),
        Edge(from_node=llm_as_a_judge, to_node=quiz_output_node, route="success"),
        Edge(
            from_node=llm_as_a_judge,
            to_node=quality_failure_node,
            route="quality_failure",
        ),
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
    plugins=[FoxQuizSecurityPlugin()],
)
