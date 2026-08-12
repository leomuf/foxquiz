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
import logging
import os
import re
from typing import Any, NoReturn

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.app_utils.operational_logging import emit_operational_event
from app.app_utils.typing import QuizContext, QuizQualityFailure

logger = logging.getLogger(__name__)

SHARED_QUIZ_TTL_DAYS = 30
TRANSIENT_BUDGET_TTL_DAYS = 7

REQUIRED_SECURITY_RESPONSE_KEYS = frozenset(
    f"{response}_{locale}"
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
)


class SecurityConfigurationError(RuntimeError):
    """Raised when the private Firestore security configuration is invalid."""


def validate_security_config(config: Any) -> dict[str, Any]:
    """Validate the private security contract without exposing its contents."""
    if not isinstance(config, dict):
        raise SecurityConfigurationError("Security configuration must be a mapping.")

    for field in ("classification_prompt", "salt"):
        if not isinstance(config.get(field), str) or not config[field].strip():
            raise SecurityConfigurationError(
                f"Security configuration field {field!r} must be a non-empty string."
            )

    for field in ("blocklist_keywords", "injection_regexes"):
        values = config.get(field)
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(value, str) and value.strip() for value in values)
        ):
            raise SecurityConfigurationError(
                f"Security configuration field {field!r} must be an array of non-empty strings."
            )

    try:
        for pattern in config["injection_regexes"]:
            re.compile(pattern)
    except re.error as error:
        raise SecurityConfigurationError(
            "Security configuration contains an invalid injection regex."
        ) from error

    responses = config.get("responses")
    if not isinstance(responses, dict):
        raise SecurityConfigurationError(
            "Security configuration field 'responses' must be a mapping."
        )
    missing_responses = sorted(
        key
        for key in REQUIRED_SECURITY_RESPONSE_KEYS
        if not isinstance(responses.get(key), str) or not responses[key].strip()
    )
    if missing_responses:
        raise SecurityConfigurationError(
            "Security configuration is missing required response keys: "
            + ", ".join(missing_responses)
        )

    return config


class FirestorePersistenceError(RuntimeError):
    """Raised when a named Firestore operation fails."""

    def __init__(self, operation: str, phase: str):
        super().__init__("Persistent storage is temporarily unavailable.")
        self.operation = operation
        self.phase = phase


# Fallback in-memory database for testing and local runs without Google Cloud credentials
_mock_db: dict[str, dict[str, Any]] = {
    "quizzes": {},
    "budgets": {
        "global": {
            "tokens_used": 0,
            "last_reset_date": datetime.date.today().isoformat(),
        }
    },
    "feedback_logs": {},
    "quiz_quality_failures": {},
    "feedback_metrics": {
        "satisfaction": {"thumbs_up_count": 0, "thumbs_down_count": 0}
    },
    "system_config": {
        "security": {
            "classification_prompt": "Test classifier prompt with SAFE, OFF_TOPIC, MALICIOUS, and PII outcomes.",
            "blocklist_keywords": ["test-blocked-keyword"],
            "injection_regexes": [r"\btest-injection-pattern\b"],
            "salt": "test-only-security-salt",
            "responses": {
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
            },
        }
    },
    "security_events": {},
    "banned_signatures": {},
}


