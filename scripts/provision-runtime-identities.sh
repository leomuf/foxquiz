#!/usr/bin/env bash

set -euo pipefail

PROD_ACCOUNT_ID="foxquiz-prod-runtime"
DEV_ACCOUNT_ID="foxquiz-dev-runtime"
PROD_DATABASE_ID="(default)"
DEV_DATABASE_ID="foxquiz-dev"

PROJECT_ID=""
APPLY_CHANGES="false"

usage() {
  cat <<'EOF'
Usage: scripts/provision-runtime-identities.sh --project PROJECT_ID [--apply]

Prepare dedicated least-privilege Cloud Run runtime identities for FoxQuiz.
The default mode prints the intended changes without calling Google Cloud.
Pass --apply to execute them after an interactive confirmation.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --project." >&2
        usage >&2
        exit 2
      fi
      PROJECT_ID="$2"
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

if [[ -z "${PROJECT_ID}" ]]; then
  echo "--project is required." >&2
  usage >&2
  exit 2
fi

if [[ ! "${PROJECT_ID}" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
  echo "Invalid Google Cloud project ID: ${PROJECT_ID}" >&2
  exit 2
fi

PROD_ACCOUNT_EMAIL="${PROD_ACCOUNT_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
DEV_ACCOUNT_EMAIL="${DEV_ACCOUNT_ID}@${PROJECT_ID}.iam.gserviceaccount.com"

COMMON_ROLES=(
  roles/aiplatform.user
  roles/logging.logWriter
  roles/monitoring.metricWriter
  roles/serviceusage.serviceUsageConsumer
  roles/telemetry.tracesWriter
)

print_command() {
  printf '  '
  printf '%q ' "$@"
  printf '\n'
}

print_plan() {
  echo "FoxQuiz dedicated runtime identity plan"
  echo "Project: ${PROJECT_ID}"
  echo "Production identity: ${PROD_ACCOUNT_EMAIL}"
  echo "Production Firestore database: ${PROD_DATABASE_ID}"
  echo "DEV identity: ${DEV_ACCOUNT_EMAIL}"
  echo "DEV Firestore database: ${DEV_DATABASE_ID}"
  echo
  echo "Ensure service accounts:"
  print_command gcloud iam service-accounts create "${PROD_ACCOUNT_ID}" \
    --project "${PROJECT_ID}" \
    --display-name "FoxQuiz production Cloud Run runtime"
  print_command gcloud iam service-accounts create "${DEV_ACCOUNT_ID}" \
    --project "${PROJECT_ID}" \
    --display-name "FoxQuiz DEV Cloud Run runtime"

  echo
  echo "Grant common runtime roles to each identity:"
  local role account_email
  for account_email in "${PROD_ACCOUNT_EMAIL}" "${DEV_ACCOUNT_EMAIL}"; do
    for role in "${COMMON_ROLES[@]}"; do
      print_command gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member "serviceAccount:${account_email}" \
        --role "${role}" \
        --condition None
    done
  done

  echo
  echo "Grant database-scoped Firestore access:"
  print_command gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member "serviceAccount:${PROD_ACCOUNT_EMAIL}" \
    --role roles/datastore.user \
    --condition \
    "expression=resource.name==\"projects/${PROJECT_ID}/databases/${PROD_DATABASE_ID}\",title=foxquiz_prod_firestore,description=FoxQuiz production database only"
  print_command gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member "serviceAccount:${DEV_ACCOUNT_EMAIL}" \
    --role roles/datastore.user \
    --condition \
    "expression=resource.name==\"projects/${PROJECT_ID}/databases/${DEV_DATABASE_ID}\",title=foxquiz_dev_firestore,description=FoxQuiz DEV database only"

  echo
  echo "Excluded from both runtime identities:"
  echo "  roles/artifactregistry.writer"
  echo "  roles/storage.objectViewer"
}

ensure_service_account() {
  local account_id="$1"
  local account_email="$2"
  local display_name="$3"

  if gcloud iam service-accounts describe "${account_email}" \
    --project "${PROJECT_ID}" >/dev/null 2>&1; then
    echo "Service account already exists: ${account_email}"
    return
  fi

  gcloud iam service-accounts create "${account_id}" \
    --project "${PROJECT_ID}" \
    --display-name "${display_name}"
}

grant_common_roles() {
  local account_email="$1"
  local role
  for role in "${COMMON_ROLES[@]}"; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member "serviceAccount:${account_email}" \
      --role "${role}" \
      --condition None \
      --quiet >/dev/null
    echo "Granted ${role} to ${account_email}"
  done
}

grant_firestore_role() {
  local account_email="$1"
  local database_id="$2"
  local condition_title="$3"
  local condition_description="$4"

  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member "serviceAccount:${account_email}" \
    --role roles/datastore.user \
    --condition \
    "expression=resource.name==\"projects/${PROJECT_ID}/databases/${database_id}\",title=${condition_title},description=${condition_description}" \
    --quiet >/dev/null
  echo "Granted database-scoped Firestore access to ${account_email}: ${database_id}"
}

print_plan

if [[ "${APPLY_CHANGES}" != "true" ]]; then
  echo
  echo "Dry run only. Re-run with --apply to change Google Cloud IAM."
  exit 0
fi

echo
read -r -p "Type APPLY to create accounts and grant the listed roles: " CONFIRMATION
if [[ "${CONFIRMATION}" != "APPLY" ]]; then
  echo "No changes applied."
  exit 1
fi

ensure_service_account \
  "${PROD_ACCOUNT_ID}" \
  "${PROD_ACCOUNT_EMAIL}" \
  "FoxQuiz production Cloud Run runtime"
ensure_service_account \
  "${DEV_ACCOUNT_ID}" \
  "${DEV_ACCOUNT_EMAIL}" \
  "FoxQuiz DEV Cloud Run runtime"

grant_common_roles "${PROD_ACCOUNT_EMAIL}"
grant_common_roles "${DEV_ACCOUNT_EMAIL}"
grant_firestore_role \
  "${PROD_ACCOUNT_EMAIL}" \
  "${PROD_DATABASE_ID}" \
  foxquiz_prod_firestore \
  "FoxQuiz production database only"
grant_firestore_role \
  "${DEV_ACCOUNT_EMAIL}" \
  "${DEV_DATABASE_ID}" \
  foxquiz_dev_firestore \
  "FoxQuiz DEV database only"

echo
echo "Runtime identities are provisioned. No Cloud Run service was changed."
echo "Migrate and verify DEV before assigning the production identity."
