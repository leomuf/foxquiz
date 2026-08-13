# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Privacy-contract tests for structured operational failure events.

Purpose:
    Ensure Firestore failures emit queryable JSON containing only approved
    fields: phase, operation, error class/code, version, and deployed revision.

Privacy boundary:
    Exception messages, prompts, IP addresses, client identifiers, hashed
    signatures, and private security rules must never appear. Cloud Logging
    ingestion and metrics are infrastructure concerns outside this unit test.
"""

import json
from unittest.mock import patch

from app.app_utils.operational_logging import emit_operational_event


def test_operational_event_is_structured_and_privacy_safe(capsys) -> None:
    """Operational failures expose diagnostics without user or rule content."""
    secret = "prompt=Meu CPF is 123; ip=203.0.113.7; rule=private-pattern"
    error = RuntimeError(secret)
    error.code = secret

    with patch(
        "app.app_utils.operational_logging.get_build_info",
        return_value={
            "version": "1.1.0",
            "short_commit_sha": "abc1234",
        },
    ):
        emit_operational_event(
            event="firestore_operation_failed",
            phase="ban_check",
            operation="check_banned_signature",
            error=error,
        )

    output = capsys.readouterr()
    payload = json.loads(output.err)
    assert payload == {
        "deployment_revision": "abc1234",
        "error_type": "RuntimeError",
        "event": "firestore_operation_failed",
        "operation": "check_banned_signature",
        "phase": "ban_check",
        "service_version": "1.1.0",
        "severity": "ERROR",
    }
    assert secret not in output.err
    assert output.out == ""
