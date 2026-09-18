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
import uuid
from collections.abc import Mapping
from typing import (
    Any,
    Literal,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class QuizContext(BaseModel):
    """Request-scoped quiz parameters shared by feedback and quality diagnostics."""

    grade: str
    subject: str
    topic: str
    preferred_language: str = "en"

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> "QuizContext":
        """Reconstruct a typed quiz context from JSON-serializable ADK state."""
        return cls(
            grade=str(state.get("grade") or ""),
            subject=str(state.get("subject") or ""),
            topic=str(state.get("topic") or ""),
            preferred_language=str(state.get("preferred_language") or "en"),
        )


class JudgeHistoryEntry(BaseModel):
    """Privacy-safe record of one structured Judge decision."""

    model_config = ConfigDict(extra="forbid", strict=True)

    attempt: int = Field(ge=1, le=10)
    passed: bool
    issue_codes: list[str] = Field(default_factory=list, max_length=20)
    question_indices: list[int] = Field(default_factory=list, max_length=10)
    selected_route: Literal[
        "success", "targeted", "full_regeneration", "quality_failure"
    ]


class RepairHistoryEntry(BaseModel):
    """Privacy-safe record of one deterministic or academic repair attempt."""

    model_config = ConfigDict(extra="forbid", strict=True)

    attempt: int = Field(ge=1, le=10)
    kind: Literal["targeted", "full_regeneration"]
    issue_codes: list[str] = Field(default_factory=list, max_length=20)
    question_indices: list[int] = Field(default_factory=list, max_length=10)
    result: Literal["applied", "failed"]


class UsageSummary(BaseModel):
    """Bounded token summary persisted with a quality failure."""

    model_config = ConfigDict(extra="forbid", strict=True)

    model_call_count: int = Field(ge=0)
    prompt_token_count: int = Field(ge=0)
    candidate_token_count: int = Field(ge=0)
    thoughts_token_count: int = Field(ge=0)
    total_token_count: int = Field(ge=0)
    stage_total_token_counts: dict[str, int] = Field(default_factory=dict)


class QuizQualityFailure(BaseModel):
    """Versioned diagnostic record created when quiz quality cannot be verified."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[2] = 2
    failure_type: Literal[
        "deterministic_validation_failed",
        "final_invariant_failed",
        "judge_rejected",
        "judge_exception",
    ]
    quiz_context: QuizContext
    generation_attempts: int = Field(ge=0, le=10)
    judge_attempts: int = Field(ge=0, le=10)
    academic_repair_attempts: int = Field(ge=0, le=10)
    deterministic_repair_attempts: int = Field(ge=0, le=10)
    judge_history: list[JudgeHistoryEntry] = Field(default_factory=list, max_length=10)
    repair_history: list[RepairHistoryEntry] = Field(
        default_factory=list, max_length=10
    )
    normalization_failures: list[dict[str, Any]] = Field(
        default_factory=list, max_length=20
    )
    usage_summary: UsageSummary
    duration_ms: int = Field(ge=0, le=3_600_000)
    service_version: str = Field(min_length=1, max_length=100)
    deployment_revision: str = Field(min_length=1, max_length=100)
    grounding_title: str | None = Field(default=None, max_length=500)
    grounding_discarded: bool = False
    timestamp: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )


class Feedback(BaseModel):
    """Represents feedback for a conversation."""

    score: int | float
    text: str | None = ""
    log_type: Literal["feedback"] = "feedback"
    service_name: Literal["foxquiz"] = "foxquiz"
    user_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    quiz_data: dict[str, Any] | None = None
    quiz_context: QuizContext | None = None
