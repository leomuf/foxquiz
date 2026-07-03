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

import os
import json
import logging
import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

import google.auth
from google.genai import Client, types
from google.adk.models import Gemini
from google.adk.workflow import Workflow, START, node, FunctionNode, Edge
from google.adk.events.event import Event
from google.adk.agents.context import Context
from google.adk.apps import App

from app.app_utils.callbacks import before_agent_callback, after_agent_callback
from app.app_utils.request_context import get_client_locale

# Setup project configuration
_, project_id = google.auth.default()
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

logger = logging.getLogger(__name__)

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
    previous_score: Optional[int] = Field(
        None, description="The previous quiz score out of 10 if provided (e.g., 3, 10)."
    )
    previous_questions: Optional[List[str]] = Field(
        None, description="A list of question texts from the previous quiz to avoid duplication if provided."
    )
    previous_quiz_json: Optional[str] = Field(
        None, description="The full JSON string of the previous quiz to adapt if provided."
    )



class QuizQuestion(BaseModel):
    question: str = Field(description="The question text.")
    options: List[str] = Field(description="List of 3 to 5 options/choices.")
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
        description="The difficulty indicator of the quiz. Must be exactly one of: '🌱 Easy', '⭐ Medium', or '🚀 Hard'."
    )



class JudgeAssessment(BaseModel):
    passed: bool = Field(
        description="True if the quiz meets all criteria: 10 questions, appropriate grade difficulty, exactly one correct option per question, and factually accurate."
    )
    reason: str = Field(description="Detailed review comments/feedback.")


class CurriculumCompatibility(BaseModel):
    is_compatible: bool = Field(
        description="True if the chosen topic is cognitively, pedagogically, and curriculum-wise appropriate for the requested school grade and subject. False otherwise."
    )
    explanation: str = Field(
        description="A helpful, kind, and pedagogically sound explanation of why the topic is or is not compatible with the grade. If incompatible, state clearly why (e.g., differential equations is university-level math, not for 5th graders)."
    )
    suggested_topics: List[str] = Field(
        description="If is_compatible is False, provide 2 to 3 alternative topics in the requested language that are age-appropriate and curriculum-aligned for this grade and subject."
    )


# --- Helper Function for Curriculum Search Skill ---


def search_wikipedia(query: str, lang: str = "en") -> str:
    """Real live Wikipedia search API call to gather localized curriculum context (GDPR-safe, zero model cost)."""
    try:
        import requests

        url = f"https://{lang}.wikipedia.org/w/api.php"

        # Step 1: Search for matches
        search_params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": query,
            "utf8": 1,
            "formatversion": 2,
        }
        r = requests.get(url, params=search_params, timeout=5)
        r.raise_for_status()
        data = r.json()
        search_results = data.get("query", {}).get("search", [])
        if not search_results:
            return f"No direct Wikipedia articles found for search query: {query}"

        # Step 2: Extract article intro
        page_id = search_results[0]["pageid"]
        title = search_results[0]["title"]
        extract_params = {
            "action": "query",
            "format": "json",
            "prop": "extracts",
            "pageids": page_id,
            "exintro": 1,
            "explaintext": 1,
            "formatversion": 2,
        }
        r = requests.get(url, params=extract_params, timeout=5)
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
            f"Wikipedia search failed for '{query}': {e}. Proceeding with internal LLM knowledge."
        )
        return ""


# --- Graph Nodes ---


