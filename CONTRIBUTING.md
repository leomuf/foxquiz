# Contributing to FoxQuiz 🦊

First off, thank you for checking out FoxQuiz! We are thrilled that you want to help us build a more engaging, gamified, and child-safe learning companion for kids around the world. 

FoxQuiz is developed and maintained by **AUTOSOFT Engineering** and is now open to public contributions. By contributing to this project, you help make education more interactive, personalized, and accessible.

---

## ⚖️ Licensing & Intellectual Property

By contributing your code, documentation, or other materials to FoxQuiz, you agree that:
1. Your contributions will be licensed under the project's **Creative Commons Attribution 4.0 International (CC BY 4.0)** license.
2. Any upstream components belonging to Google LLC's Agent Development Kit (ADK) remain preserved under their respective **Apache License 2.0**.
3. You grant AUTOSOFT Engineering and the public the right to use, modify, and distribute your contributions freely under these terms.

---

## 🚀 How to Get Started

### 1. Reporting Bugs & Requesting Features
If you find a bug or have a great idea for a new mascot, educational topic, or UI feature:
* Check the existing [GitHub Issues](https://github.com/leomuf/foxquiz/issues) to see if it has already been reported.
* If not, open a new issue with a clear description, steps to reproduce, and expected behavior.

### 2. Local Development Setup
To work on the codebase locally, we use `uv` (a fast Python package installer and resolver) to manage dependencies.

#### Install and Update Development Tools

Run the following commands inside Ubuntu or WSL. Installing these tools only on
Windows does not make them available to commands executed in the Linux project
environment.

Install Agents CLI with `uv` when it is not already available:
```bash
uv tool install google-agents-cli
```

Upgrade an existing Agents CLI installation and verify the installed version:
```bash
uv tool upgrade google-agents-cli
agents-cli info
```

The CLI package and its coding-agent skills are updated separately. After
upgrading the package, refresh the installed skills when required:
```bash
agents-cli update
```

1. **Fork and Clone the Repository:**
   ```bash
   git clone https://github.com/your-username/foxquiz.git
   cd foxquiz
   ```

2. **Install Dependencies:**
   Make sure you have [uv](https://github.com/astral-sh/uv) installed, then run:
   ```bash
   uv sync --locked
   ```
   The `--locked` option ensures that `uv.lock` matches `pyproject.toml`
   and fails instead of modifying the lockfile. Without it, `uv` could silently update `uv.lock`, causing local and CI environments to use different dependency versions.

3. **Configure Environment Variables:**
   Copy the example environment file and fill in your credentials:
   ```bash
   cp .env.example .env
   ```
   *Edit the `.env` file to add your `GOOGLE_CLOUD_PROJECT` or test variables.*

4. **Run the Application locally:**
   ```bash
   uv run uvicorn app.fast_api_app:app --reload
   ```

   The FoxQuiz browser creates the required structured request automatically.
   When using the ADK playground directly, enter JSON text such as:

   ```json
   {"grade":"Grade 8","subject":"Biology","topic":"Cells","preferred_language":"en"}
   ```

   Free-form chat prompts and incomplete payloads are intentionally rejected
   without an LLM call.

#### Inspecting Local Quiz Logs

For troubleshooting a complete quiz request, run the application in the
foreground and save the backend output at the same time:

```bash
GOOGLE_CLOUD_PROJECT=<YOUR_PROJECT_ID> \
GCLOUD_PROJECT=<YOUR_PROJECT_ID> \
uv run uvicorn app.fast_api_app:app \
  --reload \
  --host 127.0.0.1 \
  --port 8000 \
  --log-level debug \
  2>&1 | tee /tmp/foxquiz-local.log
```

These environment variable assignments apply only to the launched process and
do not write the project ID to a repository file.

Follow the complete log from a second terminal while performing the quiz:

```bash
tail -f /tmp/foxquiz-local.log
```

Filter the saved log for the most relevant agent stages and failures:

```bash
grep -Ei \
  "Gather and Route|validated structured|quiz contract|curriculum|compatib|Generating Quiz|Judge|ERROR|WARNING" \
  /tmp/foxquiz-local.log
```

For curriculum-routing problems, inspect these messages in order:

- `Gather and Route started.`
- `Loaded validated structured quiz parameters.`
- `Performing upfront curriculum validation check`
- `Upfront curriculum check completed with status=...`
- `Curriculum Search Skill invoked`
- `Generating Quiz`

Also open the browser developer tools (`F12`) while reproducing the problem:

- **Console** shows JavaScript and response-processing errors.
- **Network > `run_sse`** shows the prompt sent to FoxQuiz and all returned
  agent events.
- **Response** helps determine whether incorrect content came from the backend
  or was interpreted incorrectly by the frontend.

### 3. Running Quality Checks
Before submitting your changes, please ensure that all tests and code quality checks pass.

* **Install the Chromium browser used by the frontend tests (one-time):**
  ```bash
  uv run playwright install chromium
  ```

* **Run all credential-free tests:**
  ```bash
  uv run python -m pytest \
    tests/unit tests/integration tests/browser \
    -m "not google_cloud"
  ```

  This is the same test boundary used by GitHub Actions. It runs the unit,
  deterministic server, and mocked browser tests without contacting Google
  Cloud.

  The browser tests mock the session, Server-Sent Events (SSE), and persistence
  responses. They verify language and mascot selection, grade/subject/topic
  submission, completion of a ten-question quiz, scoring, negative-feedback
  context, and blocked-response handling without calling Gemini, Vertex AI, or
  Firestore.

* **Run Google-dependent integration tests locally:**
  ```bash
  GOOGLE_CLOUD_PROJECT=<YOUR_PROJECT_ID> \
  GCLOUD_PROJECT=<YOUR_PROJECT_ID> \
  uv run python -m pytest tests/integration -m google_cloud
  ```

  These tests invoke the real agent and require local Application Default
  Credentials. They are deliberately excluded from GitHub Actions; never add
  Google credentials or service-account keys to the repository.

* **Run the code linter:**
  ```bash
  agents-cli lint
  ```

### 4. Deploying & Infrastructure Optimization (For Maintainers)

This section is the single source of truth for using the provisioning and
deployment scripts. The README intentionally provides only an overview and
links here so the commands cannot drift between two documents.

`scripts/deploy.sh` is the single entry point for manual DEV and production
deployments. It generates build metadata, runs `agents-cli deploy`, applies the
Cloud Run scaling and startup settings, grants public invocation, and verifies
the deployed identity, configuration, root page, and `/version` response.

One-time IAM and Firestore preparation remains separate. FoxQuiz intentionally
does not use the optional Terraform infrastructure stack; do not run
`agents-cli infra single-project` for this project.

Set the target project once before following the steps below:

```bash
export GCLOUD_PROJECT_ID="<GCLOUD_PROJECT_ID>"
```

Run either script with `--help` to inspect its supported options without making
changes.

#### Step 1: Initialize Firestore (One-Time Project Prerequisite)

Create the Native Mode Firestore database once for every new Google Cloud
project:
```bash
gcloud firestore databases create \
  --project="${GCLOUD_PROJECT_ID}" \
  --location=us-east1 \
  --type=firestore-native
```

The `us-east1` location keeps Firestore in the same region as FoxQuiz.
Skip this command when the `(default)` database already exists.

#### Step 2: Provision Runtime Identities (One-Time Prerequisite)

Provision the dedicated identities before the first deployment. The first
command is a dry run; the second changes IAM and therefore requires explicit
maintainer approval:

```bash
scripts/provision-runtime-identities.sh --project "${GCLOUD_PROJECT_ID}"
scripts/provision-runtime-identities.sh \
  --project "${GCLOUD_PROJECT_ID}" \
  --apply
```

Provisioning does not update either Cloud Run service. If production still uses
the default Compute Engine service account, first deploy and verify DEV with its
dedicated identity.

#### Step 3: Preview and Deploy with `scripts/deploy.sh`

The script is dry-run-only unless `--apply` is present. Always inspect the
rendered project, environment, service, identity, database, commit, and commands
before applying them.

Preview and deploy DEV with a new random `svc-...` service name:

```bash
scripts/deploy.sh --environment dev --project "${GCLOUD_PROJECT_ID}"
scripts/deploy.sh --environment dev --project "${GCLOUD_PROJECT_ID}" --apply
```

To update the current DEV campaign rather than create another random service,
pass its locally recorded name explicitly:

```bash
scripts/deploy.sh \
  --environment dev \
  --project "${GCLOUD_PROJECT_ID}" \
  --service-name "${GCLOUD_RUN_DEV_SERVICE_NAME}"

scripts/deploy.sh \
  --environment dev \
  --project "${GCLOUD_PROJECT_ID}" \
  --service-name "${GCLOUD_RUN_DEV_SERVICE_NAME}" \
  --apply
```

Preview and deploy production only after DEV verification and separate
production approval:

```bash
scripts/deploy.sh --environment prod --project "${GCLOUD_PROJECT_ID}"
scripts/deploy.sh --environment prod --project "${GCLOUD_PROJECT_ID}" --apply
```

Every `--apply` form requires a clean worktree and the exact typed confirmation
shown by the script. The deployer must have `roles/iam.serviceAccountUser` on
the selected runtime identity. Production is fixed to service `foxquiz`; DEV
uses `foxquiz-dev-runtime`, database `foxquiz-dev`, and at most two instances.
The script never creates IAM roles, Firestore resources, tags, or releases.

#### Step 4: Configure Remaining Infrastructure Manually

These one-time resources are not part of an application deployment.

##### Step 4.1: Firestore Security-Event Composite Index

Create the index required by the automated Sheriff to count recent violations
for one privacy-preserving client signature:

```bash
gcloud firestore indexes composite create \
  --collection-group=security_events \
  --database='(default)' \
  --query-scope=collection \
  --field-config=field-path=hashed_ip,order=ascending \
  --field-config=field-path=timestamp,order=ascending \
  --project="${GCLOUD_PROJECT_ID}"
```

Run this once per project. Index creation may take several minutes. Check that
its state is `READY` before testing the Sheriff:

```bash
gcloud firestore indexes composite list \
  --database='(default)' \
  --project="${GCLOUD_PROJECT_ID}" \
  --format='table(name.basename(),queryScope,state,fields)'
```

##### Step 4.2: Firestore Time To Live (TTL) Policies
```bash
gcloud firestore fields ttls update expires_at \
  --collection-group=budgets \
  --database='(default)' \
  --enable-ttl \
  --project="${GCLOUD_PROJECT_ID}"

gcloud firestore fields ttls update expires_at \
  --collection-group=quizzes \
  --database='(default)' \
  --enable-ttl \
  --project="${GCLOUD_PROJECT_ID}"
```

The application sets `expires_at` to seven days for transient budgets and
30 days for shared quizzes. Firestore TTL performs the eventual physical
deletion.

##### Step 4.3: Firestore Failure Counter

Create a project-level logs-based counter once so Firestore outages can be
tracked over time without storing prompts, IP addresses, signatures, private
rules, or exception messages:

```bash
gcloud logging metrics create foxquiz_firestore_operation_failures \
  --project="${GCLOUD_PROJECT_ID}" \
  --description='Count of privacy-safe FoxQuiz Firestore operation failures' \
  --log-filter='resource.type="cloud_run_revision" AND resource.labels.service_name="foxquiz" AND jsonPayload.event="firestore_operation_failed"'
```

The application emits one structured event for each failed Firestore operation.
The event contains only the phase, operation name, exception class/code, service
version, and deployed commit. An alert policy can be added later if operational
notifications are needed; it is not required for deployment.

#### Step 5: Configure a Custom Domain (Optional)

```bash
gcloud beta run domain-mappings create \
  --service=foxquiz \
  --domain=www.foxquiz.app \
  --project="${GCLOUD_PROJECT_ID}" \
  --region=us-east1
```

Configure a CNAME record at the domain registrar that points `www` to
`ghs.googlehosted.com.`.

#### Temporary Public DEV Campaigns

Maintainer-led DEV campaigns normally remain available for two to five days so
the application can be tested from multiple unauthenticated smartphones. Each
campaign must use a new, cryptographically random Cloud Run service name. Do
not reuse predictable names containing the project, application, environment,
date, version, or previous service name.

Start a new campaign with the new-DEV preview and apply workflow in
[Step 3](#step-3-preview-and-deploy-with-scriptsdeploysh). Do not pass
`--service-name`: the deployment script must generate the campaign's random
name. It uses 80 bits of randomness, verifies the dedicated DEV configuration,
and prints the service name and URL after success.

The preview is non-mutating. The apply form refuses a dirty worktree, checks the
runtime identity, confirms that an automatically generated name is unused,
requires `DEPLOY DEV`, deploys, configures public access and resource limits,
then verifies the service.

For work spanning multiple shells, record the reported values only in the
ignored local `.env` file:

```bash
export GCLOUD_RUN_DEV_SERVICE_NAME="<RANDOM_DEV_SERVICE_NAME>"
export GCLOUD_RUN_DEV_URL="<GCLOUD_RUN_DEV_URL>"
```

The address is not a credential: public invocation means anyone who obtains it
can access the service. Its random name makes guessing impractical, while token
budgets, Sheriff blocking, and the two-instance ceiling remain the abuse and
cost controls during the campaign.

Verify the root page, `/version`, ADK session creation, A2A-disabled response,
runtime identity, resource limits, and security behavior before beginning the
evaluation pilot. Monitor request errors, token budgets, and security events
throughout the campaign.

At campaign end, the maintainer manually deletes the exact service recorded in
`GCLOUD_RUN_DEV_SERVICE_NAME`:

```bash
test -n "${GCLOUD_PROJECT_ID:-}"
test -n "${GCLOUD_RUN_DEV_SERVICE_NAME:-}"

gcloud run services describe "${GCLOUD_RUN_DEV_SERVICE_NAME}" \
  --project "${GCLOUD_PROJECT_ID}" \
  --region us-east1

gcloud run services delete "${GCLOUD_RUN_DEV_SERVICE_NAME}" \
  --project "${GCLOUD_PROJECT_ID}" \
  --region us-east1
```

Read the confirmation prompt carefully before deleting. Afterwards, remove the
two local campaign variables. `min-instances=0` only scales idle containers to
zero; it does not deactivate the public endpoint. The next campaign must
generate a completely new random service name and URL.

### Inspecting Production Logs Programmatically

Use `gcloud logging read` to search Cloud Run logs without manually scrolling
through Logs Explorer. Adjust `--freshness`, `--limit`, and the project or service
names as needed.

Show the latest FoxQuiz log entries:
```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=foxquiz' \
  --project=<YOUR_PROJECT_ID> \
  --freshness=1h \
  --limit=100 \
  --order=desc \
  --format='table(timestamp,severity,textPayload)'
```

Hide OpenTelemetry exporter noise while investigating application messages:
```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=foxquiz AND NOT textPayload:opentelemetry.exporter' \
  --project=<YOUR_PROJECT_ID> \
  --freshness=24h \
  --limit=500 \
  --order=desc \
  --format='table(timestamp,severity,textPayload)'
```

Find errors, including Python messages whose severity was not parsed into the
structured `severity` field:
```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=foxquiz AND NOT textPayload:opentelemetry.exporter AND (severity>=ERROR OR textPayload:ERROR OR textPayload:Exception OR textPayload:Traceback)' \
  --project=<YOUR_PROJECT_ID> \
  --freshness=24h \
  --limit=500 \
  --order=desc \
  --format='table(timestamp,severity,textPayload)'
```

Find privacy-safe Firestore failure events and group them by operation:

```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=foxquiz AND jsonPayload.event="firestore_operation_failed"'  \
  --project=<YOUR_PROJECT_ID> \
  --freshness=24h \
  --limit=500 \
  --order=desc \
  --format='table(timestamp,jsonPayload.phase,jsonPayload.operation,jsonPayload.error_type,jsonPayload.error_code,jsonPayload.deployment_revision)'
```

The logs-based counter is available as
`logging.googleapis.com/user/foxquiz_firestore_operation_failures` in Cloud
Monitoring.

#### Analyzing token usage

FoxQuiz emits privacy-minimized `llm_token_usage` events for direct Gemini
responses and at most one `llm_invocation_token_summary` per invocation with
model usage. These events contain only allowlisted stage names, numeric token
counters, bounded attempt numbers, terminal outcomes, and trusted build
metadata. They do not contain prompts, responses, quiz content, learner data,
IP addresses, or persistent client identifiers.

Inspect the numeric call breakdown for one deployed revision:

```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=<SERVICE_NAME> AND jsonPayload.event="llm_token_usage" AND jsonPayload.deployment_revision="<REVISION>"' \
  --project=<YOUR_PROJECT_ID> \
  --freshness=24h \
  --limit=10000 \
  --order=asc \
  --format='table(timestamp,jsonPayload.call_stage,jsonPayload.generation_attempt,jsonPayload.judge_attempt,jsonPayload.prompt_token_count,jsonPayload.cached_content_token_count,jsonPayload.candidates_token_count,jsonPayload.thoughts_token_count,jsonPayload.total_token_count)'
```

Count successful quiz summaries. This is the authoritative rollout count;
completed HTTP requests can also represent clarification or quality-failure
responses:

```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=<SERVICE_NAME> AND jsonPayload.event="llm_invocation_token_summary" AND jsonPayload.terminal_outcome="success" AND jsonPayload.deployment_revision="<REVISION>"' \
  --project=<YOUR_PROJECT_ID> \
  --freshness=24h \
  --limit=10000 \
  --format='value(timestamp)' \
  | wc -l
```

Aggregate prompt, cached, candidate, thinking, and total tokens by workflow
stage without printing any unrelated log payloads:

```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=<SERVICE_NAME> AND jsonPayload.event="llm_token_usage" AND jsonPayload.deployment_revision="<REVISION>"' \
  --project=<YOUR_PROJECT_ID> \
  --freshness=24h \
  --limit=10000 \
  --format='csv[no-heading](jsonPayload.call_stage,jsonPayload.prompt_token_count,jsonPayload.cached_content_token_count,jsonPayload.candidates_token_count,jsonPayload.thoughts_token_count,jsonPayload.total_token_count)' \
  | awk -F, '{calls[$1]++; prompt[$1]+=$2; cached[$1]+=$3; candidate[$1]+=$4; thoughts[$1]+=$5; total[$1]+=$6} END {for (stage in calls) printf "%s calls=%d prompt=%d cached=%d candidate=%d thoughts=%d thinking_share=%.2f%% total=%d\n", stage,calls[stage],prompt[stage],cached[stage],candidate[stage],thoughts[stage],(total[stage] ? 100*thoughts[stage]/total[stage] : 0),total[stage]}' \
  | sort
```

Calculate call-level cache-hit rate:

```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=<SERVICE_NAME> AND jsonPayload.event="llm_token_usage" AND jsonPayload.deployment_revision="<REVISION>"' \
  --project=<YOUR_PROJECT_ID> \
  --freshness=24h \
  --limit=10000 \
  --format='value(jsonPayload.cached_content_token_count)' \
  | awk '{calls++; if ($1>0) hits++} END {if (calls) printf "calls=%d cache_hits=%d hit_rate=%.2f%%\n", calls,hits,100*hits/calls}'
```

Measure provider-reported token overhead from second generator or Judge calls:

```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=<SERVICE_NAME> AND jsonPayload.event="llm_token_usage" AND jsonPayload.deployment_revision="<REVISION>" AND (jsonPayload.generation_attempt>1 OR jsonPayload.judge_attempt>1)' \
  --project=<YOUR_PROJECT_ID> \
  --freshness=24h \
  --limit=10000 \
  --format='csv[no-heading](jsonPayload.call_stage,jsonPayload.total_token_count)' \
  | awk -F, '{calls[$1]++; total[$1]+=$2} END {for (stage in calls) printf "%s retry_calls=%d retry_tokens=%d\n", stage,calls[stage],total[stage]}' \
  | sort
```

Calculate average, median, and p95 total tokens for successful quizzes:

```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=<SERVICE_NAME> AND jsonPayload.event="llm_invocation_token_summary" AND jsonPayload.terminal_outcome="success" AND jsonPayload.deployment_revision="<REVISION>"' \
  --project=<YOUR_PROJECT_ID> \
  --freshness=24h \
  --limit=10000 \
  --format='value(jsonPayload.total_token_count)' \
  | sort -n \
  | awk '{values[NR]=$1; sum+=$1} END {if (NR) {p50=int((NR-1)*0.50)+1; p95=int((NR-1)*0.95)+1; printf "count=%d average=%.2f median=%d p95=%d\n", NR,sum/NR,values[p50],values[p95]}}'
```

Summaries expose generator and Judge retry booleans and call counts. Cache-hit
rate comes from calls whose `cached_content_token_count` is greater than zero.
Group successful totals by revision before and after an optimization so traffic
from different builds is never combined:

```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=<SERVICE_NAME> AND jsonPayload.event="llm_invocation_token_summary" AND jsonPayload.terminal_outcome="success"' \
  --project=<YOUR_PROJECT_ID> \
  --freshness=30d \
  --limit=100000 \
  --format='csv[no-heading](jsonPayload.deployment_revision,jsonPayload.total_token_count,jsonPayload.generator_retry_occurred,jsonPayload.judge_retry_occurred)' \
  | awk -F, '{count[$1]++; total[$1]+=$2; generator_retry[$1]+=(tolower($3)=="true"); judge_retry[$1]+=(tolower($4)=="true")} END {for (revision in count) printf "%s calls=%d average=%.2f generator_retries=%d judge_retries=%d\n", revision,count[revision],total[revision]/count[revision],generator_retry[revision],judge_retry[revision]}' \
  | sort
```

For a controlled concurrency rollout, inspect request failures and latency in
the same time window separately from application token events:

```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=<SERVICE_NAME> AND httpRequest.requestUrl:"/run_sse"' \
  --project=<YOUR_PROJECT_ID> \
  --freshness=1h \
  --limit=10000 \
  --order=asc \
  --format='table(timestamp,httpRequest.status,httpRequest.latency,resource.labels.revision_name)'
```

No log-based token metric, BigQuery dataset, prompt-response upload, or other
managed observability infrastructure is provisioned by this feature. Add those
only after a separate privacy, retention, and cost review.

#### Analyzing deterministic quiz validation

Each generated candidate produces one privacy-minimized structured event. The
payload contains only the outcome, generation attempt, aggregate issue count,
stable issue codes, service version, and deployed commit. It deliberately
excludes grade, subject, topic, prompts, questions, options, explanations,
client identifiers, and model responses.

Show recent validation outcomes:

```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=foxquiz AND jsonPayload.phase="deterministic_quiz_validation"' \
  --project=<YOUR_PROJECT_ID> \
  --freshness=7d \
  --limit=5000 \
  --order=desc \
  --format='table(timestamp,jsonPayload.event,jsonPayload.generation_attempt,jsonPayload.issue_count,jsonPayload.issue_codes,jsonPayload.deployment_revision)'
```

Count first-pass successes, initial failures, recovered retries, and exhausted
retries in the selected time window:

```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=foxquiz AND jsonPayload.phase="deterministic_quiz_validation"' \
  --project=<YOUR_PROJECT_ID> \
  --freshness=7d \
  --limit=100000 \
  --format='value(jsonPayload.event)' | sort | uniq -c | sort -nr
```

Rank deterministic failure categories. This command requires `jq`:

```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=foxquiz AND jsonPayload.phase="deterministic_quiz_validation" AND jsonPayload.issue_count>0' \
  --project=<YOUR_PROJECT_ID> \
  --freshness=7d \
  --limit=100000 \
  --format=json \
  | jq -r '.[].jsonPayload.issue_codes[]?' \
  | sort | uniq -c | sort -nr
```

No log-based validation metric is provisioned automatically. These queries are
sufficient for on-demand analysis and avoid adding Terraform or other managed
infrastructure. Add a manual counter metric later only if continuous dashboards
or alerts become necessary.

Find unsuccessful HTTP requests:
```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=foxquiz AND httpRequest.status>=400' \
  --project=<YOUR_PROJECT_ID> \
  --freshness=24h \
  --limit=500 \
  --order=desc \
  --format='table(timestamp,httpRequest.status,httpRequest.requestMethod,httpRequest.requestUrl)'
```

Count all FoxQuiz entries or only OpenTelemetry entries in a time window:
```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=foxquiz' \
  --project=<YOUR_PROJECT_ID> \
  --freshness=24h \
  --limit=100000 \
  --format='value(timestamp)' | wc -l

gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=foxquiz AND textPayload:opentelemetry' \
  --project=<YOUR_PROJECT_ID> \
  --freshness=24h \
  --limit=100000 \
  --format='value(timestamp)' | wc -l
```

The project `_Default` log bucket normally removes entries automatically after
its configured retention period. Do not delete an entire Cloud Run log merely to
remove repetitive exporter errors; correct the underlying permissions instead.

---

## 📥 Submitting a Pull Request (PR)

When you are ready to share your changes:
1. **Create a new branch** for your feature or bugfix:
   ```bash
   git checkout -b feature/my-amazing-feature
   ```
2. **Commit your changes** with a clear and descriptive commit message. We recommend using semantic messages, e.g., `feat(ui): add new dark mode toggle` or `fix(agent): handle empty topic edge case`.
3. **Push to your fork** and open a **Pull Request** on the main FoxQuiz repository.
4. A maintainer will review your code, run automated tests, and work with you to merge it!

---

## 💬 Community & Support
If you have any questions or want to discuss design decisions, feel free to open a thread in the GitHub Discussions page. We are excited to build the future of EdTech together with you!

*Happy Coding!*  
**The AUTOSOFT Engineering Team**
