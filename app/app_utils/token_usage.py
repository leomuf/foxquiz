# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Privacy-safe normalization and aggregation for Gemini token metadata."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

TOKEN_USAGE_SCHEMA_VERSION = 1
MAX_RECORDED_ATTEMPT = 10


class CallStage(StrEnum):
    """Allowlisted direct Gemini call stages."""

    SECURITY_CLASSIFIER = "security_classifier"
    PARAMETER_EXTRACTOR = "parameter_extractor"
    CURRICULUM_EVALUATOR = "curriculum_evaluator"
    MASCOT_PROMPT = "mascot_prompt"
    QUIZ_GENERATOR = "quiz_generator"
    ACADEMIC_JUDGE = "academic_judge"


class TerminalOutcome(StrEnum):
    """Allowlisted invocation outcomes used by aggregate telemetry."""

    SUCCESS = "success"
    NEEDS_INPUT = "needs_input"
    BLOCKED = "blocked"
    QUALITY_FAILURE = "quality_failure"
    ERROR = "error"


CALL_STAGES: tuple[CallStage, ...] = (
    CallStage.SECURITY_CLASSIFIER,
    CallStage.PARAMETER_EXTRACTOR,
    CallStage.CURRICULUM_EVALUATOR,
    CallStage.MASCOT_PROMPT,
    CallStage.QUIZ_GENERATOR,
    CallStage.ACADEMIC_JUDGE,
)

MODEL_BY_STAGE: dict[CallStage, str] = dict.fromkeys(CALL_STAGES, "gemini-2.5-flash")


def normalize_attempt(value: int | None) -> int | None:
    """Return a bounded positive attempt number, or reject invalid metadata."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Attempt metadata must be an integer.")
    if not 1 <= value <= MAX_RECORDED_ATTEMPT:
        raise ValueError(
            f"Attempt metadata must be between 1 and {MAX_RECORDED_ATTEMPT}."
        )
    return value


def _non_negative_int(value: Any) -> int:
    """Normalize provider metadata without allowing malformed negative values."""
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    return max(value, 0)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Normalized numeric fields from one Gemini response."""

    prompt_token_count: int = 0
    cached_content_token_count: int = 0
    candidates_token_count: int = 0
    thoughts_token_count: int = 0
    tool_use_prompt_token_count: int = 0
    total_token_count: int = 0

    @classmethod
    def from_usage_metadata(cls, usage_metadata: Any) -> "TokenUsage":
        """Build a normalized value from SDK metadata or a compatible mock."""
        return cls(
            prompt_token_count=_non_negative_int(
                getattr(usage_metadata, "prompt_token_count", None)
            ),
            cached_content_token_count=_non_negative_int(
                getattr(usage_metadata, "cached_content_token_count", None)
            ),
            candidates_token_count=_non_negative_int(
                getattr(usage_metadata, "candidates_token_count", None)
            ),
            thoughts_token_count=_non_negative_int(
                getattr(usage_metadata, "thoughts_token_count", None)
            ),
            tool_use_prompt_token_count=_non_negative_int(
                getattr(usage_metadata, "tool_use_prompt_token_count", None)
            ),
            total_token_count=_non_negative_int(
                getattr(usage_metadata, "total_token_count", None)
            ),
        )

    @classmethod
    def from_response(cls, response: Any) -> "TokenUsage":
        """Build a normalized value from a Gemini response."""
        return cls.from_usage_metadata(getattr(response, "usage_metadata", None))

    @property
    def uncached_prompt_token_count(self) -> int:
        return max(self.prompt_token_count - self.cached_content_token_count, 0)

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            prompt_token_count=self.prompt_token_count + other.prompt_token_count,
            cached_content_token_count=(
                self.cached_content_token_count + other.cached_content_token_count
            ),
            candidates_token_count=(
                self.candidates_token_count + other.candidates_token_count
            ),
            thoughts_token_count=self.thoughts_token_count + other.thoughts_token_count,
            tool_use_prompt_token_count=(
                self.tool_use_prompt_token_count + other.tool_use_prompt_token_count
            ),
            total_token_count=self.total_token_count + other.total_token_count,
        )

    def as_log_fields(self) -> dict[str, int]:
        """Return the explicit numeric field allowlist for structured events."""
        return {
            "prompt_token_count": self.prompt_token_count,
            "uncached_prompt_token_count": self.uncached_prompt_token_count,
            "cached_content_token_count": self.cached_content_token_count,
            "candidates_token_count": self.candidates_token_count,
            "thoughts_token_count": self.thoughts_token_count,
            "tool_use_prompt_token_count": self.tool_use_prompt_token_count,
            "total_token_count": self.total_token_count,
        }

    def as_state_fields(self) -> dict[str, int]:
        """Serialize only provider fields needed to reconstruct this value."""
        fields = self.as_log_fields()
        fields.pop("uncached_prompt_token_count")
        return fields

    @classmethod
    def from_state_fields(cls, value: Any) -> "TokenUsage":
        if not isinstance(value, dict):
            return cls()
        return cls.from_usage_metadata(_MappingAttributes(value))


