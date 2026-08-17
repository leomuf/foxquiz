# DEV Cloud Run Deployment Plan

## Status

Planned. This document describes the intended workflow; it does not authorize
or perform a deployment.

## Goal

Deploy development revisions to a separate, temporary public Cloud Run service
without changing the production service or the `foxquiz.app` domain mapping.

The target topology is:

- production service: `foxquiz`;
- development service: `foxquiz-dev`;
- Google Cloud project: `quiz-buddy-501017`;
- region: `us-east1`;
- DEV address: the stable `run.app` URL assigned to `foxquiz-dev`;
- logging: the existing Cloud Logging project, separated by the Cloud Run
  `service_name` resource label;
- access during tests: public and unauthenticated;
- access after tests: public access removed while the service remains available
  for a later test cycle;
- custom DEV domain: not planned.

The DEV service must never receive traffic from `foxquiz.app`. Production
deployments continue to target `foxquiz`; DEV deployments must always pass
`--service-name foxquiz-dev` explicitly.

## Architecture Decisions

1. Use a second Cloud Run service rather than a traffic-split revision of the
   production service. This prevents a DEV revision from receiving production
   traffic accidentally.
2. Keep DEV in the same project and region as production. This reuses the
   existing APIs, runtime identity, Vertex AI access, Firestore permissions,
   and Cloud Logging destination.
3. Set `--min-instances 0` and a conservative `--max-instances 2` explicitly.
   The installed `agents-cli` defaults a newly created service to one minimum
   instance and ten maximum instances when these arguments are omitted.
4. Make DEV public only for the manual test window. Removing the `allUsers`
   invoker binding is the normal way to take DEV offline for web users.
   `--min-instances 0` alone is not sufficient because an incoming request can
   start a new instance.
5. Do not configure Identity-Aware Proxy (IAP), a local authenticated proxy, or
   a custom DEV domain as part of this plan.
6. Deploy only committed source from a clean working tree so the footer and
   operational logs identify the exact deployed commit.

## Firestore Decision

Choose one of the following Firestore paths before implementing the permanent
DEV workflow.

### Firestore Path A: Share the Production `(default)` Database

This is the fastest path and requires no application change. A new Cloud Run
service in `quiz-buddy-501017` uses the project's default runtime identity and
the Firestore Python client connects to `(default)` when no database ID is
specified.

DEV can immediately exercise:

- shared quiz creation and retrieval;
- Thumbs Up and Thumbs Down feedback;
- `feedback_logs` and `quiz_quality_failures`;
- security configuration and security events;
- daily user and global token budgets;
- banned signatures;
- existing indexes and Time To Live (TTL) policies.

Risks and consequences:

- DEV feedback changes production counters and reports.
- DEV shared quizzes are stored beside production shared quizzes.
- DEV model usage contributes to the same global application token budget.
- malicious-input tests can create production security events and can ban the
  tester's IP for both DEV and production.
- a DEV persistence bug can modify or delete production data.

Use this path only for a controlled initial test. Do not delete Firestore data
afterwards unless every target document has been identified and confirmed as
test-only.

### Firestore Path B: Use a Named `foxquiz-dev` Database

This is the recommended permanent architecture. It preserves the same
Firestore behavior and the same Google Cloud project while isolating DEV
documents, budgets, feedback, and security events from production.

One-time database creation:

```bash
gcloud firestore databases create \
  --database=foxquiz-dev \
  --location=us-east1 \
  --type=firestore-native \
  --project=quiz-buddy-501017
```

Required implementation and provisioning work:

1. The application supports a `FIRESTORE_DATABASE_ID` runtime setting with
   `(default)` as the production-safe default.
2. The repository initializes `firestore.Client(database=database_id)` with
   the selected database ID.
3. Unit tests prove that an unset value selects `(default)` and the DEV value
   selects `foxquiz-dev`.
4. Pass `FIRESTORE_DATABASE_ID=foxquiz-dev` only to the DEV service.
5. Populate the DEV database with the required private security configuration
   through the existing private configuration procedure. Do not commit private
   detection rules or salts.
6. Create the `security_events` composite index for `foxquiz-dev`.
7. Enable the `expires_at` Time To Live (TTL) policies for the `budgets` and
   `quizzes` collection groups in `foxquiz-dev`.
8. Verify sharing, feedback, security events, quality failures, daily resets,
   and TTL timestamps before using DEV for release testing.

The DEV deploy command in this document shows the additional database variable
as an optional line. Include it only after Path B has been implemented and
verified.

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
- the current commit is the revision intended for manual testing;
- `gcloud` is authenticated to `quiz-buddy-501017`.

Do not continue when the current branch or commit is ambiguous.

## Phase 2: Prepare Build Metadata

```bash
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Refusing DEV deployment: commit or remove all workspace changes."
  exit 1
fi

COMMIT_SHA="$(git rev-parse HEAD)"
BASE_VERSION="$(uv version --short)"
AGENT_VERSION="${BASE_VERSION}-dev"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

The `-dev` suffix distinguishes the test deployment in the page footer while
the commit SHA identifies its exact source.

## Phase 3: Deploy `foxquiz-dev`

### Path A: Shared `(default)` Firestore Database

```bash
agents-cli deploy \
  --project quiz-buddy-501017 \
  --region us-east1 \
  --service-name foxquiz-dev \
  --min-instances 0 \
  --max-instances 2 \
  --no-confirm-project \
  --update-env-vars "COMMIT_SHA=${COMMIT_SHA},AGENT_VERSION=${AGENT_VERSION},BUILD_TIME=${BUILD_TIME},ENABLE_A2A=FALSE"