class FirestoreRepository:
    """Repository for Firestore, with an explicit in-memory mode for tests."""

    def __init__(self, force_mock: bool = False):
        self.use_mock = force_mock or os.getenv("INTEGRATION_TEST") == "TRUE"
        self.client = None

        if not self.use_mock:
            try:
                # Attempt to initialize real Firestore client
                self.client = firestore.Client()
                logger.info("Firestore client initialized successfully.")
            except Exception as e:
                self._raise_persistence_error(
                    "initialize_client", "client_initialization", e
                )

    def _raise_persistence_error(
        self, operation: str, phase: str, error: Exception
    ) -> NoReturn:
        emit_operational_event(
            event="firestore_operation_failed",
            phase=phase,
            operation=operation,
            error=error,
        )
        raise FirestorePersistenceError(operation, phase) from error

    def _get_mock_doc(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        return _mock_db.get(collection, {}).get(doc_id)

    def _set_mock_doc(
        self, collection: str, doc_id: str, data: dict[str, Any], merge: bool = True
    ):
        if collection not in _mock_db:
            _mock_db[collection] = {}
        if merge and doc_id in _mock_db[collection]:
            _mock_db[collection][doc_id].update(data)
        else:
            _mock_db[collection][doc_id] = data

    def _transient_budget_expiration(
        self, budget_id: str
    ) -> datetime.datetime | str | None:
        if not budget_id.startswith("budget_transient_"):
            return None

        expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
            days=TRANSIENT_BUDGET_TTL_DAYS
        )
        return expires_at.isoformat() if self.use_mock else expires_at

    def _new_budget_data(self, budget_id: str, today_iso: str) -> dict[str, Any]:
        data: dict[str, Any] = {
            "tokens_used": 0,
            "last_reset_date": today_iso,
        }
        expires_at = self._transient_budget_expiration(budget_id)
        if expires_at is not None:
            data["expires_at"] = expires_at
        return data

    # --- 1. Shared Quizzes ---
    def get_shared_quiz(self, quiz_id: str) -> dict[str, Any] | None:
        """Fetch a frozen shared quiz by its unique ID."""
        if self.use_mock:
            quiz = self._get_mock_doc("quizzes", quiz_id)
            if quiz:
                # Check for expiration
                expires_at_str = quiz.get("expires_at")
                if expires_at_str:
                    expires_at = datetime.datetime.fromisoformat(expires_at_str)
                    if datetime.datetime.now(datetime.UTC) > expires_at:
                        # Expired, clean up and return None
                        _mock_db["quizzes"].pop(quiz_id, None)
                        return None
                return quiz.get("quiz_data")
            return None

        try:
            doc_ref = self.client.collection("quizzes").document(quiz_id)
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                # Firestore Native TTL handles physical deletion, but we check logically too
                expires_at = data.get("expires_at")
                if expires_at and expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=datetime.UTC)
                if expires_at and datetime.datetime.now(datetime.UTC) > expires_at:
                    return None
                return data.get("quiz_data")
            return None
        except Exception as e:
            self._raise_persistence_error("read_shared_quiz", "quiz_persistence", e)

    def save_shared_quiz(
        self,
        quiz_id: str,
        quiz_data: dict[str, Any],
        ttl_days: int = SHARED_QUIZ_TTL_DAYS,
    ) -> bool:
        """Store a frozen quiz object in Firestore with an expiration timestamp."""
        now = datetime.datetime.now(datetime.UTC)
        expires_at = now + datetime.timedelta(days=ttl_days)
        data = {
            "quiz_id": quiz_id,
            "quiz_data": quiz_data,
            "created_at": now.isoformat() if self.use_mock else now,
            "expires_at": expires_at.isoformat() if self.use_mock else expires_at,
        }

        if self.use_mock:
            self._set_mock_doc("quizzes", quiz_id, data, merge=False)
            return True

        try:
            doc_ref = self.client.collection("quizzes").document(quiz_id)
            doc_ref.set(data)
            return True
        except Exception as e:
            self._raise_persistence_error("save_shared_quiz", "quiz_persistence", e)

    # --- 2. Token Budgets ---
    def get_token_budget(self, budget_id: str) -> dict[str, Any]:
        """Fetch token budget stats for a user (anonymous_id) or the global key."""
        today_iso = datetime.date.today().isoformat()
        default_budget = self._new_budget_data(budget_id, today_iso)

        if self.use_mock:
            budget = self._get_mock_doc("budgets", budget_id)
            if not budget or budget.get("last_reset_date") != today_iso:
                # Reset or initialize
                self._set_mock_doc("budgets", budget_id, default_budget, merge=False)
                return default_budget
            return budget

        try:
            doc_ref = self.client.collection("budgets").document(budget_id)
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                if data.get("last_reset_date") != today_iso:
                    # Daily reset required
                    doc_ref.set(default_budget)
                    return default_budget

                # Backfill TTL on legacy transient documents when next accessed.
                expires_at = self._transient_budget_expiration(budget_id)
                if expires_at is not None and not data.get("expires_at"):
                    doc_ref.set({"expires_at": expires_at}, merge=True)
                    data["expires_at"] = expires_at
                return data
            # Initialize new budget entry
            doc_ref.set(default_budget)
            return default_budget
        except Exception as e:
            phase = (
                "global_budget_check" if budget_id == "global" else "user_budget_check"
            )
            self._raise_persistence_error("read_token_budget", phase, e)

    def increment_token_budget(self, budget_id: str, tokens_to_add: int) -> bool:
        """Atomically increment token usage for a user or global budget."""
        today_iso = datetime.date.today().isoformat()

        if self.use_mock:
            budget = self.get_token_budget(budget_id)
            budget["tokens_used"] += tokens_to_add
            self._set_mock_doc("budgets", budget_id, budget)
            return True

        try:
            doc_ref = self.client.collection("budgets").document(budget_id)

            # Use transactional / atomic update if reset date is correct
            @firestore.transactional
            def update_tx(transaction, ref, tokens):
                snapshot = ref.get(transaction=transaction)
                data = snapshot.to_dict() if snapshot.exists else {}
                if not snapshot.exists or data.get("last_reset_date") != today_iso:
                    # Reset
                    reset_budget = self._new_budget_data(budget_id, today_iso)
                    reset_budget["tokens_used"] = tokens
                    transaction.set(ref, reset_budget)
                else:
                    updates: dict[str, Any] = {
                        "tokens_used": data.get("tokens_used", 0) + tokens
                    }
                    expires_at = self._transient_budget_expiration(budget_id)
                    if expires_at is not None and not data.get("expires_at"):
                        updates["expires_at"] = expires_at
                    transaction.update(ref, updates)

            transaction = self.client.transaction()
            update_tx(transaction, doc_ref, tokens_to_add)
            return True
        except Exception as e:
            self._raise_persistence_error(
                "increment_token_budget", "token_usage_flush", e
            )

    # --- 3. Feedback Logs ---
    def save_feedback_log(
        self,
        score: int,
        text: str,
        session_id: str,
        anonymous_id: str,
        quiz_data: dict[str, Any] | None = None,
        quiz_context: QuizContext | None = None,
    ) -> str:
        """Save feedback log following spec: positive feedback only increments global stats, negative stores quiz data."""
        log_id = f"fb_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(4).hex()}"
        now = datetime.datetime.now(datetime.UTC)

        # 1. Thumbs-Up (Positive): Do NOT store individual logs/quiz info, only increment counter
        if score > 0:
            if self.use_mock:
                metrics = self._get_mock_doc("feedback_metrics", "satisfaction") or {
                    "thumbs_up_count": 0,
                    "thumbs_down_count": 0,
                }
                metrics["thumbs_up_count"] += 1
                self._set_mock_doc("feedback_metrics", "satisfaction", metrics)
                return log_id
            try:
                metrics_ref = self.client.collection("feedback_metrics").document(
                    "satisfaction"
                )
                metrics_ref.set({"thumbs_up_count": firestore.Increment(1)}, merge=True)
                return log_id
            except Exception as e:
                self._raise_persistence_error(
                    "increment_thumbs_up_metric", "feedback_persistence", e
                )

        # 2. Thumbs-Down (Negative): Store detailed log with complete quiz context
        data = {
            "log_id": log_id,
            "score": score,
            "text": text,
            "session_id": session_id,
            "anonymous_id": anonymous_id,
            "timestamp": now.isoformat() if self.use_mock else now,
            "quiz_data": quiz_data,
            "grade": quiz_context.grade if quiz_context else None,
            "subject": quiz_context.subject if quiz_context else None,
            "topic": quiz_context.topic if quiz_context else None,
            "preferred_language": (
                quiz_context.preferred_language if quiz_context else None
            ),
        }

        if self.use_mock:
            self._set_mock_doc("feedback_logs", log_id, data, merge=False)
            metrics = self._get_mock_doc("feedback_metrics", "satisfaction") or {
                "thumbs_up_count": 0,
                "thumbs_down_count": 0,
            }
            metrics["thumbs_down_count"] += 1
            self._set_mock_doc("feedback_metrics", "satisfaction", metrics)
            return log_id

        try:
            # Save feedback document
            doc_ref = self.client.collection("feedback_logs").document(log_id)
            doc_ref.set(data)

            # Atomically update global stats
            metrics_ref = self.client.collection("feedback_metrics").document(
                "satisfaction"
            )
            metrics_ref.set({"thumbs_down_count": firestore.Increment(1)}, merge=True)
            return log_id
        except Exception as e:
            self._raise_persistence_error(
                "save_thumbs_down_feedback", "feedback_persistence", e
            )

    def save_quiz_quality_failure(self, failure: QuizQualityFailure) -> str:
        """Persist a structured diagnostic record for an unverified quiz."""
        failure_id = (
            f"qf_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_"
            f"{os.urandom(4).hex()}"
        )
        data = failure.model_dump()
        data["failure_id"] = failure_id
        if self.use_mock:
            data["timestamp"] = failure.timestamp.isoformat()
            self._set_mock_doc("quiz_quality_failures", failure_id, data, merge=False)
            return failure_id

        try:
            doc_ref = self.client.collection("quiz_quality_failures").document(
                failure_id
            )
            doc_ref.set(data)
            return failure_id
        except Exception as e:
            self._raise_persistence_error(
                "save_quiz_quality_failure", "quality_diagnostic_persistence", e
            )

    def get_satisfaction_metrics(self) -> dict[str, int]:
        """Fetch the atomic thumbs up / down metrics."""
        default_metrics = {"thumbs_up_count": 0, "thumbs_down_count": 0}
        if self.use_mock:
            return (
                self._get_mock_doc("feedback_metrics", "satisfaction")
                or default_metrics
            )

        try:
            doc_ref = self.client.collection("feedback_metrics").document(
                "satisfaction"
            )
            doc = doc_ref.get()
            if doc.exists:
                return doc.to_dict()
            return default_metrics
        except Exception as e:
            self._raise_persistence_error(
                "read_satisfaction_metrics", "feedback_persistence", e
            )

    # --- 4. Dynamic Security Configuration ---
    def get_security_config(self) -> dict[str, Any]:
        """Fetch and validate the private dynamic security configuration."""
        if self.use_mock:
            return validate_security_config(
                self._get_mock_doc("system_config", "security")
            )

        try:
            doc_ref = self.client.collection("system_config").document("security")
            doc = doc_ref.get()
        except Exception as e:
            self._raise_persistence_error(
                "load_security_config", "security_config_load", e
            )

        if not doc.exists:
            raise SecurityConfigurationError(
                "Required security configuration document is missing."
            )
        config = validate_security_config(doc.to_dict())
        logger.info("Private security configuration validation passed.")
        return config

    # --- 5. Security Events (Violations) ---
    def log_security_event(
        self, anonymous_id: str, hashed_ip: str, blocked_input: str, violation_type: str
    ) -> str:
        """Write an audit log for security violations."""
        event_id = f"ev_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(4).hex()}"
        now = datetime.datetime.now(datetime.UTC)
        data = {
            "event_id": event_id,
            "anonymous_id": anonymous_id,
            "hashed_ip": hashed_ip,
            "blocked_input": blocked_input,
            "timestamp": now.isoformat() if self.use_mock else now,
            "violation_type": violation_type,
        }

        if self.use_mock:
            self._set_mock_doc("security_events", event_id, data, merge=False)
            return event_id

        try:
            doc_ref = self.client.collection("security_events").document(event_id)
            doc_ref.set(data)
            return event_id
        except Exception as e:
            self._raise_persistence_error(
                "write_security_event", "security_event_write", e
            )

    def get_recent_violations_count(self, hashed_ip: str, hours: int = 1) -> int:
        """Retrieve the count of security violations for a hashed IP signature in the past hour."""
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=hours)

        if self.use_mock:
            count = 0
            for event in _mock_db.get("security_events", {}).values():
                if event.get("hashed_ip") == hashed_ip:
                    evt_time = datetime.datetime.fromisoformat(event.get("timestamp"))
                    if evt_time > cutoff:
                        count += 1
            return count

        try:
            events_ref = self.client.collection("security_events")
            query = events_ref.where(
                filter=FieldFilter("hashed_ip", "==", hashed_ip)
            ).where(filter=FieldFilter("timestamp", ">=", cutoff))
            docs = query.stream()
            return len(list(docs))
        except Exception as e:
            self._raise_persistence_error(
                "count_recent_security_violations", "violation_count", e
            )

    # --- 6. Banned Signatures ---
    def is_signature_banned(self, hashed_ip: str) -> bool:
        """Check if a hashed IP is currently banned."""
        now = datetime.datetime.now(datetime.UTC)

        if self.use_mock:
            ban = self._get_mock_doc("banned_signatures", hashed_ip)
            if ban:
                expires_at = datetime.datetime.fromisoformat(ban.get("expires_at"))
                if now > expires_at:
                    # Clean up expired ban
                    _mock_db["banned_signatures"].pop(hashed_ip, None)
                    return False
                return True
            return False

        try:
            doc_ref = self.client.collection("banned_signatures").document(hashed_ip)
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                expires_at = data.get("expires_at")
                if expires_at and expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=datetime.UTC)
                if expires_at and now > expires_at:
                    # Ban expired
                    doc_ref.delete()
                    return False
                return True
            return False
        except Exception as e:
            self._raise_persistence_error("check_banned_signature", "ban_check", e)

    def ban_signature(self, hashed_ip: str, duration_hours: int = 24) -> bool:
        """Ban a hashed IP signature for a specified duration."""
        now = datetime.datetime.now(datetime.UTC)
        expires_at = now + datetime.timedelta(hours=duration_hours)
        data = {
            "hashed_ip": hashed_ip,
            "banned_at": now.isoformat() if self.use_mock else now,
            "expires_at": expires_at.isoformat() if self.use_mock else expires_at,
        }

        if self.use_mock:
            self._set_mock_doc("banned_signatures", hashed_ip, data, merge=False)
            return True

        try:
            doc_ref = self.client.collection("banned_signatures").document(hashed_ip)
            doc_ref.set(data)
            return True
        except Exception as e:
            self._raise_persistence_error("write_banned_signature", "ban_write", e)
