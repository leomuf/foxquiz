#!/usr/bin/env bash

set -euo pipefail

REGION="us-east1"
PROD_SERVICE_NAME="foxquiz"
PROD_ACCOUNT_ID="foxquiz-prod-runtime"
DEV_ACCOUNT_ID="foxquiz-dev-runtime"
PROD_DATABASE_ID="(default)"
DEV_DATABASE_ID="foxquiz-dev"
CPU="1"
MEMORY="1Gi"
CONCURRENCY="8"
MIN_INSTANCES="0"
PROD_MAX_INSTANCES="10"
DEV_MAX_INSTANCES="2"

ENVIRONMENT=""
PROJECT_ID=""
REQUESTED_SERVICE_NAME=""
APPLY_CHANGES="false"

usage() {
  cat <<'EOF'
Usage: scripts/deploy.sh --environment dev|prod --project PROJECT_ID [options]

Deploy FoxQuiz to Cloud Run with its required metadata, runtime identity,
resource settings, public access, and post-deployment verification.

Options:
  --environment dev|prod  Required deployment target.
  --project PROJECT_ID    Required Google Cloud project ID.
  --service-name NAME     Reuse an existing random DEV campaign service name.
                          Omit to generate a new DEV name. Invalid for prod.
  --apply                 Execute after a typed confirmation. Without this
                          flag, print the complete plan without cloud calls.
  -h, --help              Show this help.
EOF
}