```

### Path B: Named `foxquiz-dev` Firestore Database

Use this command only after the Path B implementation and database preparation
are complete:

```bash
agents-cli deploy \
  --project quiz-buddy-501017 \
  --region us-east1 \
  --service-name foxquiz-dev \
  --min-instances 0 \
  --max-instances 2 \
  --no-confirm-project \
  --update-env-vars "COMMIT_SHA=${COMMIT_SHA},AGENT_VERSION=${AGENT_VERSION},BUILD_TIME=${BUILD_TIME},FIRESTORE_DATABASE_ID=foxquiz-dev,ENABLE_A2A=FALSE"
```

The installed `agents-cli` derives `APP_URL` from the explicit service name,
project number, and region. It therefore assigns the `foxquiz-dev` `run.app`
address rather than the production address.

Wait for the successful Cloud Run deployment message before continuing.

## Phase 4: Apply DEV Cloud Run Settings

`agents-cli deploy` does not expose the FoxQuiz CPU startup boost and Gen1
settings, so apply them to the DEV service explicitly:

```bash
gcloud run services update foxquiz-dev \
  --project quiz-buddy-501017 \
  --region us-east1 \
  --min-instances 0 \
  --cpu-boost \
  --execution-environment gen1
```

The existing project-level telemetry roles do not need to be granted again
when `foxquiz-dev` uses the same runtime service account as production.

Verify the runtime identity:

```bash
gcloud run services describe foxquiz-dev \
  --project=quiz-buddy-501017 \
  --region=us-east1 \
  --format='value(spec.template.spec.serviceAccountName)'
```

## Phase 5: Make DEV Public for the Test Window

`agents-cli deploy` creates or updates Cloud Run with unauthenticated access
disabled. Grant public invocation only after the deployment and post-settings
have succeeded:

```bash
gcloud run services add-iam-policy-binding foxquiz-dev \
  --member='allUsers' \
  --role='roles/run.invoker' \
  --project=quiz-buddy-501017 \
  --region=us-east1
```

Retrieve the stable DEV URL:

```bash
gcloud run services describe foxquiz-dev \
  --project=quiz-buddy-501017 \
  --region=us-east1 \
  --format='value(status.url)'
```

Open this `run.app` URL directly. Share links created there use the DEV browser
origin and therefore point back to the DEV service.

Repeat the public IAM binding after later DEV deployments if the deploy command
has restored private access.

## Phase 6: Manual DEV Verification

At minimum verify:

- footer version and short commit SHA;
- German, English, and Portuguese language switching;
- grade, subject, and topic selection;
- initial quiz generation;
- all ten answers and explanations;
- adaptive follow-up generation for each difficulty;
- Thumbs Up and Thumbs Down persistence;
- offline-save behavior;
- shared-quiz creation and retrieval through the DEV URL;
- localized error messages;
- safe, off-topic, malicious, and personally identifiable information (PII)
  routing;
- expected Firestore documents for the selected database path;
- absence of unexpected Cloud Run errors.

When using shared production Firestore, remember that malicious tests and
budget tests affect production state.

## Phase 7: Inspect Production and DEV Logs

Both services write to the same Cloud Logging project. The Cloud Run resource
label distinguishes them.

DEV only:

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="foxquiz-dev"' \
  --project=quiz-buddy-501017 \
  --freshness=1h \
  --limit=200
```

Production and DEV together:

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name=~"^foxquiz(-dev)?$"' \
  --project=quiz-buddy-501017 \
  --freshness=1h \
  --limit=300
```

Keep production log-based metrics and alerts separate from DEV. If DEV needs a
metric, create a second metric with the same event filter and
`resource.labels.service_name="foxquiz-dev"`; do not add DEV failures to the
production alert counter.

## Phase 8: Take DEV Offline After Testing

Remove anonymous invocation:

```bash
gcloud run services remove-iam-policy-binding foxquiz-dev \
  --member='allUsers' \
  --role='roles/run.invoker' \
  --project=quiz-buddy-501017 \
  --region=us-east1
```

Verify that the service no longer lists `allUsers` as an invoker:

```bash
gcloud run services get-iam-policy foxquiz-dev \
  --project=quiz-buddy-501017 \
  --region=us-east1
```

Do not delete the Cloud Run service after every test. Keeping the private
service preserves its stable URL and revisions while `min-instances 0` allows
it to scale to zero. Re-enable public access with the Phase 5 binding only for
the next authorized test window.

## Production Guardrail

The established production command continues to deploy the service name
`foxquiz`. Never reuse the DEV command without `--service-name foxquiz-dev`.

Suggested human-facing command distinction:

- `Deploy the application` or `Deploy to Google Cloud`: production workflow;
- `Deploy the DEV application`: DEV workflow from this plan.

Both workflows require an explicit description of the target and human
confirmation before any deployment or IAM change.

## Completion Criteria

- `foxquiz.app` still routes exclusively to `foxquiz`.
- `foxquiz-dev` has its own stable `run.app` URL.
- DEV displays the expected `-dev` version and commit.
- sharing and feedback work against the selected Firestore path.
- production and DEV logs are queryable together and separately.
- DEV can be made public for tests and returned to private access afterwards.
- no custom DEV domain, IAP configuration, or production traffic split exists.
