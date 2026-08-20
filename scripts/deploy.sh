#!/usr/bin/env bash

set -euo pipefail

REGION="us-east1"
PROD_SERVICE_NAME="foxquiz"
PROD_ACCOUNT_ID="foxquiz-prod-runtime"
DEV_ACCOUNT_ID="foxquiz-dev-runtime"
PROD_DATABASE_ID="(default)"
DEV_DATABASE_ID="foxquiz-dev"
CPU="1"
MEMORY="4Gi"
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
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Required command not found: ${command_name}" >&2
    exit 1
  fi
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

if [[ "${ENVIRONMENT}" == "prod" ]]; then
  SERVICE_NAME="${PROD_SERVICE_NAME}"
  RUNTIME_SERVICE_ACCOUNT="${PROD_ACCOUNT_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
  DATABASE_ID="${PROD_DATABASE_ID}"
  AGENT_VERSION="${BASE_VERSION}"
  MAX_INSTANCES="${PROD_MAX_INSTANCES}"
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
  --cpu-boost
  --execution-environment gen1
  --quiet
)

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
echo "Scaling: ${MIN_INSTANCES}-${MAX_INSTANCES} instances, concurrency ${CONCURRENCY}"
if [[ -n "${WORKTREE_CHANGES}" ]]; then
  echo "Warning: the worktree is dirty; preview is allowed but deployment is not."
fi
echo
echo "Deploy application and inject build metadata:"
print_command "${DEPLOY_COMMAND[@]}"
echo "Apply Cloud Run scaling, startup CPU boost, and Gen1:"
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
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Required command not found: ${command_name}" >&2
    exit 1
  fi
done

ACTIVE_ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' | head -n 1)"
if [[ -z "${ACTIVE_ACCOUNT}" ]]; then
  echo "No active gcloud account. Authenticate before deploying." >&2
  exit 1
fi

gcloud iam service-accounts describe "${RUNTIME_SERVICE_ACCOUNT}" \
  --project "${PROJECT_ID}" >/dev/null

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

expected_account, expected_min, expected_max, expected_commit, expected_version, expected_database = sys.argv[1:]
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
assert str(annotations.get("run.googleapis.com/startup-cpu-boost")).lower() == "true"
assert annotations.get("run.googleapis.com/execution-environment") == "gen1"
assert environment.get("COMMIT_SHA") == expected_commit
assert environment.get("AGENT_VERSION") == expected_version
assert environment.get("FIRESTORE_DATABASE_ID") == expected_database
assert environment.get("ENABLE_A2A") == "FALSE"
assert environment.get("ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS") == "FALSE"
assert environment.get("BUILD_TIME")
assert service.get("status", {}).get("url")
' "${RUNTIME_SERVICE_ACCOUNT}" "${MIN_INSTANCES}" "${MAX_INSTANCES}" \
  "${COMMIT_SHA}" "${AGENT_VERSION}" "${DATABASE_ID}"

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
if [[ "${ENVIRONMENT}" == "dev" ]]; then
  echo "Keep the service name and URL locally for this DEV campaign."
fi
