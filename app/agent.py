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
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

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
    SECURITY_BLOCK_STATE_KEY,
    record_token_usage,
    set_invocation_outcome,
)
from app.app_utils.operational_logging import emit_quiz_validation_event
from app.app_utils.request_context import get_client_locale
from app.app_utils.token_usage import CallStage, TerminalOutcome
from app.app_utils.typing import QuizContext, QuizQualityFailure
from app.database.firestore_repo import FirestorePersistenceError, FirestoreRepository
from app.domain.quiz_validation import (
    build_retry_guidance,
    validate_quiz_candidate,
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


def _validated_quiz_event(quiz: dict[str, Any]) -> Event:
    """Publish a validated quiz through both workflow and content contracts."""
    return Event(
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text=json.dumps(quiz, ensure_ascii=False))],
        ),
        output=quiz,
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


# --- Pydantic Models for Quiz and Safety Structures ---


class ExtractedQuizInfo(BaseModel):
    grade: Optional[str] = Field(
        None,
        description="The school grade/year (e.g., 'Grade 5', '5. Klasse') if mentioned in the prompt.",
    )
    subject: Optional[str] = Field(
        None,
        description="The school subject (e.g., 'Math', 'Geschichte', 'Geographie') if mentioned.",
    )
    topic: Optional[str] = Field(
        None,
        description="The specific topic/theme (e.g., 'Fractions', 'Weimar Republic', 'Sambaquis') if mentioned.",
    )
    preferred_language: Optional[str] = Field(
        None, description="The detected preferred language ('de', 'pt', 'en') if clear."
    )
    mascot_id: Optional[str] = Field(
        None,
        description="The selected FoxQuiz mascot ID ('fox', 'owl', or 'dragon').",
    )
    previous_score: Optional[int] = Field(
        None, description="The previous quiz score out of 10 if provided (e.g., 3, 10)."
    )
    previous_questions: Optional[List[str]] = Field(
        None,
        description="A list of question texts from the previous quiz to avoid duplication if provided.",
    )
    previous_quiz_json: Optional[str] = Field(
        None,
        description="The full JSON string of the previous quiz to adapt if provided.",
    )
    selected_difficulty: Optional[str] = Field(
        None,
        description="The user selected progression difficulty ('medium' or 'hard') if chosen via the modal.",
    )
    clarification_response: Optional[str] = Field(
        None,
        description="Additional scope supplied after a clarification question while retaining the original topic.",
    )


class QuizQuestion(BaseModel):
    question: str = Field(
        description="The question text. Decorative emojis are allowed only when they do not reveal the answer."
    )
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
    title: str = Field(description="A fun and engaging title for the quiz.")
    questions: List[QuizQuestion] = Field(description="List of exactly 10 questions.")
    difficulty: Optional[str] = Field(
        None,
        description="The difficulty indicator of the quiz. Must be exactly one of: '🌱 Easy', '⭐ Medium', or '🚀 Hard'.",
    )


class JudgeAssessment(BaseModel):
    passed: bool = Field(
        description="True if the quiz meets all criteria: 10 questions, appropriate grade difficulty, exactly one correct option per question, and factually accurate."
    )
    reason: str = Field(description="Detailed review comments/feedback.")


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