require_command() {
  local command_name="$1"

  if command -v "${command_name}" >/dev/null 2>&1; then
    return
  fi

  echo "Missing required command: ${command_name}" >&2
  case "${command_name}" in
    git)
      echo "Install Git before continuing: https://git-scm.com/downloads" >&2
      ;;
    uv)
      echo "Install uv before continuing: https://docs.astral.sh/uv/getting-started/installation/" >&2
      ;;
    openssl)
      echo "Install OpenSSL before continuing (for example: sudo apt install openssl)." >&2
      ;;
    agents-cli)
      echo "Install the Google Agents CLI before continuing:" >&2
      echo "  uv tool install google-agents-cli" >&2
      ;;
    gcloud)
      echo "Install the Google Cloud CLI before continuing: https://cloud.google.com/sdk/docs/install" >&2
      ;;
    curl)
      echo "Install curl before continuing (for example: sudo apt install curl)." >&2
      ;;
  esac
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --environment)
      [[ $# -ge 2 ]] || { echo "Missing value for --environment." >&2; exit 2; }
      ENVIRONMENT="${2,,}"
      shift 2
      ;;
    --project)
      [[ $# -ge 2 ]] || { echo "Missing value for --project." >&2; exit 2; }
      PROJECT_ID="$2"
      shift 2
      ;;
    --service-name)
      [[ $# -ge 2 ]] || { echo "Missing value for --service-name." >&2; exit 2; }
      REQUESTED_SERVICE_NAME="$2"
      shift 2
      ;;
    --apply)
      APPLY_CHANGES="true"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "${ENVIRONMENT}" != "dev" && "${ENVIRONMENT}" != "prod" ]]; then
  echo "--environment must be dev or prod." >&2
  exit 2
fi

if [[ -z "${PROJECT_ID}" ]]; then
  echo "--project is required." >&2
  exit 2
fi

if [[ ! "${PROJECT_ID}" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
  echo "Invalid Google Cloud project ID: ${PROJECT_ID}" >&2
  exit 2
fi

if [[ "${ENVIRONMENT}" == "prod" && -n "${REQUESTED_SERVICE_NAME}" ]]; then
  echo "--service-name is valid only for DEV deployments." >&2
  exit 2
fi

if [[ -n "${REQUESTED_SERVICE_NAME}" && ! "${REQUESTED_SERVICE_NAME}" =~ ^svc-[0-9a-f]{20}$ ]]; then
  echo "DEV service names must match svc- followed by 20 lowercase hex characters." >&2
  exit 2
fi

for command_name in git uv openssl; do
  require_command "${command_name}"
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPOSITORY_ROOT}"

if [[ "$(git rev-parse --show-toplevel)" != "${REPOSITORY_ROOT}" ]]; then
  echo "Deployment must run from the FoxQuiz Git repository." >&2
  exit 1
fi

WORKTREE_CHANGES="$(git status --porcelain)"
if [[ -n "${WORKTREE_CHANGES}" && "${APPLY_CHANGES}" == "true" ]]; then
  echo "Deployment refused: the worktree is not clean." >&2
  exit 1
fi

COMMIT_SHA="$(git rev-parse HEAD)"
BRANCH_NAME="$(git branch --show-current)"
BASE_VERSION="$(uv version --short)"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
GENERATED_DEV_SERVICE="false"
EXPECTED_REVISION_NAME=""
PROD_REVISION_SUFFIX=""

if [[ "${ENVIRONMENT}" == "prod" ]]; then
  SERVICE_NAME="${PROD_SERVICE_NAME}"
  RUNTIME_SERVICE_ACCOUNT="${PROD_ACCOUNT_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
  DATABASE_ID="${PROD_DATABASE_ID}"
  AGENT_VERSION="${BASE_VERSION}"
  MAX_INSTANCES="${PROD_MAX_INSTANCES}"

  RELEASE_REVISION_LABEL="v${BASE_VERSION,,}"
  RELEASE_REVISION_LABEL="${RELEASE_REVISION_LABEL//./p}"
  RELEASE_REVISION_LABEL="${RELEASE_REVISION_LABEL//+/-}"
  RELEASE_REVISION_LABEL="${RELEASE_REVISION_LABEL//_/-}"
  if [[ ! "${RELEASE_REVISION_LABEL}" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]; then
    echo "Cannot convert release version '${BASE_VERSION}' into a Cloud Run revision label." >&2
    exit 1
  fi

  REVISION_BUILD_ID="${BUILD_TIME//-/}"
  REVISION_BUILD_ID="${REVISION_BUILD_ID//:/}"
  REVISION_BUILD_ID="${REVISION_BUILD_ID,,}"
  PROD_REVISION_SUFFIX="${REVISION_BUILD_ID}-${RELEASE_REVISION_LABEL}"
  EXPECTED_REVISION_NAME="${SERVICE_NAME}-${PROD_REVISION_SUFFIX}"
  if (( ${#EXPECTED_REVISION_NAME} > 63 )); then
    echo "Production revision name exceeds Cloud Run's 63-character limit:" >&2
    echo "  ${EXPECTED_REVISION_NAME}" >&2
    exit 1
  fi
else
  if [[ -n "${REQUESTED_SERVICE_NAME}" ]]; then
    SERVICE_NAME="${REQUESTED_SERVICE_NAME}"
  else
    SERVICE_NAME="svc-$(openssl rand -hex 10)"
    GENERATED_DEV_SERVICE="true"
  fi
  RUNTIME_SERVICE_ACCOUNT="${DEV_ACCOUNT_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
  DATABASE_ID="${DEV_DATABASE_ID}"
  AGENT_VERSION="${BASE_VERSION}-dev"
  MAX_INSTANCES="${DEV_MAX_INSTANCES}"
fi

UPDATE_ENV_VARS="COMMIT_SHA=${COMMIT_SHA},AGENT_VERSION=${AGENT_VERSION},BUILD_TIME=${BUILD_TIME},FIRESTORE_DATABASE_ID=${DATABASE_ID},ENABLE_A2A=FALSE,ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=FALSE"

DEPLOY_COMMAND=(
  agents-cli deploy
  --project "${PROJECT_ID}"
  --region "${REGION}"
  --service-name "${SERVICE_NAME}"
  --service-account "${RUNTIME_SERVICE_ACCOUNT}"
  --cpu "${CPU}"
  --memory "${MEMORY}"
  --concurrency "${CONCURRENCY}"
  --min-instances "${MIN_INSTANCES}"
  --max-instances "${MAX_INSTANCES}"
  --no-confirm-project
  --update-env-vars "${UPDATE_ENV_VARS}"
)

SETTINGS_COMMAND=(
  gcloud run services update "${SERVICE_NAME}"
  --project "${PROJECT_ID}"
  --region "${REGION}"
  --min-instances "${MIN_INSTANCES}"
  --max-instances "${MAX_INSTANCES}"
  --cpu-throttling
  --cpu-boost
  --execution-environment gen1
  --quiet
)

if [[ "${ENVIRONMENT}" == "prod" ]]; then
  SETTINGS_COMMAND+=(--revision-suffix "${PROD_REVISION_SUFFIX}")
fi

PUBLIC_COMMAND=(
  gcloud run services add-iam-policy-binding "${SERVICE_NAME}"
  --project "${PROJECT_ID}"
  --region "${REGION}"
  --member allUsers
  --role roles/run.invoker
  --quiet
)

print_command() {
  printf '  '
  printf '%q ' "$@"
  printf '\n'
}

echo "FoxQuiz Cloud Run deployment plan"
echo "Environment: ${ENVIRONMENT^^}"
echo "Project: ${PROJECT_ID}"
echo "Region: ${REGION}"
echo "Service: ${SERVICE_NAME}"
echo "Runtime identity: ${RUNTIME_SERVICE_ACCOUNT}"
echo "Firestore database: ${DATABASE_ID}"
echo "Version: ${AGENT_VERSION}"
echo "Branch: ${BRANCH_NAME:-detached HEAD}"
echo "Commit: ${COMMIT_SHA}"
echo "Build time: ${BUILD_TIME}"
if [[ "${ENVIRONMENT}" == "prod" ]]; then
  echo "Final revision: ${EXPECTED_REVISION_NAME}"
fi
echo "Scaling: ${MIN_INSTANCES}-${MAX_INSTANCES} instances, concurrency ${CONCURRENCY}"
if [[ -n "${WORKTREE_CHANGES}" ]]; then
  echo "Warning: the worktree is dirty; preview is allowed but deployment is not."
fi
echo
echo "Deploy application and inject build metadata:"
print_command "${DEPLOY_COMMAND[@]}"
echo "Apply Cloud Run scaling, request-based billing, startup CPU boost, and Gen1:"
print_command "${SETTINGS_COMMAND[@]}"
echo "Grant public invocation:"
print_command "${PUBLIC_COMMAND[@]}"
echo "Verify runtime identity, settings, metadata, public IAM, root page, and /version."

if [[ "${APPLY_CHANGES}" != "true" ]]; then
  echo
  echo "Dry run only. Re-run with --apply to deploy."
  exit 0
fi

for command_name in agents-cli gcloud curl; do
  require_command "${command_name}"
done

if ! ACTIVE_ACCOUNT="$(
  gcloud auth list --filter=status:ACTIVE --limit=1 --format='value(account)' 2>/dev/null
)"; then
  echo "Unable to inspect Google Cloud authentication." >&2
  echo "Run 'gcloud auth login', then verify access with 'gcloud auth list'." >&2
  exit 1
fi
if [[ -z "${ACTIVE_ACCOUNT}" ]]; then
  echo "No active Google Cloud account." >&2
  echo "Authenticate with 'gcloud auth login', then rerun the deployment." >&2
  exit 1
fi

if ! gcloud iam service-accounts describe "${RUNTIME_SERVICE_ACCOUNT}" \
  --project "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "Runtime service account not found or inaccessible:" >&2
  echo "  ${RUNTIME_SERVICE_ACCOUNT}" >&2
  echo "Provision the dedicated identities first:" >&2
  echo "  scripts/provision-runtime-identities.sh --project ${PROJECT_ID} --apply" >&2
  echo "If it already exists, verify that the active account can view and use it." >&2
  exit 1
fi

if [[ "${GENERATED_DEV_SERVICE}" == "true" ]]; then
  EXISTING_SERVICE="$(gcloud run services list \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --filter "metadata.name=${SERVICE_NAME}" \
    --format='value(metadata.name)')"
  if [[ -n "${EXISTING_SERVICE}" ]]; then
    echo "Generated DEV service name already exists; rerun to generate another." >&2
    exit 1
  fi
fi

echo
read -r -p "Type DEPLOY ${ENVIRONMENT^^} to continue: " CONFIRMATION
if [[ "${CONFIRMATION}" != "DEPLOY ${ENVIRONMENT^^}" ]]; then
  echo "Deployment cancelled."
  exit 1
fi

echo
echo "Deploying FoxQuiz"
"${DEPLOY_COMMAND[@]}"

echo
echo "Applying Cloud Run settings"
"${SETTINGS_COMMAND[@]}"

echo
echo "Enabling public invocation"
"${PUBLIC_COMMAND[@]}"

echo
echo "Verifying Cloud Run configuration"
SERVICE_JSON="$(gcloud run services describe "${SERVICE_NAME}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --format=json)"

printf '%s' "${SERVICE_JSON}" | uv run python -c '
import json
import sys

(
    expected_account,
    expected_min,
    expected_max,
    expected_commit,
    expected_version,
    expected_database,
    expected_revision,
) = sys.argv[1:]
service = json.load(sys.stdin)
template = service["spec"]["template"]
annotations = template.get("metadata", {}).get("annotations", {})
spec = template["spec"]
container = spec["containers"][0]
environment = {item["name"]: item.get("value", "") for item in container.get("env", [])}

assert spec["serviceAccountName"] == expected_account
actual_min = annotations.get("autoscaling.knative.dev/minScale", "0")
assert str(actual_min) == expected_min
assert str(annotations.get("autoscaling.knative.dev/maxScale")) == expected_max
assert str(annotations.get("run.googleapis.com/cpu-throttling")).lower() == "true"
assert str(annotations.get("run.googleapis.com/startup-cpu-boost")).lower() == "true"
assert annotations.get("run.googleapis.com/execution-environment") == "gen1"
assert environment.get("COMMIT_SHA") == expected_commit
assert environment.get("AGENT_VERSION") == expected_version
assert environment.get("FIRESTORE_DATABASE_ID") == expected_database
assert environment.get("ENABLE_A2A") == "FALSE"
assert environment.get("ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS") == "FALSE"
assert environment.get("BUILD_TIME")
assert service.get("status", {}).get("url")
if expected_revision:
    assert service.get("status", {}).get("latestReadyRevisionName") == expected_revision
' "${RUNTIME_SERVICE_ACCOUNT}" "${MIN_INSTANCES}" "${MAX_INSTANCES}" \
  "${COMMIT_SHA}" "${AGENT_VERSION}" "${DATABASE_ID}" "${EXPECTED_REVISION_NAME}"

POLICY_JSON="$(gcloud run services get-iam-policy "${SERVICE_NAME}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --format=json)"

printf '%s' "${POLICY_JSON}" | uv run python -c '
import json
import sys

policy = json.load(sys.stdin)
assert any(
    binding.get("role") == "roles/run.invoker"
    and "allUsers" in binding.get("members", [])
    for binding in policy.get("bindings", [])
)
'

SERVICE_URL="$(printf '%s' "${SERVICE_JSON}" | uv run python -c \
  'import json, sys; print(json.load(sys.stdin)["status"]["url"])')"

curl --fail --silent --show-error --output /dev/null "${SERVICE_URL}/"
VERSION_JSON="$(curl --fail --silent --show-error "${SERVICE_URL}/version")"

printf '%s' "${VERSION_JSON}" | uv run python -c '
import json
import sys

expected_version, expected_commit = sys.argv[1:]
metadata = json.load(sys.stdin)
assert metadata.get("version") == expected_version
assert metadata.get("commit_sha") == expected_commit
assert metadata.get("short_commit_sha") == expected_commit[:7]
assert metadata.get("commit_url", "").endswith(expected_commit)
assert metadata.get("build_time")
' "${AGENT_VERSION}" "${COMMIT_SHA}"

echo
echo "Deployment verified."
echo "Environment: ${ENVIRONMENT^^}"
echo "Service: ${SERVICE_NAME}"
echo "URL: ${SERVICE_URL}"
echo "Commit: ${COMMIT_SHA}"
if [[ "${ENVIRONMENT}" == "prod" ]]; then
  echo "Revision: ${EXPECTED_REVISION_NAME}"
fi
if [[ "${ENVIRONMENT}" == "dev" ]]; then
  echo "Keep the service name and URL locally for this DEV campaign."
fi
