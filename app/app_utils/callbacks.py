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
import hashlib
import logging
import re
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.context import Context
from google.adk.agents.invocation_context import InvocationContext
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import Client
from google.genai import types as genai_types

from app.app_utils.request_context import (
    get_anonymous_id,
    get_client_ip,
    get_client_locale,
)
from app.database.firestore_repo import FirestoreRepository

logger = logging.getLogger(__name__)

# Config caches
_cached_config: dict[str, Any] | None = None
_cached_time: datetime.datetime | None = None

# Local TTL banned signatures cache to guarantee exactly 0 DB/token cost for active bans
_local_banned_cache: dict[str, datetime.datetime] = {}

# Invocation-local accounting state. The `temp:` prefix prevents these values
# from being persisted as durable session state by ADK session services.
_TOKEN_USAGE_STATE_KEY = "temp:foxquiz_token_usage"
_TOKEN_USAGE_FLUSHED_STATE_KEY = "temp:foxquiz_token_usage_flushed"


class SecurityBlockException(Exception):
    """Exception raised when a request is blocked by the security checkpoint or token budgets."""

    def __init__(self, message: str, block_type: str):
        super().__init__(message)
        self.message = message
        self.block_type = (
            block_type  # "BANNED", "MALICIOUS", "OFF_TOPIC", "BUDGET_EXCEEDED"
        )


def record_token_usage(callback_context: CallbackContext, response: Any) -> int:
    """Accumulate token usage from a direct Google GenAI response."""
    usage_metadata = getattr(response, "usage_metadata", None)
    tokens = getattr(usage_metadata, "total_token_count", 0) or 0
    if tokens <= 0:
        return 0

    current_total = callback_context.state.get(_TOKEN_USAGE_STATE_KEY, 0) or 0
    callback_context.state[_TOKEN_USAGE_STATE_KEY] = current_total + tokens
    return tokens


def get_cached_security_config(repo: FirestoreRepository) -> dict[str, Any]:
    """Helper to lazily load and cache the Firestore security configuration."""
    global _cached_config, _cached_time
    now = datetime.datetime.now()
    if (
        _cached_config is None
        or _cached_time is None
        or (now - _cached_time).total_seconds() > 300
    ):
        logger.info("Lazy-loading security configuration from Firestore.")
        _cached_config = repo.get_security_config()
        _cached_time = now
    return _cached_config