@node
async def gather_and_route(ctx: Context, node_input: Any) -> Event:
    """Extracts school grade, subject, and topic from user prompts and handles follow-up chat interactions."""
    prompt = ""
    if isinstance(node_input, str):
        prompt = node_input
    elif hasattr(node_input, "parts"):
        prompt = "".join([part.text for part in node_input.parts if part.text]).strip()
    elif isinstance(node_input, dict):
        prompt = node_input.get("text", "")

    logger.info(f"Gather and Route. Raw prompt: '{prompt}'")

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
        ctx.state["preferred_language"] = get_client_locale() or "de"
    # Reset judge attempts on any fresh start or new turn
    ctx.state["judge_attempts"] = 0

    lang = ctx.state["preferred_language"]

    # Try parsing the prompt as JSON directly (e.g. for deterministic buttons / Let's go for more questions)
    is_json_payload = False
    if prompt and prompt.strip().startswith("{") and prompt.strip().endswith("}"):
        try:
            parsed = json.loads(prompt)
            if isinstance(parsed, dict) and ("grade" in parsed or "subject" in parsed or "topic" in parsed or "previous_score" in parsed):
                logger.info("Successfully parsed prompt as structured JSON parameters.")
                if parsed.get("grade"):
                    ctx.state["grade"] = parsed["grade"]
                if parsed.get("subject"):
                    ctx.state["subject"] = parsed["subject"]
                if parsed.get("topic"):
                    ctx.state["topic"] = parsed["topic"]
                if parsed.get("preferred_language"):
                    ctx.state["preferred_language"] = parsed["preferred_language"]
                if "previous_score" in parsed:
                    ctx.state["previous_score"] = parsed["previous_score"]
                if "previous_questions" in parsed:
                    ctx.state["previous_questions"] = parsed["previous_questions"]
                if "previous_quiz_json" in parsed:
                    ctx.state["previous_quiz_json"] = parsed["previous_quiz_json"]
                is_json_payload = True
        except Exception as e:
            logger.info(f"Prompt is not a structured JSON payload: {e}. Proceeding with natural language extraction.")

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
            extracted = ExtractedQuizInfo.model_validate_json(response.text.strip())
            logger.info(f"Extracted parameters: {extracted}")

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
            if extracted.previous_questions:
                ctx.state["previous_questions"] = extracted.previous_questions
            if extracted.previous_quiz_json:
                ctx.state["previous_quiz_json"] = extracted.previous_quiz_json
        except Exception as e:
            logger.error(f"Error during info extraction: {e}")

    # Check if we have gathered all 3 pieces of information
    grade = ctx.state.get("grade")
    subject = ctx.state.get("subject")
    topic = ctx.state.get("topic")
    lang = ctx.state.get("preferred_language") or "de"

    if grade and subject and topic:
        # Perform Upfront Curriculum Validation Check to prevent mismatched/inappropriate topics
        logger.info(f"Performing upfront curriculum validation check for: Grade='{grade}', Subject='{subject}', Topic='{topic}'")
        client = Client()
        try:
            validation_prompt = (
                f"Assess whether the school topic '{topic}' is age-appropriate, cognitively and curriculum-wise suitable "
                f"for school grade '{grade}' and subject '{subject}'.\n"
                f"Return structured JSON matching CurriculumCompatibility schema.\n"
                f"Provide alternative suggested topics if incompatible. Ensure everything is in language '{lang}' (either 'de', 'pt', or 'en')."
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
            compatibility = CurriculumCompatibility.model_validate_json(response.text.strip())
            logger.info(f"Upfront curriculum check results: is_compatible={compatibility.is_compatible}, explanation='{compatibility.explanation}', suggestions={compatibility.suggested_topics}")

            if compatibility.is_compatible:
                return Event(route="generate_quiz")
            else:
                # Clear incompatible topic from state so they can enter a new one
                ctx.state["topic"] = None

                # Select and localize mascot for friendly dialogue delivery
                mascots = [
                    {
                        "id": "fox",
                        "emoji": "🦊",
                        "name": "Felix der Fuchs",
                        "name_pt": "Felix, a Raposa",
                        "name_en": "Felix the Fox",
                    },
                    {
                        "id": "owl",
                        "emoji": "🦉",
                        "name": "Olivia die Eule",
                        "name_pt": "Olivia, a Coruja",
                        "name_en": "Olivia the Owl",
                    },
                    {
                        "id": "dragon",
                        "emoji": "🐉",
                        "name": "Dino der Drache",
                        "name_pt": "Dino, o Dragão",
                        "name_en": "Dino the Dragon",
                    },
                ]
                mascot = mascots[len(prompt or "") % 3]
                mascot_name = (
                    mascot["name"]
                    if lang == "de"
                    else mascot["name_pt"]
                    if lang == "pt"
                    else mascot["name_en"]
                )
                mascot_emoji = mascot["emoji"]

                mascot_prompt = (
                    f"You are {mascot_name} {mascot_emoji}, a friendly, encouraging school learning companion mascot speaking directly to a child.\n"
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
                msg_text = mascot_resp.text.strip()
                return Event(
                    content=types.Content(
                        role="model", parts=[types.Part.from_text(text=msg_text)]
                    ),
                    route="ask_more",
                )
        except Exception as e:
            logger.error(f"Error during upfront curriculum check: {e}. Defaulting to allowing quiz generation.")
            return Event(route="generate_quiz")

    # Otherwise, ask conversationally for what is missing in their language
    mascots = [
        {
            "id": "fox",
            "emoji": "🦊",
            "name": "Felix der Fuchs",
            "name_pt": "Felix, a Raposa",
            "name_en": "Felix the Fox",
        },
        {
            "id": "owl",
            "emoji": "🦉",
            "name": "Olivia die Eule",
            "name_pt": "Olivia, a Coruja",
            "name_en": "Olivia the Owl",
        },
        {
            "id": "dragon",
            "emoji": "🐉",
            "name": "Dino der Drache",
            "name_pt": "Dino, o Dragão",
            "name_en": "Dino the Dragon",
        },
    ]
    mascot = mascots[len(prompt or "") % 3]
    mascot_name = (
        mascot["name"]
        if lang == "de"
        else mascot["name_pt"]
        if lang == "pt"
        else mascot["name_en"]
    )
    mascot_emoji = mascot["emoji"]

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
        f"You are {mascot_name} {mascot_emoji}, a playful, friendly learning companion for kids.\n"
        f"The user wants a quiz but some info is missing: ({missing_str}).\n"
        f"Ask them conversationally to fill in these missing values. Speak directly to them in '{lang}'.\n"
        f"Keep your message encouraging, short, clear, and full of positive vibes! Use appropriate emojis."
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
        msg_text = response.text.strip()
    except Exception as e:
        logger.error(f"Mascot prompt generation error: {e}. Using fallback.")
        if lang == "de":
            msg_text = f"Hallo! {mascot_emoji} Ich bin {mascot_name}. Um dein cooles Quiz vorzubereiten, brauche ich noch folgende Infos: {missing_str}! Lass es mich wissen!"
        elif lang == "pt":
            msg_text = f"Olá! {mascot_emoji} Eu sou o {mascot_name}. Para montar seu super quiz, ainda preciso saber: {missing_str}! Me conta!"
        else:
            msg_text = f"Hello! {mascot_emoji} I'm {mascot_name}. To build your awesome quiz, I still need: {missing_str}! Tell me about it!"

    return Event(
        content=types.Content(
            role="model", parts=[types.Part.from_text(text=msg_text)]
        ),
        route="ask_more",
    )


@node
async def decision_and_search(ctx: Context, node_input: Any) -> Event:
    """Autonomous Curriculum Search Skill. Dynamically gathers actual curriculum standards and facts from Wikipedia."""
    subject = ctx.state.get("subject")
    topic = ctx.state.get("topic")
    lang = ctx.state.get("preferred_language") or "de"

    logger.info(
        f"Curriculum Search Skill invoked. Querying Wikipedia for subject='{subject}', topic='{topic}', lang='{lang}'"
    )
    search_query = f"{subject} {topic}"
    wikipedia_data = search_wikipedia(search_query, lang=lang)

    ctx.state["search_context"] = wikipedia_data
    return Event()


@node
async def quiz_generation(ctx: Context, node_input: Any) -> Event:
    """Uses LLM structured generation to build a highly tailored, fun multiple-choice quiz of 10 questions."""
    grade = ctx.state.get("grade")
    subject = ctx.state.get("subject")
    topic = ctx.state.get("topic")
    lang = ctx.state.get("preferred_language") or "de"
    search_context = ctx.state.get("search_context", "")
    attempt = ctx.attempt_count or 1

    previous_score = ctx.state.get("previous_score")
    previous_questions = ctx.state.get("previous_questions")
    previous_quiz_json = ctx.state.get("previous_quiz_json")

    logger.info(
        f"Generating Quiz (Attempt {attempt}) for Grade={grade}, Subject={subject}, Topic={topic}, previous_score={previous_score}"
    )

    prompt = (
        f"Create an interactive, kid-friendly multiple-choice quiz with exactly 10 questions.\n"
        f"Target Audience: School children in Grade/Year {grade} (aged 10-16 years old).\n"
        f"Subject: {subject}\n"
        f"Topic: {topic}\n"
        f"Preferred Language: Entire quiz MUST be written in '{lang}' (Deutsch, Português, or English).\n"
    )

    if search_context:
        prompt += f"\nUse these verified curriculum grounding facts to shape your questions and answers correctly:\n{search_context}\n"

    prompt += (
        "\nRules & Schema requirements:\n"
        "1. Create exactly 10 questions.\n"
        "2. Each question has between 3 and 5 answer options.\n"
        "3. EXACTLY one option must be correct.\n"
        "4. Set 'correct_option_index' to the exact 0-based index of the correct option inside the options array.\n"
        "5. Keep the explanations warm, educational, clear, and highly encouraging (explain why the correct answer is right and why others are wrong in a child-friendly mascot way). CRITICAL: Do NOT start explanations with congratulatory words like 'Parabéns!', 'Congratulations!', or 'Herzlichen Glückwunsch!', because these explanations are shown even when the student chooses the wrong answer.\n"
    )

    adaptation_instructions = ""
    if previous_score is not None:
        logger.info(f"Applying adaptive progression for previous_score={previous_score}")
        if previous_score <= 4:
            # Score <= 4/10: the current quiz is difficult enough, repeat it with small changes in the question orders.
            # No duplicate-prevention, reuse previous questions but shuffle.
            adaptation_instructions = (
                f"\n--- ADAPTIVE REINFORCEMENT MODE ---\n"
                f"The student scored {previous_score}/10 on the previous quiz, which is a failing grade.\n"
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
            # Score >= 8/10: Strictly avoid duplicating any previously asked questions.
            # If 10/10: significantly scale up difficulty.
            # If 8 or 9: same difficulty, fresh questions.
            if previous_score == 10:
                adaptation_instructions = (
                    f"\n--- ADAPTIVE PROGRESSION MODE (CHALLENGE) ---\n"
                    f"The student scored {previous_score}/10 (perfect score!) on the previous quiz.\n"
                    f"You must significantly SCALE UP the difficulty of this new quiz. Introduce more advanced concepts, trickier options/distractors, and deeper questions suitable for high-achieving student in Grade {grade}.\n"
                    f"Set the 'difficulty' field to exactly: '🚀 Hard'.\n"
                )
            else:
                adaptation_instructions = (
                    f"\n--- ADAPTIVE PROGRESSION MODE (NEXT LEVEL) ---\n"
                    f"The student scored {previous_score}/10 on the previous quiz.\n"
                    f"Maintain the same grade difficulty level, but generate a completely fresh set of questions.\n"
                    f"Set the 'difficulty' field to exactly: '⭐ Medium'.\n"
                )
            
            # Strict Avoid Duplication rules
            adaptation_instructions += (
                f"\nCRITICAL COMPLIANCE RULES:\n"
                f"1. You MUST STRICTLY AVOID duplicating any previously asked questions to encourage learning progression.\n"
                f"2. Compare your new questions with the previous questions. Do not generate questions that are similar or duplicate the old ones.\n"
            )
            if previous_questions:
                adaptation_instructions += f"Do NOT use any of these questions from the previous quiz:\n" + "\n".join(f"- {q}" for q in previous_questions) + "\n"
        else:
            # Score 5 to 7: Keep standard difficulty, generate a new set of questions.
            # Important: duplication prevention is NOT strictly required for bad/normal results below 8/10,
            # but we should suggest keeping standard difficulty. Let's make it clear.
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
        quiz_dict = json.loads(response.text.strip())
        # Ensure difficulty field is set in quiz_dict
        if "difficulty" not in quiz_dict or not quiz_dict["difficulty"]:
            quiz_dict["difficulty"] = "🌱 Easy" if (previous_score is not None and previous_score <= 4) else "🚀 Hard" if (previous_score == 10) else "⭐ Medium"
        ctx.state["temp_quiz"] = quiz_dict
        return Event(output=quiz_dict)
    except Exception as e:
        logger.error(f"Quiz generation failed: {e}")
        raise


@node
async def llm_as_a_judge(ctx: Context, node_input: Any) -> Event:
    """Strict Reviewer: evaluates the generated quiz structure and content accuracy. Loops back on failures."""
    quiz_dict = ctx.state.get("temp_quiz")
    
    # Track attempts using our state counter instead of unreliable/non-incrementing ctx.attempt_count inside manual loops
    attempts = ctx.state.get("judge_attempts", 0) + 1
    ctx.state["judge_attempts"] = attempts

    if not quiz_dict:
        return Event(route="retry")

    if attempts >= 3:
        logger.warning("Max quality judge iterations reached. Releasing current quiz.")
        ctx.state["judge_attempts"] = 0
        return Event(route="success")

    grade = ctx.state.get("grade")
    subject = ctx.state.get("subject")
    topic = ctx.state.get("topic")

    judge_prompt = (
        "You are a strict, professional school academic reviewer (LLM-as-a-judge).\n"
        "Assess if the following generated quiz JSON satisfies all standards:\n"
        f"1. Is the difficulty and content exactly aligned with school standards for Grade '{grade}'?\n"
        f"2. Does it cover the subject '{subject}' and topic '{topic}' accurately?\n"
        "3. Does the quiz contain exactly 10 questions?\n"
        "4. Does each question contain between 3 and 5 options, with exactly ONE correct choice?\n"
        "5. Is the correct_option_index mathematically and factually correct?\n\n"
        f"Quiz JSON:\n{json.dumps(quiz_dict)}\n"
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
        assessment = JudgeAssessment.model_validate_json(response.text.strip())
        logger.info(
            f"LLM Judge Quality Review (Attempt {attempts}): Passed={assessment.passed}. Reason: {assessment.reason}"
        )

        if assessment.passed:
            return Event(route="success")
        else:
            logger.warning(
                f"Judge failed validation. Triggering loop retry. Reason: {assessment.reason}"
            )
            return Event(route="retry")
    except Exception as e:
        logger.error(f"LLM Judge error: {e}. Defaulting to safe release.")
        return Event(route="success")


@node
async def quiz_output_node(ctx: Context, node_input: Any) -> Event:
    """Prepares and releases the validated quiz. Returns friendly message and frozen quiz JSON."""
    quiz_dict = ctx.state.get("temp_quiz")
    lang = ctx.state.get("preferred_language") or "de"

    # Reset judge attempts as we successfully finalized the quiz
    ctx.state["judge_attempts"] = 0

    logger.info(f"Finalizing validated quiz: '{quiz_dict.get('title')}'")

    if lang == "de":
        msg = "🎉 **Dein personalisiertes Quiz ist fertig!**\n\nKlicke unten auf den Knopf, um loszulegen! Ich drücke dir ganz fest die Pfoten! 🦊✨"
    elif lang == "pt":
        msg = "🎉 **Seu quiz personalizado está pronto!**\n\nClique no botão abaixo para começar a jogar! Boa sorte! 🐉✨"
    else:
        msg = "🎉 **Your customized quiz is ready!**\n\nClick the button below to start solving! Good luck! 🦉✨"

    # Stream friendly greeting to user chat
    yield Event(
        content=types.Content(role="model", parts=[types.Part.from_text(text=msg)])
    )

    # Return structured Quiz object as the workflow's terminal output
    yield Event(output=quiz_dict)
@node
async def ask_more_node(ctx: Context, node_input: Any) -> Event:
    """Terminal node for the 'ask_more' route. Gracefully ends the branch."""
    logger.info("Mascot prompt asking for more information.")
    return Event()


# --- ADK 2.0 Workflow Definition ---

root_agent = Workflow(
    name="root_agent",
    description="Interactive School Exam Preparation Companion (Quiz Buddy)",
    edges=[
        Edge(from_node=START, to_node=gather_and_route),
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
        Edge(from_node=quiz_generation, to_node=llm_as_a_judge),
        Edge(from_node=llm_as_a_judge, to_node=quiz_generation, route="retry"),
        Edge(from_node=llm_as_a_judge, to_node=quiz_output_node, route="success"),
    ],
    before_agent_callback=before_agent_callback,
    after_agent_callback=after_agent_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
