# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Pure tests for privacy-safe Gemini token metadata normalization."""

from types import SimpleNamespace

import pytest

from app.app_utils.token_usage import (
    CallStage,
    InvocationTokenUsage,
    TokenUsage,
    normalize_attempt,
)


def test_complete_usage_metadata_is_normalized() -> None:
    usage = TokenUsage.from_usage_metadata(
        SimpleNamespace(
            prompt_token_count=100,
            cached_content_token_count=40,
            candidates_token_count=20,
            thoughts_token_count=30,
            tool_use_prompt_token_count=5,
            total_token_count=155,
        )
    )

    assert usage.as_log_fields() == {
        "prompt_token_count": 100,
        "uncached_prompt_token_count": 60,
        "cached_content_token_count": 40,
        "candidates_token_count": 20,
        "thoughts_token_count": 30,
        "tool_use_prompt_token_count": 5,
        "total_token_count": 155,
    }


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (None, TokenUsage()),
        (SimpleNamespace(total_token_count=None), TokenUsage()),
        (
            SimpleNamespace(
                prompt_token_count=-5,
                cached_content_token_count=20,
                candidates_token_count="7",
                thoughts_token_count=True,
                total_token_count=0,
            ),
            TokenUsage(cached_content_token_count=20),
        ),
    ],
)
def test_missing_partial_and_malformed_metadata_is_safe(
    metadata, expected: TokenUsage
) -> None:
    assert TokenUsage.from_usage_metadata(metadata) == expected


def test_uncached_prompt_count_never_becomes_negative() -> None:
    usage = TokenUsage(prompt_token_count=10, cached_content_token_count=20)

    assert usage.uncached_prompt_token_count == 0


def test_accumulator_keeps_stages_and_retry_counters_independent() -> None:
    accumulator = InvocationTokenUsage()
    accumulator.add_direct(
        CallStage.QUIZ_GENERATOR,
        TokenUsage(prompt_token_count=100, total_token_count=150),
    )
    accumulator.add_direct(
        CallStage.QUIZ_GENERATOR,
        TokenUsage(prompt_token_count=110, total_token_count=160),
    )
    accumulator.add_direct(
        CallStage.ACADEMIC_JUDGE,
        TokenUsage(cached_content_token_count=40, total_token_count=80),
    )

    summary = accumulator.as_summary_fields()
    assert summary["model_call_count"] == 3
    assert summary["cache_hit_model_call_count"] == 1
    assert summary["generator_call_count"] == 2
    assert summary["judge_call_count"] == 1
    assert summary["generator_retry_occurred"] is True
    assert summary["judge_retry_occurred"] is False
    assert summary["total_token_count"] == 390
    assert summary["stage_total_token_counts"]["quiz_generator"] == 310
    assert summary["stage_total_token_counts"]["academic_judge"] == 80

    restored = InvocationTokenUsage.from_state(accumulator.as_state())
    assert restored.as_summary_fields() == summary


def test_accumulator_tracks_every_allowlisted_stage_independently() -> None:
    accumulator = InvocationTokenUsage()

    for token_count, stage in enumerate(CallStage, start=1):
        accumulator.add_direct(stage, TokenUsage(total_token_count=token_count))

    summary = accumulator.as_summary_fields()
    assert summary["model_call_count"] == len(CallStage)
    assert summary["stage_total_token_counts"] == {
        stage.value: token_count for token_count, stage in enumerate(CallStage, start=1)
    }


def test_accumulator_discards_unknown_stage_state() -> None:
    accumulator = InvocationTokenUsage.from_state(
        {
            "totals": {"total_token_count": 10},
            "stages": {
                "quiz_generator": {"calls": 1, "total_token_count": 10},
                "PRIVATE-STAGE-FROM-INPUT": {
                    "calls": 999,
                    "total_token_count": 999,
                },
            },
            "model_call_count": 1,
        }
    )

    assert set(accumulator.stages) == {CallStage.QUIZ_GENERATOR}
    assert "PRIVATE-STAGE-FROM-INPUT" not in str(accumulator.as_state())


@pytest.mark.parametrize("value", [0, 11, -1, True, "1"])
def test_attempt_metadata_rejects_unbounded_or_non_integer_values(value) -> None:
    with pytest.raises(ValueError):
        normalize_attempt(value)


def test_attempt_metadata_accepts_none_and_bounded_integer() -> None:
    assert normalize_attempt(None) is None
    assert normalize_attempt(2) == 2