async def before_agent_callback(callback_context: CallbackContext) -> None:
    """ADK 2.0 Upstream Guard: Conducts safety scans, Sheriff checks, and token budgets."""
    repo = FirestoreRepository()
    config = get_cached_security_config(repo)

    # 1. Secure IP Hashing (GDPR/LGPD privacy preservation)
    client_ip = get_client_ip()
    salt = config.get("salt", "foxquiz_secret_salt_2026")
    hashed_ip = hashlib.sha256((client_ip + salt).encode("utf-8")).hexdigest()

    locale = get_client_locale()
    locale_suffix = (
        "de" if "de" in locale.lower() else "pt" if "pt" in locale.lower() else "en"
    )

    # 2. Zero-Token Fast Block (Local Active Ban Cache check)
    now = datetime.datetime.now(datetime.UTC)
    if hashed_ip in _local_banned_cache:
        if now < _local_banned_cache[hashed_ip]:
            logger.warning(
                f"Fast-blocking banned client signature {hashed_ip} from local active cache."
            )
            banned_msg = config.get("responses", {}).get(
                f"banned_{locale_suffix}", "Access blocked."
            )
            raise SecurityBlockException(banned_msg, "BANNED")
        else:
            _local_banned_cache.pop(hashed_ip, None)

    # Check database if not in local cache (and seed local cache if banned)
    if repo.is_signature_banned(hashed_ip):
        logger.warning(
            f"Fast-blocking banned client signature {hashed_ip} from Firestore database."
        )
        _local_banned_cache[hashed_ip] = now + datetime.timedelta(hours=24)
        banned_msg = config.get("responses", {}).get(
            f"banned_{locale_suffix}", "Access blocked."
        )
        raise SecurityBlockException(banned_msg, "BANNED")

    # 3. Daily Token Budget Verifications
    anon_id = get_anonymous_id()
    # Personal Limit Check (100k tokens per user per day)
    user_budget = repo.get_token_budget(f"budget_{anon_id}")
    if user_budget.get("tokens_used", 0) >= 100000:
        logger.warning(f"User {anon_id} reached daily token budget limit.")
        msg = config.get("responses", {}).get(
            f"budget_user_{locale_suffix}",
            "Du hast heute schon fleißig gelernt und dein Tageslimit erreicht! 🌙 Komm morgen gerne wieder!",
        )
        raise SecurityBlockException(msg, "BUDGET_EXCEEDED")

    # Global Limit Check (5M tokens per app per day)
    global_budget = repo.get_token_budget("global")
    if global_budget.get("tokens_used", 0) >= 5000000:
        logger.warning("Global application token budget limit reached.")
        msg = config.get("responses", {}).get(
            f"budget_global_{locale_suffix}",
            "Heute waren besonders viele fleißige Lernende unterwegs! 🦉 Bitte versuch es morgen noch einmal.",
        )
        raise SecurityBlockException(msg, "BUDGET_EXCEEDED")

    # 4. Prompt Screening
    prompt = ""
    if callback_context.user_content and callback_context.user_content.parts:
        prompt = "".join(
            [part.text for part in callback_context.user_content.parts if part.text]
        ).strip()

    if not prompt:
        return  # No text to screen (e.g. empty init or media-only invocation)

    # --- Stage 1: Local Regex & Keyword Scanning (Fast Filter) ---
    blocklist_keywords = config.get("blocklist_keywords", [])
    injection_regexes = config.get("injection_regexes", [])

    # Keyword scanning (case-insensitive)
    for kw in blocklist_keywords:
        if kw.lower() in prompt.lower():
            logger.error(f"Safety violation triggered by blocklist keyword: '{kw}'")
            await _handle_safety_violation(
                callback_context,
                repo,
                config,
                hashed_ip,
                prompt,
                "KeywordMatch",
                locale_suffix,
            )

    # Regex matching
    for pattern in injection_regexes:
        if re.search(pattern, prompt, re.IGNORECASE):
            logger.error(
                f"Safety violation triggered by injection regex match: '{pattern}'"
            )
            await _handle_safety_violation(
                callback_context,
                repo,
                config,
                hashed_ip,
                prompt,
                "RegexMatch",
                locale_suffix,
            )

    # --- Stage 2: LLM Classification (Semantic Filter) ---
    try:
        classifier_prompt = config.get("classification_prompt")
        # Call Google GenAI fast model for safe, cost-efficient security classification
        client = Client()
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part.from_text(
                            text=f'{classifier_prompt}\n\nUser input to review: "{prompt}"'
                        )
                    ],
                )
            ],
            config=genai_types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=512,
                thinking_config=genai_types.ThinkingConfig(thinking_budget=256),
            ),
        )
        record_token_usage(callback_context, response)
        response_text = response.text
        if not isinstance(response_text, str) or not response_text.strip():
            raise ValueError("Semantic classifier returned no decision.")

        decision = response_text.strip().upper()
        valid_decisions = {"SAFE", "OFF_TOPIC", "MALICIOUS"}
        if decision not in valid_decisions:
            raise ValueError(
                f"Semantic classifier returned an invalid decision: {decision!r}."
            )

        logger.info(f"Lightweight semantic classifier safety decision: {decision}")

        if decision == "MALICIOUS":
            logger.error(
                "Safety violation triggered by semantic classifier evaluation."
            )
            await _handle_safety_violation(
                callback_context,
                repo,
                config,
                hashed_ip,
                prompt,
                "ClassifierBlock",
                locale_suffix,
            )
        elif decision == "OFF_TOPIC":
            logger.info("Intercepted harmless off-topic prompt.")
            off_topic_msg = config.get("responses", {}).get(
                f"off_topic_{locale_suffix}",
                "Dieser Assistent kann dir leider nur bei der Vorbereitung auf Prüfungen helfen!",
            )
            raise SecurityBlockException(off_topic_msg, "OFF_TOPIC")

    except SecurityBlockException:
        raise
    except Exception as e:
        logger.exception(
            "Semantic safety classification failed. Blocking the request instead of "
            "allowing it without semantic review."
        )
        unavailable_defaults = {
            "de": "Die Sicherheitsprüfung ist vorübergehend nicht verfügbar. Bitte versuche es gleich noch einmal.",
            "pt": "A verificação de segurança está temporariamente indisponível. Tente novamente em instantes.",
            "en": "The safety check is temporarily unavailable. Please try again shortly.",
        }
        unavailable_msg = config.get("responses", {}).get(
            f"classifier_unavailable_{locale_suffix}",
            unavailable_defaults[locale_suffix],
        )
        raise SecurityBlockException(unavailable_msg, "CLASSIFIER_UNAVAILABLE") from e