@node
async def gather_and_route(ctx: Context, node_input: Any) -> Event:
    """Extracts school grade, subject, and topic from user prompts and handles follow-up chat interactions."""
    prompt = _text_from_node_input(node_input)
    if not prompt:
        prompt = ctx.state.get(_ALLOWED_INPUT_STATE_KEY, "") or ""
    ctx.state[_ALLOWED_INPUT_STATE_KEY] = ""

    logger.info("Gather and Route started.")

    # Handle user requests to reset or start over
    prompt_lower = prompt.lower()
    reset_keywords = [
        "neu",
        "new",
        "reset",
        "starten",
        "start over",
        "outro",
        "outra",
        "novo",
        "nova",
    ]
    if any(kw in prompt_lower for kw in reset_keywords) and len(prompt_lower) < 25:
        ctx.state.clear()
        logger.info("Mascot resetting conversation state.")

    # Lazy state initialization
    if "grade" not in ctx.state:
        ctx.state["grade"] = None
    if "subject" not in ctx.state:
        ctx.state["subject"] = None
    if "topic" not in ctx.state:
        ctx.state["topic"] = None
    if "preferred_language" not in ctx.state:
        ctx.state["preferred_language"] = get_client_locale() or "en"
    if "mascot_id" not in ctx.state:
        ctx.state["mascot_id"] = DEFAULT_MASCOT_ID
    # Reset quality diagnostics on any fresh start or new turn.
    ctx.state["judge_attempts"] = 0
    ctx.state["judge_reasons"] = []
    ctx.state["generation_attempts"] = 0
    ctx.state["deterministic_retry_guidance"] = ""
    ctx.state["deterministic_validation_issues"] = []
    ctx.state["curriculum_status"] = None
    ctx.state["curriculum_guidance"] = ""
    ctx.state["quality_failure_type"] = None
    ctx.state["grounding_title"] = None
    ctx.state["grounding_discarded"] = False

    lang = ctx.state["preferred_language"]

    # Try parsing the prompt as JSON directly (e.g. for deterministic buttons / Let's go for more questions)
    is_json_payload = False
    if prompt and prompt.strip().startswith("{") and prompt.strip().endswith("}"):
        try:
            parsed = json.loads(prompt)
            if isinstance(parsed, dict) and (
                "grade" in parsed
                or "subject" in parsed
                or "topic" in parsed
                or "previous_score" in parsed
            ):
                logger.info("Successfully parsed prompt as structured JSON parameters.")
                if parsed.get("grade"):
                    ctx.state["grade"] = parsed["grade"]
                if parsed.get("subject"):
                    ctx.state["subject"] = parsed["subject"]
                if parsed.get("topic"):
                    ctx.state["topic"] = parsed["topic"]
                if parsed.get("preferred_language"):
                    ctx.state["preferred_language"] = parsed["preferred_language"]
                if "mascot_id" in parsed:
                    ctx.state["mascot_id"] = parsed["mascot_id"]
                if "previous_score" in parsed:
                    ctx.state["previous_score"] = parsed["previous_score"]
                if "previous_questions" in parsed:
                    ctx.state["previous_questions"] = parsed["previous_questions"]
                if "previous_quiz_json" in parsed:
                    ctx.state["previous_quiz_json"] = parsed["previous_quiz_json"]
                if "selected_difficulty" in parsed:
                    ctx.state["selected_difficulty"] = parsed["selected_difficulty"]
                ctx.state["clarification_response"] = parsed.get(
                    "clarification_response"
                )
                is_json_payload = True
        except Exception as e:
            logger.info(
                "Prompt is not a structured JSON payload (%s). Proceeding with natural language extraction.",
                type(e).__name__,
            )

    # If a prompt is present and was not a parsed JSON payload, run lightweight structured LLM to extract info
    if prompt and not is_json_payload:
        client = Client()
        try:
            extraction_prompt = (
                "You are an assistant for a school exam preparation quiz generator.\n"
                "Analyze the user's input and extract: school grade/year (grade), school subject (subject), "
                "the exam topic (topic), and the preferred language ('de', 'pt', 'en').\n"
                f'User input to review: "{prompt}"'
            )
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=extraction_prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ExtractedQuizInfo,
                    temperature=0.0,
                ),
            )
            record_token_usage(
                ctx,
                response,
                call_stage=CallStage.PARAMETER_EXTRACTOR,
            )
            extracted = ExtractedQuizInfo.model_validate_json(response.text.strip())
            logger.info("Structured quiz parameters extracted.")

            if extracted.grade:
                ctx.state["grade"] = extracted.grade
            if extracted.subject:
                ctx.state["subject"] = extracted.subject
            if extracted.topic:
                ctx.state["topic"] = extracted.topic
            if extracted.preferred_language:
                ctx.state["preferred_language"] = extracted.preferred_language
            if extracted.previous_score is not None:
                ctx.state["previous_score"] = extracted.previous_score
            if extracted.mascot_id:
                ctx.state["mascot_id"] = extracted.mascot_id
            if extracted.previous_questions:
                ctx.state["previous_questions"] = extracted.previous_questions
            if extracted.previous_quiz_json:
                ctx.state["previous_quiz_json"] = extracted.previous_quiz_json
            if extracted.selected_difficulty:
                ctx.state["selected_difficulty"] = extracted.selected_difficulty
            ctx.state["clarification_response"] = extracted.clarification_response
        except Exception as e:
            logger.error("Information extraction failed (%s).", type(e).__name__)

    # Check if we have gathered all 3 pieces of information
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

        # Perform Upfront Curriculum Validation Check to prevent mismatched/inappropriate topics
        logger.info("Performing upfront curriculum validation check.")
        client = Client()
        try:
            validation_prompt = (
                "You are a strict but supportive school curriculum scope evaluator.\n"
                f"Grade/Year: {grade}\nSubject: {subject}\nTopic: {topic}\n\n"
                "Additional scope supplied after a clarification question: "
                f"{clarification_response or 'none'}\n\n"
                f"Requested adaptive level: {expected_difficulty}.\n"
                "Apply this generator-wide difficulty design contract:\n"
                f"{difficulty_design_guidance}\n\n"
                "Decide whether this exact combination is ready for quiz generation.\n"
                "Use status='compatible' only when the topic has a clear interpretation at the requested grade level without silently changing the requested topic. "
                "A recognizable school topic is compatible even when it is broad: when no narrower scope is supplied, interpret it as a balanced general overview of the topic. "
                "Treat an answer such as 'general information' as an explicit request for that overview. "
                "Provide difficulty_guidance with concrete grade-level concepts, reasonable workload and task types to include, plus elementary or overly advanced concepts and repetitive task patterns to exclude. Translate the design contract into topic-specific guidance rather than weakening it.\n"
                "Use status='needs_clarification' only when the topic is genuinely ambiguous, unintelligible, or level-dependent in a way that would produce materially different quizzes and no safe conventional school interpretation exists. "
                "Do not request clarification merely because a valid school topic covers many facts or subtopics. "
                "For example, Grade 12 Mathematics plus 'Multiplication' needs clarification between matrix multiplication, polynomial multiplication, complex-number multiplication, or another advanced scope; it must not generate elementary multiplication questions. "
                "Provide a short clarification_question and two or three suggested_topics/scopes.\n"
                "Use status='incompatible' when the topic is fundamentally outside the subject, cognitively inappropriate for the grade, or not a suitable school-learning topic. "
                "Provide two or three age-appropriate alternatives.\n"
                "Do not accept a combination merely because the topic could be simplified or made harder. First require enough scope to produce a genuinely grade-aligned quiz.\n"
                "When additional clarification is present, interpret it together with the original topic rather than replacing the original topic.\n"
                f"Write explanation, clarification_question, suggested_topics, and difficulty_guidance in language '{lang}' ('de', 'pt', or 'en').\n"
                "Return structured JSON matching CurriculumCompatibility."
            )
            response = client.models.generate_content(
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
                    f"The child asked for a quiz about '{topic}' in Grade '{grade}' and Subject '{subject}', but this topic is too complex or not appropriate (Explanation: {compatibility.explanation}).\n"
                    f"In a playful, extremely encouraging, and kind tone, explain in language '{lang}' that this topic is usually learned by older students, and suggest these age-appropriate alternatives: {', '.join(compatibility.suggested_topics)}.\n"
                    f"Ask them which of these cool topics they would like to do instead, or if they want to choose a different grade/topic. Keep the response short, clear, and full of positive energy!"
                )
                mascot_resp = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=f"Playful mascot explanation to child why '{topic}' is not suitable for grade '{grade}' and suggest: {', '.join(compatibility.suggested_topics)}",
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

    # Otherwise, ask conversationally for what is missing in their language
    missing_fields = []
    if not grade:
        missing_fields.append(
            "Grade/School Year"
            if lang == "en"
            else "Schuljahr/Klasse"
            if lang == "de"
            else "Ano escolar"
        )
    if not subject:
        missing_fields.append(
            "Subject" if lang == "en" else "Fach" if lang == "de" else "Matéria"
        )
    if not topic:
        missing_fields.append(
            "Topic" if lang == "en" else "Thema" if lang == "de" else "Tema"
        )

    missing_str = ", ".join(missing_fields)

    system_conv_prompt = (
        f"You are {mascot_name}, a playful, friendly learning companion for kids.\n"
        f"If you introduce yourself, use exactly the name '{mascot_name}' and never claim to be another mascot.\n"
        f"The user wants a quiz but some info is missing: ({missing_str}).\n"
        f"Ask them conversationally to fill in these missing values. Speak directly to them in '{lang}'.\n"
        f"Keep your message encouraging, short, and clear. Do not use animal emoji because the frontend renders the selected mascot artwork separately."
    )

    client = Client()
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f'Ask for: {missing_str}. Conversation context: "{prompt}"',
            config=types.GenerateContentConfig(
                system_instruction=system_conv_prompt, temperature=0.7
            ),
        )
        record_token_usage(
            ctx,
            response,
            call_stage=CallStage.MASCOT_PROMPT,
        )
        msg_text = response.text.strip()
    except Exception as e:
        logger.error(
            "Mascot prompt generation failed (%s). Using fallback.",
            type(e).__name__,
        )
        if lang == "de":
            msg_text = f"Hallo! Ich bin {mascot_name}. Um dein cooles Quiz vorzubereiten, brauche ich noch folgende Infos: {missing_str}! Lass es mich wissen!"
        elif lang == "pt":
            msg_text = f"Olá! Eu sou o {mascot_name}. Para montar seu super quiz, ainda preciso saber: {missing_str}! Me conta!"
        else:
            msg_text = f"Hello! I'm {mascot_name}. To build your awesome quiz, I still need: {missing_str}! Tell me about it!"

    return Event(
        content=types.Content(
            role="model", parts=[types.Part.from_text(text=msg_text)]
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


MAX_QUIZ_GENERATION_ATTEMPTS = 2
_HARD_DIFFICULTY_SELECTION = "hard"


def _expected_quiz_difficulty(
    previous_score: int | None,
    selected_difficulty: str | None,
) -> str:
    """Return the authoritative adaptive difficulty label for a quiz request."""
    if previous_score is not None and previous_score <= 3:
        return "🌱 Easy"
    if previous_score is not None and previous_score >= 8:
        normalized_selection = (
            selected_difficulty.strip().casefold()
            if isinstance(selected_difficulty, str)
            else ""
        )
        if normalized_selection == _HARD_DIFFICULTY_SELECTION:
            return "🚀 Hard"
        return (
            "🚀 Hard"
            if previous_score == 10 and not normalized_selection
            else "⭐ Medium"
        )
    return "⭐ Medium"


def _build_difficulty_design_guidance(expected_difficulty: str) -> str:
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
    if expected_difficulty == "🌱 Easy":
        return common + (
            "Keep questions short, concrete, mostly one-step, and focused on core "
            "understanding. Keep arithmetic and reading load manageable, and avoid "
            "unnecessarily large numbers. Reinforcement may reuse prior concepts, "
            "so clarity matters more than novelty."
        )
    if expected_difficulty == "🚀 Hard":
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
            "with larger operands and tightly clustered numeric distractors."
        )
    return common + (
        "Provide a balanced standard-grade mix of recall, understanding, application, "
        "and reasoning. When the topic permits, use at least four meaningfully "
        "different task forms across the ten questions. For mathematics or other "
        "quantitative topics, balance computation with estimation, strategy, and "
        "short applications, and keep manual calculation proportionate to the "
        "learning objective."
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
) -> str:
    """Build the academic-review contract shared with the LLM judge."""
    expected_difficulty = _expected_quiz_difficulty(previous_score, selected_difficulty)
    difficulty_design_guidance = _build_difficulty_design_guidance(expected_difficulty)
    return (
        "You are a strict, professional school academic reviewer (LLM-as-a-judge).\n"
        "Assess if the following generated quiz JSON satisfies all standards:\n"
        f"1. Is the difficulty and content exactly aligned with school standards for Grade '{grade}'?\n"
        f"2. Does it cover the subject '{subject}' and topic '{topic}' accurately?\n"
        "3. Does the quiz contain exactly 10 questions?\n"
        "4. Does each question contain between 3 and 5 options, with exactly ONE correct choice?\n"
        "5. Is the 'correct_option_index' mathematically and factually correct? "
        "CRITICAL: For each question, you MUST independently determine the factually correct answer (whether it is a mathematical calculation, a historical date, a biological definition, etc.). Then, verify that the 'correct_option_index' points EXACTLY to that correct answer inside the 0-based options array. "
        "If there is any mismatch between the factually correct answer, the option at 'correct_option_index', or the correct answer described in your explanation, you MUST set passed to false.\n"
        "6. Are all answer options neutral and free of emojis or visual correctness cues, and do any emojis in a question avoid depicting, naming, or otherwise revealing its correct answer? If not, you MUST set passed to false.\n\n"
        "--- AUTHORITATIVE ADAPTIVE DIFFICULTY CONTRACT ---\n"
        f"The expected difficulty field is exactly '{expected_difficulty}'.\n"
        f"Previous score: {previous_score if previous_score is not None else 'not available'}/10.\n"
        f"User-selected progression difficulty: {selected_difficulty or 'not selected'}.\n"
        "Difficulty labels are relative to the requested grade, never permission to use content from a higher grade. "
        "In particular, '🚀 Hard' means a deeper, more demanding challenge for a high-achieving student within the authoritative curriculum scope for the requested grade. "
        "Do not reject a quiz merely because '🚀 Hard' is used for a younger grade when that is the expected user-selected label. "
        "Instead, verify that its content is meaningfully challenging while remaining age-appropriate and inside the supplied grade-level scope. "
        "Reject when the label differs from the expected label, when the content is too easy for the selected mode, or when it exceeds or contradicts the grade-level scope.\n\n"
        "Apply the following task-design contract as a required quality criterion. Reject a quiz that materially violates it:\n"
        f"{difficulty_design_guidance}\n\n"
        "The upfront curriculum evaluator supplied this authoritative grade-level scope. The quiz must comply with it:\n"
        f"{curriculum_guidance or 'No additional scope guidance was available.'}\n\n"
        f"Quiz JSON:\n{json.dumps(quiz_dict)}\n"
    )


@node
async def quiz_generation(ctx: Context, node_input: Any) -> Event:
    """Uses LLM structured generation to build a highly tailored, fun multiple-choice quiz of 10 questions."""
    grade = ctx.state.get("grade")
    subject = ctx.state.get("subject")
    topic = ctx.state.get("topic")
    lang = ctx.state.get("preferred_language") or "en"
    search_context = ctx.state.get("search_context", "")
    attempt = int(ctx.state.get("generation_attempts") or 0) + 1
    ctx.state["generation_attempts"] = attempt

    previous_score = ctx.state.get("previous_score")
    previous_questions = ctx.state.get("previous_questions")
    previous_quiz_json = ctx.state.get("previous_quiz_json")
    selected_difficulty = ctx.state.get("selected_difficulty")
    expected_difficulty = _expected_quiz_difficulty(previous_score, selected_difficulty)
    difficulty_design_guidance = _build_difficulty_design_guidance(expected_difficulty)
    curriculum_guidance = ctx.state.get("curriculum_guidance", "")
    judge_reasons = list(ctx.state.get("judge_reasons") or [])
    deterministic_retry_guidance = ctx.state.get("deterministic_retry_guidance", "")

    logger.info("Generating quiz attempt %s.", attempt)

    prompt = (
        f"Create an interactive multiple-choice quiz with exactly 10 questions.\n"
        f"Target Audience: School students in Grade/Year {grade} (aged 10-18 years old).\n"
        f"Subject: {subject}\n"
        f"Topic: {topic}\n"
        f"Preferred Language: Entire quiz MUST be written in '{lang}' (Deutsch, Português, or English).\n"
        f"\nPedagogical Tone Scaling:\n"
        f"- For younger students (Grades 5-8, ages 10-14): Keep the tone highly playful, simplified, and kid-friendly. Decorative emojis may appear in titles, questions, or explanations, but never in answer options and never when they reveal the correct answer.\n"
        f"- For older students (Grades 9-12, ages 14-18): Switch to a supportive peer-mentor tone. Keep the mascot identity (e.g. Felix/Olivia/Dino) but communicate with intellectual respect, using advanced, clear explanations without sounding overly simple or talking down to them.\n"
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

    if judge_reasons:
        prompt += (
            "\n--- REQUIRED RETRY CORRECTION ---\n"
            f"The previous quiz attempt was rejected by the academic reviewer: {judge_reasons[-1]}\n"
            "Generate a materially corrected quiz that resolves this feedback. Do not repeat the rejected difficulty, scope, or factual issue.\n"
        )

    if deterministic_retry_guidance:
        prompt += (
            "\n--- REQUIRED STRUCTURAL CORRECTION ---\n"
            f"{deterministic_retry_guidance}\n"
            "Correct every listed problem before returning the complete quiz.\n"
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
        "2. Each question has between 3 and 5 answer options.\n"
        "3. EXACTLY one option must be correct.\n"
        "4. Set 'correct_option_index' to the exact 0-based index of the correct option inside the options array. CRITICAL: Double-check that your 'correct_option_index' points exactly to the mathematically or factually correct option among the provided options, and matches the correct answer stated in your explanation.\n"
        "5. Every answer option must be neutral, text-only content. Never put emojis, check marks, crosses, stars, labels such as 'correct', or any other visual answer cue in an option. Question emojis are allowed only when they do not name, depict, or otherwise reveal the correct answer.\n"
        "6. Keep the explanations warm, educational, clear, and highly encouraging (explain why the correct answer is right and why others are wrong in a child-friendly mascot way). CRITICAL: Do NOT start explanations with affirmative or congratulatory words like 'Parabéns!', 'Isso mesmo!', 'Congratulations!', 'Exactly!', 'Herzlichen Glückwunsch!', or 'Richtig!', because these explanations are shown even when the student chooses the wrong answer. Start directly with the factual explanation (e.g. 'Células-tronco são...' instead of 'Isso mesmo! Células-tronco são...').\n"
    )

    adaptation_instructions = ""
    if previous_score is not None:
        logger.info("Applying adaptive progression.")
        if previous_score <= 3:
            # Score <= 3/10: Reinforcement Mode (🌱 Easy)
            # No duplicate-prevention, reuse previous questions but shuffle.
            adaptation_instructions = (
                f"\n--- ADAPTIVE REINFORCEMENT MODE ---\n"
                f"The student scored {previous_score}/10 on the previous quiz, which indicates they struggled with the material.\n"
                f"The current quiz content is difficult enough. Your goal is to REPEAT the previous quiz questions so that the student can understand and learn them properly.\n"
                f"Do NOT generate new or different questions. Do NOT avoid duplication.\n"
                f"Instead, do the following:\n"
                f"- Shuffle the order of the 10 questions compared to the previous quiz.\n"
                f"- For each question, shuffle the order of its options (answer choices) and update the 'correct_option_index' accordingly.\n"
                f"- You can make slight, minor improvements or rephrasings to make the questions or explanations even clearer/simpler, but they must cover the exact same questions and concepts.\n"
                f"- Set the 'difficulty' field to exactly: '🌱 Easy' (since we are repeating for reinforcement and practice).\n"
            )
            if previous_quiz_json:
                adaptation_instructions += f"Here is the exact previous quiz JSON for reference:\n{previous_quiz_json}\n"
        elif previous_score >= 8:
            # Score >= 8/10: User-Choice Progression Mode (choose between ⭐ Medium and 🚀 Hard)
            if expected_difficulty == "🚀 Hard":
                adaptation_instructions = (
                    f"\n--- ADAPTIVE PROGRESSION MODE (CHALLENGE) ---\n"
                    f"The student scored {previous_score}/10 on the previous quiz and selected the DIFFICULT (Advanced) level.\n"
                    f"You must significantly SCALE UP the cognitive depth of this new quiz while staying inside Grade {grade}. Use varied reasoning, application, strategy, estimation, comparison, or error-analysis tasks when they fit the topic. Do not create difficulty mainly through larger numbers, calculator-like manual work, or tightly clustered answer choices.\n"
                    f"Set the 'difficulty' field to exactly: '🚀 Hard'.\n"
                )
            else:
                adaptation_instructions = (
                    f"\n--- ADAPTIVE PROGRESSION MODE (NEXT LEVEL) ---\n"
                    f"The student scored {previous_score}/10 on the previous quiz and selected the MEDIUM (Standard) level.\n"
                    f"Maintain standard Grade {grade} difficulty, but generate a completely fresh set of questions.\n"
                    f"Set the 'difficulty' field to exactly: '⭐ Medium'.\n"
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
            # Score 4 to 7: Practice Mode (⭐ Medium)
            # Keep standard difficulty, generate a new set of questions.
            adaptation_instructions = (
                f"\n--- STANDARD PRACTICE MODE ---\n"
                f"The student scored {previous_score}/10 on the previous quiz.\n"
                f"Keep standard difficulty for Grade {grade}. Generate a new set of questions to continue practice on the topic.\n"
                f"Set the 'difficulty' field to exactly: '⭐ Medium'.\n"
                f"Note: It is fine to reuse some questions or concepts if they are central, as duplication avoidance is not strictly enforced for scores below 8/10.\n"
            )
    else:
        # First time quiz generation or no score available:
        # Set difficulty to '⭐ Medium'
        adaptation_instructions = (
            f"\nSet the 'difficulty' field to exactly: '⭐ Medium'.\n"
        )

    prompt += adaptation_instructions

    client = Client()
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=Quiz,
                temperature=0.7 if attempt == 1 else 0.8,
            ),
        )
        record_token_usage(
            ctx,
            response,
            call_stage=CallStage.QUIZ_GENERATOR,
            generation_attempt=attempt,
        )
        quiz_dict = json.loads(response.text.strip())
        # Keep user-visible metadata deterministic and consistent with the
        # adaptive mode reviewed by the academic judge.
        quiz_dict["difficulty"] = expected_difficulty
        ctx.state["temp_quiz"] = quiz_dict
        return _candidate_ready_event()
    except Exception as e:
        logger.error("Quiz generation failed (%s).", type(e).__name__)
        raise


def _candidate_ready_event() -> Event:
    """Signal the judge without exposing unvalidated quiz JSON to clients."""
    # A non-empty output traverses the unconditional workflow edge
    # Edge(from_node=quiz_generation, to_node=llm_as_a_judge).
    return _workflow_event(output={"status": "candidate_ready"})


def _route_after_failed_judge(generation_attempts: int) -> str:
    """Share the generation retry budget across deterministic and LLM review."""
    return (
        "quality_failure"
        if generation_attempts >= MAX_QUIZ_GENERATION_ATTEMPTS
        else "retry"
    )


@node
async def deterministic_quiz_validation(ctx: Context, node_input: Any) -> Event:
    """Reject structural defects and answer cues before the expensive LLM judge."""
    result = validate_quiz_candidate(ctx.state.get("temp_quiz"))
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

    guidance = build_retry_guidance(result)
    ctx.state["deterministic_retry_guidance"] = guidance
    ctx.state["quality_failure_type"] = "deterministic_validation_failed"
    route = _route_after_failed_judge(generation_attempts)
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
    """Strict Reviewer: evaluates the generated quiz structure and content accuracy. Loops back on failures."""
    quiz_dict = ctx.state.get("temp_quiz")

    # Track attempts using our state counter instead of unreliable/non-incrementing ctx.attempt_count inside manual loops
    attempts = ctx.state.get("judge_attempts", 0) + 1
    ctx.state["judge_attempts"] = attempts

    if not quiz_dict:
        return _workflow_event(route="retry")

    # Optimization: In Reinforcement Mode (score <= 3), we shuffle the previously validated questions.
    # We can skip the LLM Judge review call completely to save token usage and cut latency by 1.5 - 2.5 seconds!
    previous_score = ctx.state.get("previous_score")
    if previous_score is not None and previous_score <= 3:
        logger.info(
            "Reinforcement mode: skipping LLM-as-a-judge review on shuffled questions."
        )
        return _workflow_event(route="success")

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
    )

    client = Client()
    try:
        response = client.models.generate_content(
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
            return _workflow_event(route="success")
        else:
            failure_route = _route_after_failed_judge(
                int(ctx.state.get("generation_attempts") or 0)
            )
            judge_reasons = list(ctx.state.get("judge_reasons") or [])
            judge_reasons.append(assessment.reason)
            ctx.state["judge_reasons"] = judge_reasons
            ctx.state["quality_failure_type"] = "judge_rejected"
            logger.warning(
                "Judge failed validation. Routing to %s.",
                failure_route,
            )
            return _workflow_event(route=failure_route)
    except Exception as e:
        judge_reasons = list(ctx.state.get("judge_reasons") or [])
        judge_reasons.append(f"Judge unavailable: {type(e).__name__}")
        ctx.state["judge_reasons"] = judge_reasons
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

    final_validation = validate_quiz_candidate(quiz_dict)
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

    ctx.state["judge_attempts"] = 0
    ctx.state["generation_attempts"] = 0
    ctx.state["deterministic_retry_guidance"] = ""
    ctx.state["deterministic_validation_issues"] = []

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
    yield _validated_quiz_event(quiz_dict)


@node
async def ask_more_node(ctx: Context, node_input: Any) -> Event:
    """Terminal node for the 'ask_more' route. Gracefully ends the branch."""
    logger.info("Mascot prompt asking for more information.")
    set_invocation_outcome(ctx, TerminalOutcome.NEEDS_INPUT)
    return _workflow_event()


def _save_quality_failure_best_effort(failure: QuizQualityFailure) -> None:
    """Persist diagnostics without replacing the user-facing failure response."""
    try:
        FirestoreRepository().save_quiz_quality_failure(failure)
        logger.info("Saved quiz quality failure diagnostic.")
    except FirestorePersistenceError:
        logger.warning("Could not persist quiz quality failure diagnostic.")


def _quality_failure_event(ctx: Context) -> Event:
    """Persist diagnostics and build the localized fail-closed response."""
    set_invocation_outcome(ctx, TerminalOutcome.QUALITY_FAILURE)
    lang = ctx.state.get("preferred_language") or "en"
    failure = QuizQualityFailure(
        quiz_context=QuizContext.from_state(ctx.state),
        failure_type=ctx.state.get("quality_failure_type") or "judge_rejected",
        judge_attempts=int(ctx.state.get("judge_attempts") or 0),
        judge_reasons=list(ctx.state.get("judge_reasons") or []),
        validation_issues=list(ctx.state.get("deterministic_validation_issues") or []),
        grounding_title=ctx.state.get("grounding_title"),
        grounding_discarded=bool(ctx.state.get("grounding_discarded", False)),
    )
    _save_quality_failure_best_effort(failure)

    ctx.state["temp_quiz"] = None
    ctx.state["judge_attempts"] = 0
    ctx.state["generation_attempts"] = 0
    ctx.state["deterministic_retry_guidance"] = ""
    ctx.state["deterministic_validation_issues"] = []

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
