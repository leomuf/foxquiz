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
