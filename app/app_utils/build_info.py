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

import os
import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as metadata_version

REPOSITORY_URL = "https://github.com/leomuf/foxquiz"
_COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _environment_value(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _installed_version() -> str:
    try:
        return metadata_version("foxquiz")
    except PackageNotFoundError:
        return "dev"


def get_build_info() -> dict[str, str | None]:
    """Return public metadata identifying the exact deployed source revision."""
    app_version = _environment_value("AGENT_VERSION") or _installed_version()
    commit_sha = _environment_value("COMMIT_SHA") or "dev"
    build_time = _environment_value("BUILD_TIME")
    commit_url = (
        f"{REPOSITORY_URL}/commit/{commit_sha}"
        if _COMMIT_PATTERN.fullmatch(commit_sha)
        else None
    )

    return {
        "version": app_version,
        "commit_sha": commit_sha,
        "short_commit_sha": (
            commit_sha[:7] if _COMMIT_PATTERN.fullmatch(commit_sha) else commit_sha
        ),
        "commit_url": commit_url,
        "build_time": build_time,
    }
