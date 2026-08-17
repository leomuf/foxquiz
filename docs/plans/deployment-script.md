# Planned FoxQuiz Deployment Script

## Status

This document describes a future deployment script. The script has not been
implemented yet, and the commands below must not be executed merely by reading
or updating this document.

## Trigger and Confirmation

The future script supports the established FoxQuiz deployment workflow used
when the user asks:

- "Please deploy the application"
- "Deploy the application"
- "Deploy to Google Cloud"

Before making any Google Cloud change, the operator or coding agent must:

1. Explain the complete deployment and post-configuration sequence.
2. Show the version, branch, full commit SHA, project, service, and region.
3. Ask for explicit human confirmation.
4. Stop without executing anything unless confirmation is given.

## Problem

The deployed container does not include the repository's `.git` directory, so
FoxQuiz cannot discover its Git commit at runtime. When deployment does not
provide `COMMIT_SHA`, the frontend and `/version` endpoint display `dev`.
The build timestamp is also unavailable when `BUILD_TIME` is omitted.

Running plain `agents-cli deploy --no-confirm-project` therefore does not
guarantee complete build identity.

## Goal

Create a versioned script such as `scripts/deploy.sh` and use it for every
manual deployment. It must preserve the established deployment sequence while
injecting the project version, exact Git commit, and UTC build timestamp
automatically.

## Fixed FoxQuiz Deployment Targets

The first script is project-specific and should use:

```text
Google Cloud project: GCLOUD_PROJECT_ID
Cloud Run service: foxquiz
Cloud Run region: us-east1
```

## Required Safeguards

The future script should:

- Run from the FoxQuiz repository root on Linux or WSL.
- Use `set -euo pipefail` so the sequence stops after any failed command.
- Refuse deployment when `git status --porcelain` is not empty.
- Read the full commit SHA with `git rev-parse HEAD`.
- Read the branch with `git branch --show-current`.
- Read the application version with `uv version --short`.
- Generate the build time in UTC.
- Require explicit human confirmation before changing Google Cloud.
- Run every deployment command in the foreground and preserve its live output.
- Report an error immediately and never continue to the next phase after a
  failure.
- Never write Google credentials, project secrets, or generated environment
  values into versioned files.
- Never deploy as a side effect of tests, builds, commits, tags, or releases.

## Planned Command Sequence

### Phase 1: Preflight and Confirmation

The script should calculate and display the exact deployment identity:

```bash
set -euo pipefail

PROJECT_ID="GCLOUD_PROJECT_ID"
SERVICE_NAME="foxquiz"
REGION="us-east1"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Deployment refused: the worktree is not clean."
  exit 1
fi

COMMIT_SHA="$(git rev-parse HEAD)"
BRANCH_NAME="$(git branch --show-current)"
AGENT_VERSION="$(uv version --short)"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "FoxQuiz deployment plan"
echo "Version: ${AGENT_VERSION}"
echo "Branch: ${BRANCH_NAME}"
echo "Commit: ${COMMIT_SHA}"
echo "Project: ${PROJECT_ID}"
echo "Service: ${SERVICE_NAME}"
echo "Region: ${REGION}"

read -r -p "Continue with deployment and post-configuration? [y/N] " CONFIRM
if [[ "${CONFIRM}" != "y" && "${CONFIRM}" != "Y" ]]; then
  echo "Deployment cancelled."
  exit 0
fi
```

### Phase 2: Deploy FoxQuiz

The script must announce this phase and run:

```bash
echo "Deploying FoxQuiz to Google Cloud"

agents-cli deploy \
  --no-confirm-project \
  --update-env-vars \
  "COMMIT_SHA=${COMMIT_SHA},AGENT_VERSION=${AGENT_VERSION},BUILD_TIME=${BUILD_TIME}"
```

The command must run in the foreground. Its output should remain visible while
the deployment is in progress. The script may continue only after
`agents-cli deploy` exits successfully. Any error must stop the sequence.

### Phase 3: Apply Cloud Run Cost and Startup Settings

After successful deployment, the script must announce this phase and run:

```bash
echo "Applying Cloud Run cost and startup settings"

gcloud run services update "${SERVICE_NAME}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --min-instances 0 \
  --cpu-boost \
  --execution-environment gen1
```

The script may continue only after Cloud Run reports a successful service
update. Any error must stop the sequence.

### Phase 4: Ensure Public Invocation

After the successful service update, the script must announce this phase and
run:

```bash
echo "Ensuring public invocation is enabled"

gcloud run services add-iam-policy-binding "${SERVICE_NAME}" \
  --member="allUsers" \
  --role="roles/run.invoker" \
  --project "${PROJECT_ID}" \
  --region "${REGION}"
```

The script may continue only after the IAM policy update succeeds. Any error
must stop the sequence.

### Phase 5: Verify the Deployment

After deployment and post-configuration succeed, the script should query:

```bash
echo "Verifying deployed build identity"

curl --fail --silent --show-error https://foxquiz.app/version
```

A successful response must contain:

- The version from `pyproject.toml`.
- The full deployed commit SHA.
- The seven-character short commit SHA.
- A GitHub URL pointing to that commit.
- A non-null UTC build timestamp.

The script should compare the returned version and commit with
`AGENT_VERSION` and `COMMIT_SHA`, not merely check for HTTP success. It
should report completion only when deployment, both post-configuration commands,
and build-identity verification all succeed.

The FoxQuiz footer should display the semantic version and linked short commit,
for example `FoxQuiz v1.1.0` followed by `edeb34b`.

## Release Boundary

The Git tag is not detected automatically. Deployment completion must not
create a tag or GitHub release.

The release tag should be created only after:

1. Manual FoxQuiz testing is complete.
2. Cloud Run logs have been reviewed.
3. No release-blocking errors remain.
4. The tag target matches the commit returned by `/version`.

## Possible Future Automation

A later GitHub Actions deployment could obtain the exact commit from
`GITHUB_SHA` and authenticate to Google Cloud through Workload Identity
Federation. This would avoid storing a long-lived Google service-account key in
GitHub, but it is outside the scope of the first deployment script.