class _MappingAttributes:
    """Expose an allowlisted mapping through metadata-style attributes."""

    def __init__(self, value: dict[str, Any]) -> None:
        self._value = value

    def __getattr__(self, name: str) -> Any:
        return self._value.get(name)


@dataclass(slots=True)
class InvocationTokenUsage:
    """Invocation-local numeric usage, split across allowlisted call stages."""

    totals: TokenUsage = field(default_factory=TokenUsage)
    stages: dict[CallStage, TokenUsage] = field(default_factory=dict)
    stage_call_counts: dict[CallStage, int] = field(default_factory=dict)
    model_call_count: int = 0
    cache_hit_model_call_count: int = 0
    adk_managed_model_call_count: int = 0

    def add_direct(self, call_stage: CallStage, usage: TokenUsage) -> None:
        self.totals = self.totals + usage
        self.stages[call_stage] = self.stages.get(call_stage, TokenUsage()) + usage
        self.stage_call_counts[call_stage] = (
            self.stage_call_counts.get(call_stage, 0) + 1
        )
        self.model_call_count += 1
        if usage.cached_content_token_count > 0:
            self.cache_hit_model_call_count += 1

    def add_adk_managed(self, usage: TokenUsage) -> None:
        """Add usage from an ADK event, which direct calls never populate."""
        self.totals = self.totals + usage
        self.model_call_count += 1
        self.adk_managed_model_call_count += 1
        if usage.cached_content_token_count > 0:
            self.cache_hit_model_call_count += 1

    def as_state(self) -> dict[str, Any]:
        """Return JSON-compatible temporary ADK state."""
        return {
            "totals": self.totals.as_state_fields(),
            "stages": {
                stage.value: {
                    "calls": self.stage_call_counts.get(stage, 0),
                    **usage.as_state_fields(),
                }
                for stage, usage in self.stages.items()
            },
            "model_call_count": self.model_call_count,
            "cache_hit_model_call_count": self.cache_hit_model_call_count,
            "adk_managed_model_call_count": self.adk_managed_model_call_count,
        }

    @classmethod
    def from_state(cls, value: Any) -> "InvocationTokenUsage":
        """Rebuild an accumulator while discarding non-allowlisted state."""
        if isinstance(value, int) and not isinstance(value, bool):
            return cls(totals=TokenUsage(total_token_count=max(value, 0)))
        if not isinstance(value, dict):
            return cls()

        accumulator = cls(
            totals=TokenUsage.from_state_fields(value.get("totals")),
            model_call_count=_non_negative_int(value.get("model_call_count")),
            cache_hit_model_call_count=_non_negative_int(
                value.get("cache_hit_model_call_count")
            ),
            adk_managed_model_call_count=_non_negative_int(
                value.get("adk_managed_model_call_count")
            ),
        )
        raw_stages = value.get("stages")
        if not isinstance(raw_stages, dict):
            return accumulator
        for raw_stage, raw_usage in raw_stages.items():
            try:
                stage = CallStage(raw_stage)
            except (TypeError, ValueError):
                continue
            if not isinstance(raw_usage, dict):
                continue
            accumulator.stages[stage] = TokenUsage.from_state_fields(raw_usage)
            accumulator.stage_call_counts[stage] = _non_negative_int(
                raw_usage.get("calls")
            )
        return accumulator

    def as_summary_fields(self) -> dict[str, Any]:
        generator_calls = self.stage_call_counts.get(CallStage.QUIZ_GENERATOR, 0)
        judge_calls = self.stage_call_counts.get(CallStage.ACADEMIC_JUDGE, 0)
        return {
            "model_call_count": self.model_call_count,
            "adk_managed_model_call_count": self.adk_managed_model_call_count,
            "cache_hit_model_call_count": self.cache_hit_model_call_count,
            "generator_call_count": generator_calls,
            "judge_call_count": judge_calls,
            "generator_retry_occurred": generator_calls > 1,
            "judge_retry_occurred": judge_calls > 1,
            "stage_total_token_counts": {
                stage.value: self.stages.get(stage, TokenUsage()).total_token_count
                for stage in CALL_STAGES
            },
            **self.totals.as_log_fields(),
        }
