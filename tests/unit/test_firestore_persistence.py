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

import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.database.firestore_repo import (
    FirestorePersistenceError,
    FirestoreRepository,
)


@pytest.fixture
def real_repo():
    """Build a repository on the real-client path with a mocked Firestore client."""
    with patch("app.database.firestore_repo.firestore.Client") as client_class:
        yield FirestoreRepository(), client_class.return_value


def test_increment_token_budget_starts_transaction(real_repo):
    """The budget update must run through Firestore's transactional wrapper."""
    repo, client = real_repo
    document = client.collection.return_value.document.return_value
    transaction = client.transaction.return_value
    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {
        "tokens_used": 100,
        "last_reset_date": datetime.date.today().isoformat(),
    }
    document.get.return_value = snapshot

    with patch(
        "app.database.firestore_repo.firestore.transactional",
        side_effect=lambda function: function,
    ) as transactional:
        assert repo.increment_token_budget("budget_user", 25) is True

    transactional.assert_called_once()
    document.get.assert_called_once_with(transaction=transaction)
    transaction.update.assert_called_once_with(document, {"tokens_used": 125})


def test_legacy_transient_budget_gets_expiration_on_read(real_repo):
    """A legacy transient budget must receive the TTL field when next accessed."""
    repo, client = real_repo
    document = client.collection.return_value.document.return_value
    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {
        "tokens_used": 100,
        "last_reset_date": datetime.date.today().isoformat(),
    }
    document.get.return_value = snapshot

    budget = repo.get_token_budget("budget_transient_legacy")

    assert isinstance(budget["expires_at"], datetime.datetime)
    document.set.assert_called_once_with(
        {"expires_at": budget["expires_at"]}, merge=True
    )


def test_shared_quiz_failure_is_not_reported_as_saved(real_repo):
    """A failed production write must raise instead of succeeding in memory."""
    repo, client = real_repo
    client.collection.return_value.document.return_value.set.side_effect = RuntimeError(
        "Firestore unavailable"
    )

    with pytest.raises(FirestorePersistenceError, match="save shared quiz"):
        repo.save_shared_quiz("quiz-id", {"title": "Quiz"})

    assert repo.use_mock is False


def test_feedback_failure_is_not_reported_as_saved(real_repo):
    """Feedback write errors must be visible to the API caller."""
    repo, client = real_repo
    client.collection.return_value.document.return_value.set.side_effect = RuntimeError(
        "Firestore unavailable"
    )

    with pytest.raises(FirestorePersistenceError, match="thumbs-up metric"):
        repo.save_feedback_log(
            score=1,
            text="Great quiz",
            session_id="session-id",
            anonymous_id="anonymous-id",
        )

    assert repo.use_mock is False
