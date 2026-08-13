# Difficulty API Cleanup — Implementation Plan

## Status

Planned. This document describes a future API cleanup and must not be treated
as an already implemented contract.

## Problem

FoxQuiz currently returns presentation-oriented difficulty values in quiz JSON:

```json
{
  "difficulty": "🚀 Hard"
}
```

This combines three separate concerns:

- the stable machine meaning (`hard`);
- an English display label (`Hard`);
- a visual decoration (`🚀`).

As a result, backend logic, LLM prompts, the academic Judge, persisted shared
quizzes, tests, and frontend presentation all depend on one decorated English
string. A visual or localization change can therefore require backend and API
changes even though the semantic difficulty has not changed.

## Goal

Use stable, language-neutral difficulty codes at every API and persistence
boundary:

```text
easy
medium
hard
```

The frontend alone will translate and decorate these values for learners:

```text
hard -> 🚀 Schwer
hard -> 🚀 Hard
hard -> 🚀 Difícil
```

The cleanup must preserve adaptive-learning behavior, grade-relative hard mode,
existing shared links, offline exports, and all supported languages.

## Non-Goals

This cleanup will not:

- change when Easy, Medium, or Hard mode is selected;
- change the number or structure of quiz questions;
- permit Hard mode to use content from a higher grade;
- change the LLM model;
- deploy FoxQuiz or modify Google Cloud infrastructure;
- use Terraform.

## Proposed API Contract

Define one semantic enum for quiz difficulty:

```python
from enum import StrEnum


class DifficultyLevel(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
```

Use the enum in the public quiz model:

```python
class Quiz(BaseModel):
    title: str
    questions: list[QuizQuestion]
    difficulty: DifficultyLevel
```

The backend response and persisted quiz data will contain only the stable code:

```json
{
  "title": "O ciclo de vida das plantas",
  "difficulty": "hard",
  "questions": []
}
```

The adaptive difficulty selection request will continue to use `medium` or
`hard`. Easy mode remains score-driven and is not offered in the high-score
choice modal.

## Implementation Steps

### 1. Introduce the semantic backend enum

- Add `DifficultyLevel` as a `StrEnum` in the appropriate domain/model module.
- Change `Quiz.difficulty` from an optional free-form string to
  `DifficultyLevel`.
- Make the deterministic adaptive helper return `DifficultyLevel` members.
- Keep `hard` as the only canonical hard-mode selection value.
- Reject or safely default unknown selection values instead of accepting
  undocumented aliases.

### 2. Remove presentation strings from backend decisions

- Replace `🌱 Easy`, `⭐ Medium`, and `🚀 Hard` in backend state and output with
  `easy`, `medium`, and `hard`.
- Update generator instructions to describe the semantic level without making
  the LLM responsible for emoji or localization.
- Deterministically overwrite or construct the final difficulty value after
  generation so model output cannot change the selected adaptive mode.
- Consider separating the model-generated candidate schema from the public
  `Quiz` schema so the generator does not need to emit difficulty at all.

### 3. Update the academic Judge contract

- Pass the expected `DifficultyLevel` explicitly to the Judge.
- Define `hard` as increased cognitive depth within the requested grade's
  authoritative curriculum scope.
- Prohibit interpreting `hard` as permission to use higher-grade content.
- Reject a candidate when its semantic level differs from the expected level,
  is too easy for the chosen mode, or violates the curriculum scope.
- Do not ask the Judge to evaluate emojis or translated difficulty labels.

### 4. Move all visual presentation to the frontend

Create a frontend presentation map for every supported language:

```javascript
const difficultyPresentation = {
    de: {
        easy: { emoji: "🌱", label: "Einfach" },
        medium: { emoji: "⭐", label: "Mittel" },
        hard: { emoji: "🚀", label: "Schwer" }
    },
    en: {
        easy: { emoji: "🌱", label: "Easy" },
        medium: { emoji: "⭐", label: "Medium" },
        hard: { emoji: "🚀", label: "Hard" }
    },
    pt: {
        easy: { emoji: "🌱", label: "Fácil" },
        medium: { emoji: "⭐", label: "Médio" },
        hard: { emoji: "🚀", label: "Difícil" }
    }
};
```

- Use the map for the quiz badge, summary badge, tooltips, and offline export.
- Keep visible labels localized while API requests send stable values.
- Provide an explicit fallback for unknown values without displaying raw
  backend content.
- Keep emoji decoration out of accessibility labels when it would cause
  redundant screen-reader output.

### 5. Preserve existing shared quizzes during migration

Shared quizzes created before this cleanup may remain available in Firestore
for up to 30 days and can contain decorated legacy values. Add a temporary
normalization boundary when a quiz is loaded:

```javascript
function normalizeDifficulty(value) {
    const legacyValues = {
        "🌱 Easy": "easy",
        "⭐ Medium": "medium",
        "🚀 Hard": "hard"
    };
    return legacyValues[value] || value;
}
```

- Normalize legacy data only when reading it.
- Persist all newly created and shared quizzes using the new semantic codes.
- Verify that previously shared links still render localized badges.
- Retain the inexpensive compatibility reader beyond 30 days if offline or
  externally copied quiz JSON should remain usable indefinitely.

### 6. Update tests and behavioral evaluations

Credential-free tests must cover:
