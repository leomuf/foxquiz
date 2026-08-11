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


class QuizQualityFailure(BaseModel):
    """Diagnostic record created when generated quiz quality cannot be verified."""

    quiz_context: QuizContext
    failure_type: Literal["judge_rejected", "judge_exception"]
    judge_attempts: int
    judge_reasons: list[str] = Field(default_factory=list)
    grounding_title: str | None = None
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
