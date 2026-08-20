# DEV Cloud Run Deployment Plan

## Status

Implemented. FoxQuiz supports the isolated `foxquiz-dev` Firestore database,
dedicated runtime identity, temporary random Cloud Run services, and the guarded
`scripts/deploy.sh` workflow. Assigning the production identity remains a
separately approved operation.

## Target Architecture

- production service: `foxquiz`;
- DEV service: a new random `svc-...` name for each campaign, or the same random
  name when deliberately updating an active campaign;
- Google Cloud project: `GCLOUD_PROJECT_ID`;
- region: `us-east1`;
- DEV runtime identity: `foxquiz-dev-runtime`;
- DEV Firestore database: `foxquiz-dev`;
- DEV address: the service-specific `run.app` URL reported after deployment;
- access during tests: public and unauthenticated;
- access after tests: the temporary service is deleted;
- custom DEV domain, IAP, and production traffic split: not used.

DEV must never receive traffic from `foxquiz.app`. Connecting DEV to
production's `(default)` database or running it under the default Compute
Engine service account are rejected designs because they would mix budgets,
quizzes, feedback, security events, bans, and authorization.

## One-Time Prerequisites

Create the named DEV database:

```bash
gcloud firestore databases create \
  --database=foxquiz-dev \
  --location=us-east1 \
  --type=firestore-native \
  --project=GCLOUD_PROJECT_ID
```

Provision the dedicated identities, previewing first:

```bash
scripts/provision-runtime-identities.sh --project GCLOUD_PROJECT_ID
scripts/provision-runtime-identities.sh --project GCLOUD_PROJECT_ID --apply
```

The DEV database also needs its private security configuration,
`security_events` composite index, and Time To Live policies for the `budgets`
and `quizzes` collection groups. Never commit private detection rules or salts.

## Start a DEV Campaign

Before deployment, ensure the intended changes are committed, the credential-
free suites and relevant behavioral evaluations pass, `agents-cli lint` passes,
and the user has explicitly approved a DEV deployment.

Preview the complete plan without making cloud calls:

```bash
scripts/deploy.sh --environment dev --project GCLOUD_PROJECT_ID
```

After reviewing the generated service, identity, database, commit, resource
limits, and commands, apply the deployment:

```bash
scripts/deploy.sh \
  --environment dev \
  --project GCLOUD_PROJECT_ID \
  --apply
```

The script refuses a dirty worktree, generates the random name and build
metadata, deploys with the DEV identity and database, configures zero-to-two
instance scaling, startup CPU boost, Gen1 and public invocation, and verifies
the deployed configuration, root page, and `/version` metadata.

Record the reported values only in the ignored local `.env` file if the
campaign spans multiple shells:

```bash
export GCLOUD_RUN_DEV_SERVICE_NAME="<RANDOM_DEV_SERVICE_NAME>"
export GCLOUD_RUN_DEV_URL="<GCLOUD_RUN_DEV_URL>"
```

The address is not a credential. Anyone who obtains it can access the public
service; its random name only makes guessing impractical.

## Update the Active DEV Campaign

Pass the recorded random name to deploy a new revision without changing the
campaign URL:

```bash
scripts/deploy.sh \
  --environment dev \
  --project GCLOUD_PROJECT_ID \
  --service-name "${GCLOUD_RUN_DEV_SERVICE_NAME}"

scripts/deploy.sh \
  --environment dev \
  --project GCLOUD_PROJECT_ID \
  --service-name "${GCLOUD_RUN_DEV_SERVICE_NAME}" \
  --apply
```

The service-name option accepts only the random `svc-` format and is rejected
for production.

## Verify DEV Behavior

The deployment script verifies infrastructure and build identity. The release
campaign must additionally verify application behavior:

- German, English, and Portuguese language switching;
- initial and adaptive quiz generation;
- all ten answers and explanations;
- feedback, shared-quiz creation, and retrieval in `foxquiz-dev`;
- safe, off-topic, malicious, and personally identifiable information routing;
- structured logs, metrics, traces, and absence of permission errors.

Inspect only the current DEV service's logs:

```bash
gcloud logging read \
  "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${GCLOUD_RUN_DEV_SERVICE_NAME}\"" \
  --project GCLOUD_PROJECT_ID \
  --freshness 1h \
  --limit 200
```

Keep production metrics and alerts separate. A DEV log-based metric must filter
on the current random service name.

## End the DEV Campaign

Deployment never deletes a service automatically. Resolve and inspect the exact
target first:

```bash
test -n "${GCLOUD_RUN_DEV_SERVICE_NAME:-}"

gcloud run services describe "${GCLOUD_RUN_DEV_SERVICE_NAME}" \
  --project GCLOUD_PROJECT_ID \
  --region us-east1
```

After confirming the project, region, and random name, delete it:

```bash
gcloud run services delete "${GCLOUD_RUN_DEV_SERVICE_NAME}" \
  --project GCLOUD_PROJECT_ID \
  --region us-east1
```

Read the confirmation prompt carefully and remove local campaign variables
afterward. A later campaign generates a new service name and URL.

## Production Guardrail

Production uses fixed service `foxquiz`, the dedicated production identity, and
the `(default)` Firestore database. It is deployed only through
`scripts/deploy.sh --environment prod` after successful DEV verification and
separate explicit approval. Never adapt a DEV command by merely changing its
service name.

## Completion Criteria

- `foxquiz.app` still routes exclusively to production service `foxquiz`;
- DEV uses its dedicated runtime identity and named database;
- DEV displays the expected `-dev` version and exact commit;
- model calls, persistence, and telemetry complete without permission errors;
- the temporary public service is deleted after testing;
- no custom DEV domain, IAP configuration, or production traffic split exists.
