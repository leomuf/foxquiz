# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for public build and source-revision metadata.

Purpose:
    Ensure deployments expose version, commit SHA, commit URL, and build time,
    while local development has safe, predictable fallback values.

Boundary:
    Environment variables and installed-package metadata are mocked; no GitHub
    or Google Cloud service is contacted.
"""

from unittest.mock import patch

from app.app_utils.build_info import get_build_info


def test_build_info_uses_deployment_environment(monkeypatch):
    """Verify deployment metadata identifies the exact public Git revision."""
    commit_sha = "5b1b1ed5c11eeaab77430bf304311610c8a0cefa"
    monkeypatch.setenv("AGENT_VERSION", "1.0.4")
    monkeypatch.setenv("COMMIT_SHA", commit_sha)
    monkeypatch.setenv("BUILD_TIME", "2026-08-10T12:00:00Z")

    info = get_build_info()

    assert info == {
        "version": "1.0.4",
        "commit_sha": commit_sha,
        "short_commit_sha": "5b1b1ed",
        "commit_url": f"https://github.com/leomuf/foxquiz/commit/{commit_sha}",
        "build_time": "2026-08-10T12:00:00Z",
    }


def test_build_info_has_safe_local_defaults(monkeypatch):
    """Verify local runs remain identifiable without injected deployment values."""
    monkeypatch.delenv("AGENT_VERSION", raising=False)
    monkeypatch.delenv("COMMIT_SHA", raising=False)
    monkeypatch.delenv("BUILD_TIME", raising=False)

    with patch("app.app_utils.build_info.metadata_version", return_value="1.0.4"):
        info = get_build_info()

    assert info["version"] == "1.0.4"
    assert info["commit_sha"] == "dev"
    assert info["short_commit_sha"] == "dev"
    assert info["commit_url"] is None
    assert info["build_time"] is None
