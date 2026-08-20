"""Tests for the guarded DEV and production Cloud Run deployment script."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "deploy.sh"
TEST_PROJECT = "sample-project-123"
DEV_SERVICE = "svc-0123456789abcdefabcd"


def run_dry_run(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Render a deployment plan without permitting cloud commands."""
    return subprocess.run(
        ["bash", str(SCRIPT), *arguments],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def write_executable(path: Path, content: str) -> None:
    """Create one executable fake command used by the apply-path test."""
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_dev_dry_run_renders_isolated_bounded_deployment() -> None:
    """Select the DEV identity, database, random service, and two-instance cap."""
    result = run_dry_run("--environment", "dev", "--project", TEST_PROJECT)

    assert result.returncode == 0
    assert "Environment: DEV" in result.stdout
    assert re.search(r"Service: svc-[0-9a-f]{20}", result.stdout)
    assert (
        f"foxquiz-dev-runtime@{TEST_PROJECT}.iam.gserviceaccount.com" in result.stdout
    )
    assert "Firestore database: foxquiz-dev" in result.stdout
    assert "--max-instances 2" in result.stdout
    assert "AGENT_VERSION=1.2.0-dev" in result.stdout
    assert "Dry run only" in result.stdout


def test_prod_dry_run_renders_fixed_production_deployment() -> None:
    """Select the fixed production service, identity, database, and scaling."""
    result = run_dry_run("--environment", "prod", "--project", TEST_PROJECT)

    assert result.returncode == 0
    assert "Environment: PROD" in result.stdout
    assert "Service: foxquiz" in result.stdout
    assert (
        f"foxquiz-prod-runtime@{TEST_PROJECT}.iam.gserviceaccount.com" in result.stdout
    )
    assert "Firestore database: (default)" in result.stdout
    assert "Version: 1.2.0" in result.stdout
    assert "--max-instances 10" in result.stdout
    assert "Dry run only" in result.stdout


def test_arguments_reject_unsafe_targets() -> None:
    """Reject malformed projects, environments, and DEV-name overrides."""
    invalid_environment = run_dry_run(
        "--environment", "staging", "--project", TEST_PROJECT
    )
    invalid_project = run_dry_run("--environment", "dev", "--project", "NOT_A_PROJECT")
    production_override = run_dry_run(
        "--environment",
        "prod",
        "--project",
        TEST_PROJECT,
        "--service-name",
        DEV_SERVICE,
    )
    predictable_dev_name = run_dry_run(
        "--environment",
        "dev",
        "--project",
        TEST_PROJECT,
        "--service-name",
        "foxquiz-dev",
    )

    assert invalid_environment.returncode == 2
    assert invalid_project.returncode == 2
    assert production_override.returncode == 2
    assert predictable_dev_name.returncode == 2


def test_missing_command_reports_actionable_installation_guidance(
    tmp_path: Path,
) -> None:
    """Stop before planning and explain how to install a missing prerequisite."""
    environment = os.environ.copy()
    environment["PATH"] = str(tmp_path)

    result = subprocess.run(
        [
            "/bin/bash",
            str(SCRIPT),
            "--environment",
            "dev",
            "--project",
            TEST_PROJECT,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Missing required command: git" in result.stderr
    assert "Install Git before continuing" in result.stderr
    assert "https://git-scm.com/downloads" in result.stderr
    assert "FoxQuiz Cloud Run deployment plan" not in result.stdout


def test_apply_executes_and_verifies_the_complete_dev_sequence(tmp_path: Path) -> None:
    """Exercise deployment, settings, IAM, and verification with fake CLIs."""
    repository = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    repository.mkdir()
    fake_bin.mkdir()
    (repository / "scripts").mkdir()
    shutil.copy2(SCRIPT, repository / "scripts" / "deploy.sh")
    (repository / "pyproject.toml").write_text(
        '[project]\nname = "foxquiz"\nversion = "1.2.0"\n', encoding="utf-8"
    )

    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "FoxQuiz Test"], cwd=repository, check=True
    )
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repository, check=True)
    commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    command_log = tmp_path / "commands.log"
    service_json = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "autoscaling.knative.dev/maxScale": "2",
                        "run.googleapis.com/startup-cpu-boost": "true",
                        "run.googleapis.com/execution-environment": "gen1",
                    }
                },
                "spec": {
                    "serviceAccountName": (
                        f"foxquiz-dev-runtime@{TEST_PROJECT}.iam.gserviceaccount.com"
                    ),
                    "containers": [
                        {
                            "env": [
                                {"name": "COMMIT_SHA", "value": commit_sha},
                                {"name": "AGENT_VERSION", "value": "1.2.0-dev"},
                                {"name": "BUILD_TIME", "value": "2026-08-20T12:00:00Z"},
                                {
                                    "name": "FIRESTORE_DATABASE_ID",
                                    "value": "foxquiz-dev",
                                },
                                {"name": "ENABLE_A2A", "value": "FALSE"},
                                {
                                    "name": "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS",
                                    "value": "FALSE",
                                },
                            ]
                        }
                    ],
                },
            }
        },
        "status": {"url": "https://example.invalid"},
    }
    policy_json = {"bindings": [{"role": "roles/run.invoker", "members": ["allUsers"]}]}

    write_executable(
        fake_bin / "agents-cli",
        '#!/usr/bin/env bash\nprintf \'agents-cli %s\\n\' "$*" >>"${COMMAND_LOG}"\n',
    )
    write_executable(
        fake_bin / "gcloud",
        f"""#!/usr/bin/env bash
printf 'gcloud %s\\n' "$*" >>"${{COMMAND_LOG}}"
case "$*" in
  "auth list"*) echo "maintainer@example.invalid" ;;
  *"services describe"*"--format=json"*) printf '%s\\n' '{json.dumps(service_json)}' ;;
  *"get-iam-policy"*"--format=json"*) printf '%s\\n' '{json.dumps(policy_json)}' ;;
  *"services list"*) : ;;
esac
""",
    )
    write_executable(
        fake_bin / "curl",
        f"""#!/usr/bin/env bash
printf 'curl %s\\n' "$*" >>"${{COMMAND_LOG}}"
case "$*" in
  *"/version") printf '%s\\n' '{json.dumps({"version": "1.2.0-dev", "commit_sha": commit_sha, "short_commit_sha": commit_sha[:7], "commit_url": f"https://github.com/leomuf/foxquiz/commit/{commit_sha}", "build_time": "2026-08-20T12:00:00Z"})}' ;;
esac
""",
    )

    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["COMMAND_LOG"] = str(command_log)
    result = subprocess.run(
        [
            "bash",
            str(repository / "scripts" / "deploy.sh"),
            "--environment",
            "dev",
            "--project",
            TEST_PROJECT,
            "--service-name",
            DEV_SERVICE,
            "--apply",
        ],
        cwd=repository,
        input="DEPLOY DEV\n",
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Deployment verified." in result.stdout
    log = command_log.read_text(encoding="utf-8")
    assert "agents-cli deploy" in log
    assert f"--service-name {DEV_SERVICE}" in log
    assert "--service-account foxquiz-dev-runtime@" in log
    assert "gcloud run services update" in log
    assert "--cpu-boost --execution-environment gen1" in log
    assert "gcloud run services add-iam-policy-binding" in log
    assert "gcloud run services describe" in log
    assert "gcloud run services get-iam-policy" in log
    assert "curl --fail --silent --show-error --output /dev/null" in log
    assert "curl --fail --silent --show-error https://example.invalid/version" in log
