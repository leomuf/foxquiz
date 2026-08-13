# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Privacy-safe structured operational logging.

Events are serialized as one JSON object per line to the process stderr stream.
Cloud Run automatically captures container stdout and stderr and ingests valid
JSON as structured Cloud Logging entries under ``jsonPayload``; this module does
not call the Cloud Logging API directly. Locally, the same events appear only in
the terminal or a redirected log file.
"""

import json
import re
import sys
from typing import Any, Literal

from app.app_utils.build_info import get_build_info
from app.domain.quiz_validation import QuizValidationResult

QuizValidationEvent = Literal[
    "quiz_validation_passed",
    "quiz_validation_failed",
    "quiz_validation_retry_passed",
    "quiz_validation_retry_exhausted",
    "quiz_final_invariant_failed",
]


def _error_code(error: Exception) -> str | None:
    """Return a non-sensitive provider error code when one is available."""
    code: Any = getattr(error, "code", None)
    if callable(code):
        try:
            code = code()
        except Exception:
            return None
    if code is None:
        return None
    name = getattr(code, "name", None)
    value = str(name if name is not None else code)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value):
        return None
    return value


def emit_operational_event(
    *,
    event: str,
    phase: str,
    operation: str,
    error: Exception,
    severity: str = "ERROR",
) -> None:
    """Emit one privacy-safe structured event to the process stderr stream."""
    build_info = get_build_info()
    payload = {
        "severity": severity,
        "event": event,
        "phase": phase,
        "operation": operation,
        "error_type": type(error).__name__,
        "deployment_revision": build_info["short_commit_sha"],
        "service_version": build_info["version"],
    }
    code = _error_code(error)
    if code is not None:
        payload["error_code"] = code
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)


def emit_quiz_validation_event(
    *,
    event: QuizValidationEvent,
    generation_attempt: int,
    result: QuizValidationResult,
) -> None:
    """Emit aggregate quiz-validation diagnostics without generated content."""
    build_info = get_build_info()
    issue_codes = sorted({issue.code.value for issue in result.issues})
    payload = {
        "severity": (
            "ERROR"
            if event
            in {"quiz_validation_retry_exhausted", "quiz_final_invariant_failed"}
            else "WARNING"
            if event == "quiz_validation_failed"
            else "INFO"
        ),
        "event": event,
        "phase": "deterministic_quiz_validation",
        "generation_attempt": max(0, int(generation_attempt)),
        "issue_count": len(result.issues),
        "issue_codes": issue_codes,
        "deployment_revision": build_info["short_commit_sha"],
        "service_version": build_info["version"],
    }
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)
