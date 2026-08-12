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

import json
import re
import sys
from typing import Any

from app.app_utils.build_info import get_build_info


def _error_code(error: Exception) -> str | None:
    """Return a non-sensitive provider error code when one is available."""
    code: Any = getattr(error, "code", None)
    if callable(code):
        try:
            code = code()
        except Exception:
            return None
    if code is None:
        return None
    name = getattr(code, "name", None)
    value = str(name if name is not None else code)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value):
        return None
    return value


def emit_operational_event(
    *,
    event: str,
    phase: str,
    operation: str,
    error: Exception,
    severity: str = "ERROR",
) -> None:
    """Emit one privacy-safe JSON event for Cloud Run structured logging."""
    build_info = get_build_info()
    payload = {
        "severity": severity,
        "event": event,
        "phase": phase,
        "operation": operation,
        "error_type": type(error).__name__,
        "deployment_revision": build_info["short_commit_sha"],
        "service_version": build_info["version"],
    }
    code = _error_code(error)
    if code is not None:
        payload["error_code"] = code
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)
