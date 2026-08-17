# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Optional live contract check for Google GenAI usage metadata fields."""

import pytest
from google.genai import Client, types

from app.app_utils.token_usage import TokenUsage


@pytest.mark.google_cloud
def test_vertex_response_exposes_normalizable_usage_metadata() -> None:
    """Confirm SDK attributes exist without asserting unstable token counts."""
    client = Client()
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="Reply with the single word OK.",
            config=types.GenerateContentConfig(
                max_output_tokens=8,
                temperature=0.0,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
    finally:
        client.close()

    usage_metadata = response.usage_metadata
    assert usage_metadata is not None
    for field_name in (
        "prompt_token_count",
        "cached_content_token_count",
        "candidates_token_count",
        "thoughts_token_count",
        "tool_use_prompt_token_count",
        "total_token_count",
    ):
        assert hasattr(usage_metadata, field_name)

    normalized = TokenUsage.from_response(response)
    assert normalized.total_token_count > 0
