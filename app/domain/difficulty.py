# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Stable semantic difficulty codes for FoxQuiz contracts."""

from enum import StrEnum
from typing import Any


class DifficultyLevel(StrEnum):
    """Semantic difficulty codes used at API, workflow, and persistence boundaries."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"

    @classmethod
    def from_raw(
        cls, value: Any, default: "DifficultyLevel" = MEDIUM
    ) -> "DifficultyLevel":
        """Normalize legacy decorated strings or case variants to a canonical level.

        Falls back safely to the specified default for missing or unknown values.
        """
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            return default

        stripped = value.strip().casefold()
        if stripped in {cls.EASY.value, "easy"}:
            return cls.EASY
        if stripped in {cls.MEDIUM.value, "medium"}:
            return cls.MEDIUM
        if stripped in {cls.HARD.value, "hard"}:
            return cls.HARD

        # Backward compatibility with legacy Firestore-stored quizzes
        if (
            "easy" in stripped
            or "einfach" in stripped
            or "fácil" in stripped
            or "🌱" in value
        ):
            return cls.EASY
        if (
            "hard" in stripped
            or "schwer" in stripped
            or "difícil" in stripped
            or "🚀" in value
        ):
            return cls.HARD
        if (
            "medium" in stripped
            or "mittel" in stripped
            or "médio" in stripped
            or "⭐" in value
        ):
            return cls.MEDIUM

        return default
