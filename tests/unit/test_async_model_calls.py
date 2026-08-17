# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Regression guard for non-blocking Gemini calls in async request paths."""

from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("relative_path", "expected_async_calls"),
    [
        ("app/agent.py", 6),
        ("app/app_utils/callbacks.py", 1),
    ],
)
def test_async_request_paths_await_gemini_calls(
    relative_path: str, expected_async_calls: int
) -> None:
    """Prevent synchronous SDK calls from starving the FastAPI event loop."""
    project_root = Path(__file__).parents[2]
    source = (project_root / relative_path).read_text(encoding="utf-8")

    assert "client.models.generate_content(" not in source
    assert (
        source.count("await client.aio.models.generate_content(")
        == expected_async_calls
    )