async def _handle_safety_violation(
    callback_context: CallbackContext,
    repo: FirestoreRepository,
    config: dict[str, Any],
    hashed_ip: str,
    prompt: str,
    violation_type: str,
    locale_suffix: str,
) -> None:
    """Helper to register safety violations, trigger the Sheriff, and block execution."""
    anon_id = get_anonymous_id()
    # 1. Log violation to security_events
    repo.log_security_event(anon_id, hashed_ip, prompt, violation_type)

    # 2. Automated Sheriff Guard Check (Count last hour violations)
    violations_count = repo.get_recent_violations_count(hashed_ip, hours=1)
    logger.warning(
        f"Client {hashed_ip} has committed {violations_count} safety violations in the past hour."
    )

    if violations_count >= 3:
        # The Gavel: Auto-ban signature for 24 hours
        logger.error(
            f"🤠 The Sheriff Guard has banned client {hashed_ip} due to 3 active violations!"
        )
        repo.ban_signature(hashed_ip, duration_hours=24)
        _local_banned_cache[hashed_ip] = datetime.datetime.now(
            datetime.UTC
        ) + datetime.timedelta(hours=24)
        banned_msg = config.get("responses", {}).get(
            f"banned_{locale_suffix}", "Access blocked."
        )
        raise SecurityBlockException(banned_msg, "BANNED")

    # Raise friendly localized block warning response
    block_msg = config.get("responses", {}).get(
        f"injection_{locale_suffix}",
        "Dieser Assistent kann dich nur bei der Vorbereitung auf deine Prüfungen unterstützen.",
    )
    raise SecurityBlockException(block_msg, "MALICIOUS")


async def after_agent_callback(
    callback_context: CallbackContext,
) -> genai_types.Content | None:
    """Flush invocation token usage to the user and global Firestore budgets."""
    if callback_context.state.get(_TOKEN_USAGE_FLUSHED_STATE_KEY, False):
        return None

    invocation_id = callback_context.invocation_id
    total_tokens = callback_context.state.get(_TOKEN_USAGE_STATE_KEY, 0) or 0

    # Include usage emitted by any ADK-managed model calls that may be added to
    # the workflow in the future. Direct GenAI calls are accumulated above.
    for event in callback_context.session.events:
        if event.invocation_id == invocation_id and event.usage_metadata:
            total_tokens += event.usage_metadata.total_token_count or 0

    if total_tokens > 0:
        anon_id = get_anonymous_id()
        logger.info(
            f"Aggregated invocation {invocation_id} used {total_tokens} tokens. Logging to budget."
        )
        repo = FirestoreRepository()
        repo.increment_token_budget(f"budget_{anon_id}", total_tokens)
        repo.increment_token_budget("global", total_tokens)

    callback_context.state[_TOKEN_USAGE_STATE_KEY] = 0
    callback_context.state[_TOKEN_USAGE_FLUSHED_STATE_KEY] = True
    return None


class FoxQuizSecurityPlugin(BasePlugin):
    """Run FoxQuiz security and budget checkpoints around each invocation."""

    def __init__(self) -> None:
        super().__init__(name="foxquiz_security_and_budget")

    async def before_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> genai_types.Content | None:
        callback_context = Context(invocation_context)
        callback_context.state[_TOKEN_USAGE_STATE_KEY] = 0
        callback_context.state[_TOKEN_USAGE_FLUSHED_STATE_KEY] = False
        await before_agent_callback(callback_context)
        return None

    async def after_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> None:
        await after_agent_callback(Context(invocation_context))

    async def on_run_error_callback(
        self,
        *,
        invocation_context: InvocationContext,
        error: Exception,
    ) -> None:
        logger.warning(
            "Flushing token usage after failed invocation %s: %s",
            invocation_context.invocation_id,
            error,
        )
        await after_agent_callback(Context(invocation_context))
