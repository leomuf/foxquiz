# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

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
