# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Canonical fingerprints for short-lived server-side quiz provenance."""

import hashlib
import json
import unicodedata
from typing import Any

VALIDATION_CONTRACT_VERSION = "quiz-validation-v2"


def _canonical_text(value: Any) -> str:
    """Normalize context text without changing meaningful capitalization."""
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).split())


def canonical_json(value: Any) -> str:
    """Serialize JSON-compatible data deterministically for hashing."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def quiz_fingerprint(quiz: dict[str, Any]) -> str:
    """Return a deterministic fingerprint of the normalized public quiz."""
    return _sha256(quiz)


def context_fingerprint(
    *,
    grade: Any,
    subject: Any,
    topic: Any,
    preferred_language: Any,
) -> str:
    """Return a fingerprint for the generation context that must remain stable."""
    context = {
        "grade": _canonical_text(grade),
        "subject": _canonical_text(subject),
        "topic": _canonical_text(topic),
        "preferred_language": _canonical_text(preferred_language),
    }
    return _sha256(context)
