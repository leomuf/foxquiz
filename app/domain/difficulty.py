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
    def parse_known(cls, value: Any) -> "DifficultyLevel":
        """Parse a semantic code or an exact known legacy presentation value."""
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValueError("Difficulty must be a recognized string value.")

        normalized = " ".join(value.strip().casefold().split())
        known_values = {
            "easy": cls.EASY,
            "🌱 easy": cls.EASY,
            "einfach": cls.EASY,
            "🌱 einfach": cls.EASY,
            "fácil": cls.EASY,
            "🌱 fácil": cls.EASY,
            "medium": cls.MEDIUM,
            "⭐ medium": cls.MEDIUM,
            "mittel": cls.MEDIUM,
            "⭐ mittel": cls.MEDIUM,
            "médio": cls.MEDIUM,
            "⭐ médio": cls.MEDIUM,
            "hard": cls.HARD,
            "🚀 hard": cls.HARD,
            "schwer": cls.HARD,
            "🚀 schwer": cls.HARD,
            "difícil": cls.HARD,
            "🚀 difícil": cls.HARD,
        }
        try:
            return known_values[normalized]
        except KeyError as error:
            raise ValueError("Difficulty must be easy, medium, or hard.") from error

    @classmethod
    def from_raw(
        cls, value: Any, default: "DifficultyLevel" = MEDIUM
    ) -> "DifficultyLevel":
        """Normalize legacy decorated strings or case variants to a canonical level.

        Falls back safely to the specified default for missing or unknown values.
        """
        try:
            return cls.parse_known(value)
        except ValueError:
            return default
