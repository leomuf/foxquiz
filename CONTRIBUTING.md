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

### 3. Running Quality Checks
Before submitting your changes, please ensure that all tests and code quality checks pass.

* **Run unit and integration tests:**
  ```bash
  uv run python -m pytest tests/unit tests/integration
  ```
* **Run the code linter:**
  ```bash
  agents-cli lint
  ```

### 4. Deploying & Infrastructure Optimization (For Maintainers)
Application deployment and infrastructure configuration are separate operations.
After deploying the container, complete the manual infrastructure steps below.
FoxQuiz intentionally does not use the optional Terraform infrastructure stack;
do not run `agents-cli infra single-project` for this project.

#### Step 1: Initialize Firestore (One-Time Project Prerequisite)

Create the Native Mode Firestore database once for every new Google Cloud
project:
```bash
gcloud firestore databases create \
  --project=<YOUR_PROJECT_ID> \
  --location=us-east1 \
  --type=firestore-native
```

The `us-east1` location keeps Firestore in the same region as FoxQuiz.
Skip this command when the `(default)` database already exists.

#### Step 2: Deploy the Application Container

```bash
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Refusing production deployment: commit or remove all workspace changes."
  exit 1
fi

COMMIT_SHA="$(git rev-parse HEAD)"
AGENT_VERSION="$(uv version --short)"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

agents-cli deploy \
  --no-confirm-project \
  --update-env-vars "COMMIT_SHA=${COMMIT_SHA},AGENT_VERSION=${AGENT_VERSION},BUILD_TIME=${BUILD_TIME}"
```

The clean-worktree check ensures the commit identifies every deployed source
change. The version, full commit SHA, and UTC build time are exposed at
`/version`, in the page footer, and in telemetry. The deploy uses the
project's default Compute service account.

#### Step 3: Configure Required Infrastructure Manually

Complete the following Cloud Run, OpenTelemetry, and Firestore configuration
topics after deployment.

##### Step 3.1: Cloud Run Cost and Startup Settings
```bash
gcloud run services update foxquiz \
  --project <YOUR_PROJECT_ID> \
  --region us-east1 \
  --min-instances 0 \
  --cpu-boost \
  --execution-environment gen1
```

- `--min-instances 0` scales to zero while idle.
- `--cpu-boost` allocates additional CPU during startup.
- `--execution-environment gen1` selects the lightweight Gen1 environment.

##### Step 3.2: OpenTelemetry Export Permissions

Retrieve the project number instead of hard-coding it:
```bash
gcloud projects describe <YOUR_PROJECT_ID> \
  --format='value(projectNumber)'
```

The default Compute service-account address is
`<PROJECT_NUMBER>-compute@developer.gserviceaccount.com`. Verify that Cloud
Run uses it:
```bash
gcloud run services describe foxquiz \
  --project=<YOUR_PROJECT_ID> \
  --region=us-east1 \
  --format='value(spec.template.spec.serviceAccountName)'
```

Grant the telemetry roles once per project:
```bash
gcloud projects add-iam-policy-binding <YOUR_PROJECT_ID> \
  --member='serviceAccount:<PROJECT_NUMBER>-compute@developer.gserviceaccount.com' \
  --role=roles/monitoring.metricWriter

gcloud projects add-iam-policy-binding <YOUR_PROJECT_ID> \
  --member='serviceAccount:<PROJECT_NUMBER>-compute@developer.gserviceaccount.com' \
  --role=roles/telemetry.tracesWriter

gcloud projects add-iam-policy-binding <YOUR_PROJECT_ID> \
  --member='serviceAccount:<PROJECT_NUMBER>-compute@developer.gserviceaccount.com' \
  --role=roles/serviceusage.serviceUsageConsumer
```

Without these roles, OpenTelemetry exporters repeatedly log HTTP
`403 Forbidden` errors. Repeat these grants only if the project or runtime
service account changes.

##### Step 3.3: Firestore Time To Live (TTL) Policies
```bash
gcloud firestore fields ttls update expires_at \
  --collection-group=budgets \
  --database='(default)' \
  --enable-ttl \
  --project=<YOUR_PROJECT_ID>

gcloud firestore fields ttls update expires_at \
  --collection-group=quizzes \
  --database='(default)' \
  --enable-ttl \
  --project=<YOUR_PROJECT_ID>
```

The application sets `expires_at` to seven days for transient budgets and
30 days for shared quizzes. Firestore TTL performs the eventual physical
deletion.

#### Step 4: Ensure Public Accessibility

Public invocation is a post-deployment command:
```bash
gcloud run services add-iam-policy-binding foxquiz \
  --member='allUsers' \
  --role='roles/run.invoker' \
  --project <YOUR_PROJECT_ID> \
  --region us-east1
```

#### Step 5: Configure a Custom Domain (Optional)

```bash
gcloud beta run domain-mappings create \
  --service=foxquiz \
  --domain=www.foxquiz.app \
  --project=<YOUR_PROJECT_ID> \
  --region=us-east1
```

Configure a CNAME record at the domain registrar that points `www` to
`ghs.googlehosted.com.`.

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
