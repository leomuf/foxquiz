# DEV Cloud Run Deployment Plan

## Status

Implemented. FoxQuiz supports the isolated `foxquiz-dev` Firestore database,
and `scripts/provision-runtime-identities.sh` provisions separate production
and DEV identities with database-scoped Firestore access. The DEV identity was
verified on a temporary DEV service on 2026-08-20. Assigning the production
identity remains a separately approved operation.

## Goal

Deploy a committed development revision to a temporary public Cloud Run service
without changing the production `foxquiz` service or its `foxquiz.app` domain
mapping.

The target topology is:

- production service: `foxquiz`;
- DEV service: a new random `GCLOUD_RUN_DEV_SERVICE_NAME` for each campaign;
- Google Cloud project: `GCLOUD_PROJECT_ID`;
- region: `us-east1`;
- DEV address: the campaign-specific `run.app` URL returned by Cloud Run;
- DEV runtime identity: `foxquiz-dev-runtime`;
- DEV Firestore database: `foxquiz-dev`;
- logging: the existing Cloud Logging project, separated by the Cloud Run
  `service_name` resource label;
- access during tests: public and unauthenticated;
- access after tests: the temporary service is deleted;
- custom DEV domain: not planned.

The DEV service must never receive traffic from `foxquiz.app`. A DEV deployment
must explicitly pass the random service name and dedicated DEV identity. Never
reuse a previous campaign name.

## Architecture Decisions

1. Use a separate Cloud Run service rather than a traffic-split production
   revision. This prevents DEV from receiving production traffic.
2. Keep DEV in the same project and region while isolating its Cloud Run
   identity and Firestore authorization from production.
3. Use a new, cryptographically random service name for each temporary public
   campaign. Do not disclose it in Git, pull requests, issues, screenshots, or
   shared logs.
4. Set `--min-instances 0` and `--max-instances 2` explicitly to bound idle and
   peak costs.
5. Keep A2A disabled and do not configure IAP or a custom DEV domain for the
   campaign.
6. Deploy only committed source from a clean worktree so the UI and operational
   logs identify the exact source revision.
7. Delete the temporary service after testing. Scaling to zero does not make a
   public endpoint inaccessible.

## Firestore and Runtime Identity Decision

DEV must use the named `foxquiz-dev` database and the dedicated
`foxquiz-dev-runtime` service account. The account receives the runtime roles
needed for Vertex AI and telemetry, while its `roles/datastore.user` binding is
conditioned on the `foxquiz-dev` database.

Connecting DEV to production's `(default)` database or running DEV under the
default Compute Engine service account are rejected designs. They would mix
budgets, quizzes, feedback, security events, bans, and authorization between
environments.

One-time database creation:

```bash
gcloud firestore databases create \
  --database=foxquiz-dev \
  --location=us-east1 \
  --type=firestore-native \
  --project=GCLOUD_PROJECT_ID
```

One-time identity preparation starts with a dry run and requires explicit
approval before `--apply`:

```bash
scripts/provision-runtime-identities.sh --project GCLOUD_PROJECT_ID
scripts/provision-runtime-identities.sh --project GCLOUD_PROJECT_ID --apply
```

The script does not update Cloud Run. Before a campaign, the DEV database also
needs its private security configuration, `security_events` composite index,
and Time To Live (TTL) policies for the `budgets` and `quizzes` collection
groups. Never commit private detection rules or salts.

## Phase 1: Pre-Deployment Verification

Run from the repository root on the development branch:

```bash
git status --short
git branch --show-current
git log -1 --oneline
```

Required conditions:

- the working tree is clean;
- all intended DEV changes are committed;
- unit, integration, and frontend tests pass;
- `agents-cli lint` passes;
- the current commit is the revision intended for testing;
- `gcloud` is authenticated to `GCLOUD_PROJECT_ID`;
- the dedicated DEV identity and database prerequisites exist;
- the user has explicitly approved this DEV deployment.

Do not continue when the project, branch, commit, or target is ambiguous.

## Phase 2: Prepare Campaign Values

```bash
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Refusing DEV deployment: commit or remove all workspace changes."
  exit 1
fi

COMMIT_SHA="$(git rev-parse HEAD)"
BASE_VERSION="$(uv version --short)"
AGENT_VERSION="${BASE_VERSION}-dev"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
GCLOUD_RUN_DEV_SERVICE_NAME="svc-$(openssl rand -hex 10)"
GCLOUD_RUN_DEV_SERVICE_ACCOUNT="foxquiz-dev-runtime@GCLOUD_PROJECT_ID.iam.gserviceaccount.com"
```

Keep `GCLOUD_RUN_DEV_SERVICE_NAME` and the resulting URL only in the ignored
local `.env` file if the campaign spans multiple shells.

Confirm the generated name does not already exist:

```bash
gcloud run services describe "${GCLOUD_RUN_DEV_SERVICE_NAME}" \
  --project GCLOUD_PROJECT_ID \
  --region us-east1
```

The expected result is “not found.” Stop if an existing service is returned.

## Phase 3: Deploy the Temporary DEV Service

```bash
agents-cli deploy \
  --project GCLOUD_PROJECT_ID \
  --region us-east1 \
  --service-name "${GCLOUD_RUN_DEV_SERVICE_NAME}" \
  --service-account "${GCLOUD_RUN_DEV_SERVICE_ACCOUNT}" \
  --cpu 1 \
  --memory 4Gi \
  --concurrency 8 \
  --min-instances 0 \
  --max-instances 2 \
  --no-confirm-project \
  --update-env-vars "COMMIT_SHA=${COMMIT_SHA},AGENT_VERSION=${AGENT_VERSION},BUILD_TIME=${BUILD_TIME},FIRESTORE_DATABASE_ID=foxquiz-dev,ENABLE_A2A=FALSE,ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=FALSE"
```

Wait for the deployment to succeed before applying post-deployment settings.

## Phase 4: Apply and Verify Cloud Run Settings

```bash
gcloud run services update "${GCLOUD_RUN_DEV_SERVICE_NAME}" \
  --project GCLOUD_PROJECT_ID \
  --region us-east1 \
  --min-instances 0 \
  --cpu-boost \
  --execution-environment gen1

gcloud run services describe "${GCLOUD_RUN_DEV_SERVICE_NAME}" \
  --project GCLOUD_PROJECT_ID \
  --region us-east1 \
  --format='value(spec.template.spec.serviceAccountName)'
```

The second command must return the dedicated DEV service-account email. Stop
before public access if it reports the default Compute Engine or production
identity.

## Phase 5: Make DEV Public for the Test Window

```bash
gcloud run services add-iam-policy-binding \
  "${GCLOUD_RUN_DEV_SERVICE_NAME}" \
  --member='allUsers' \
  --role='roles/run.invoker' \
  --project GCLOUD_PROJECT_ID \
  --region us-east1

export GCLOUD_RUN_DEV_URL="$(gcloud run services describe \
  "${GCLOUD_RUN_DEV_SERVICE_NAME}" \
  --project GCLOUD_PROJECT_ID \
  --region us-east1 \
  --format='value(status.url)')"
```

Open `GCLOUD_RUN_DEV_URL` directly. Share links created there use the DEV origin
and therefore return to the temporary DEV service.

## Phase 6: Verify DEV Behavior

At minimum verify:

- footer version, commit SHA, and `/version` response;
- root page and ADK session creation;
- A2A-disabled response;
- runtime identity and bounded resource settings;
- German, English, and Portuguese language switching;
- initial and adaptive quiz generation;
- all ten answers and explanations;
- feedback, shared-quiz creation, and shared-quiz retrieval in `foxquiz-dev`;
- safe, off-topic, malicious, and personally identifiable information routing;
- structured logs, metrics, traces, and absence of permission errors.

## Phase 7: Inspect Production and DEV Logs

DEV only:

```bash
gcloud logging read \
  "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${GCLOUD_RUN_DEV_SERVICE_NAME}\"" \
  --project GCLOUD_PROJECT_ID \
  --freshness 1h \
  --limit 200
```

Keep production metrics and alerts separate. If DEV needs a log-based metric,
filter it by the current random service name; do not include DEV failures in a
production alert counter.

## Phase 8: Delete the Temporary DEV Service

Resolve and inspect the exact target first:

```bash
test -n "${GCLOUD_RUN_DEV_SERVICE_NAME:-}"

gcloud run services describe "${GCLOUD_RUN_DEV_SERVICE_NAME}" \
  --project GCLOUD_PROJECT_ID \
  --region us-east1
```

After confirming the project, region, and random name, delete the completed
campaign service:

```bash
gcloud run services delete "${GCLOUD_RUN_DEV_SERVICE_NAME}" \
  --project GCLOUD_PROJECT_ID \
  --region us-east1
```

Read the confirmation prompt carefully. Remove the local campaign variables
afterward. A later campaign generates a new service name and URL.

## Production Guardrail

Production continues to use service name `foxquiz` and the dedicated
`foxquiz-prod-runtime` identity. Never adapt the DEV command by merely changing
its service name. Production deployment or identity migration requires its own
explicit approval after DEV verification succeeds.

## Completion Criteria

- `foxquiz.app` still routes exclusively to production service `foxquiz`;
- the temporary DEV service uses the dedicated DEV identity and database;
- DEV displays the expected `-dev` version and exact commit;
- sharing and feedback work only in `foxquiz-dev`;
- model calls and telemetry complete without permission errors;
- the temporary public service is deleted after testing;
- no custom DEV domain, IAP configuration, or production traffic split exists.
