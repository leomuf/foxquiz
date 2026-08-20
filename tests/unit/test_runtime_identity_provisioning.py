"""Tests for the guarded Cloud Run runtime-identity provisioning script."""

from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "provision-runtime-identities.sh"
TEST_PROJECT = "sample-project-123"


def run_dry_run(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Execute the script without ``--apply`` and capture its rendered plan."""
    return subprocess.run(
        ["bash", str(SCRIPT), *arguments],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_dry_run_separates_production_and_dev_identities() -> None:
    """Render distinct production and DEV accounts and Firestore conditions."""
    result = run_dry_run("--project", TEST_PROJECT)

    assert result.returncode == 0
    assert (
        f"foxquiz-prod-runtime@{TEST_PROJECT}.iam.gserviceaccount.com" in result.stdout
    )
    assert (
        f"foxquiz-dev-runtime@{TEST_PROJECT}.iam.gserviceaccount.com" in result.stdout
    )
    assert rf"projects/{TEST_PROJECT}/databases/\(default\)" in result.stdout
    assert f"projects/{TEST_PROJECT}/databases/foxquiz-dev" in result.stdout
    assert "Dry run only" in result.stdout


def test_dry_run_grants_only_justified_runtime_roles() -> None:
    """Include application roles while explicitly excluding build and GCS roles."""
    result = run_dry_run("--project", TEST_PROJECT)

    assert result.returncode == 0
    for role in (
        "roles/aiplatform.user",
        "roles/datastore.user",
        "roles/logging.logWriter",
        "roles/monitoring.metricWriter",
        "roles/serviceusage.serviceUsageConsumer",
        "roles/telemetry.tracesWriter",
    ):
        assert role in result.stdout

    assert result.stdout.count("--condition None") == 10

    grant_section, excluded_section = result.stdout.split(
        "Excluded from both runtime identities:", maxsplit=1
    )
    assert "roles/artifactregistry.writer" not in grant_section
    assert "roles/storage.objectViewer" not in grant_section
    assert "roles/artifactregistry.writer" in excluded_section
    assert "roles/storage.objectViewer" in excluded_section


def test_project_is_required_and_validated() -> None:
    """Reject missing or malformed project identifiers before any cloud command."""
    missing = run_dry_run()
    malformed = run_dry_run("--project", "NOT_A_PROJECT")

    assert missing.returncode == 2
    assert "--project is required" in missing.stderr
    assert malformed.returncode == 2
    assert "Invalid Google Cloud project ID" in malformed.stderr
