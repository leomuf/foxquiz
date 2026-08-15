# agents-cli Scaffold Upgrade Plan

## Status

Planned. Do not run the upgrade as part of the DEV deployment documentation
change.

## Current State

- project manifest scaffold version: `0.5.0`;
- locally installed `agents-cli` version observed during planning: `1.3.1`;
- project deployment target: Cloud Run;
- project region: `us-east1`;
- session type: in-memory;
- CI/CD runner: skipped;
- FoxQuiz contains substantial custom application, frontend, security,
  persistence, licensing, testing, and deployment documentation changes.

`agents-cli info` currently reports a version mismatch and recommends
`agents-cli scaffold upgrade`. The upgrade uses a three-way merge between the
old template, the new template, and the customized repository.

## Goal

Upgrade the generated project scaffold to the installed `agents-cli` template
version while preserving every FoxQuiz-specific decision and exposing all
conflicts for human review.

The upgrade must not:

- deploy FoxQuiz;
- change the Gemini model;
- expose private security configuration;
- reintroduce the removed Terraform deployment stack;
- replace the custom frontend or brand assets;
- overwrite the ADK security plugin and workflow routing;
- change the production Cloud Run service name, region, or access policy;
- change Firestore retention or collection semantics silently;
- rewrite project licensing boundaries without review.

## Phase 1: Use a Dedicated Upgrade Branch

Start from an up-to-date and clean `main` branch after all intended release
changes have been merged:

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git status --short
git switch -c codex/upgrade-agents-cli-scaffold
```

Do not begin when the working tree contains staged, modified, or untracked
project changes that belong to another task.

## Phase 2: Record the Baseline

Capture the tool and project state before previewing the upgrade:

```bash
agents-cli info
agents-cli scaffold upgrade --help
git log -1 --oneline
git status --short
```

Confirm the installed tool version intentionally. If a newer CLI is desired,
upgrade the tool first and repeat `agents-cli info`:

```bash
uv tool upgrade google-agents-cli
agents-cli info
```

Do not combine an unreviewed CLI tool update and project scaffold update by
accident. Record the exact installed version in the pull request.

Run and record the pre-upgrade quality baseline:

```bash
uv sync --locked
uv run pytest tests/unit tests/integration tests/browser -m "not google_cloud"
agents-cli lint
```

Run Google-dependent integration or behavioral evaluations locally when they
are part of the selected release gate; credentials must not be added to GitHub.

## Phase 3: Preview the Three-Way Merge

Run the non-mutating preview first:

```bash
agents-cli scaffold upgrade --dry-run
```

Review the complete output before applying anything. Identify:

- files that will be added;
- unmodified template files that will be replaced;
- locally customized files that will be preserved;
- conflicts requiring manual resolution;
- manifest version changes;
- dependency or lockfile changes;
- generated deployment or CI/CD files.

Stop and investigate if the preview proposes deleting application code,
frontend assets, tests, documentation, licenses, or private-configuration
boundaries.

## Phase 4: Apply Interactively

Preferred command:

```bash
agents-cli scaffold upgrade --interactive
```

Use `--auto-approve` only when the dry-run is small, fully understood, and has
no conflicts:

```bash
agents-cli scaffold upgrade --auto-approve
```

Interactive review is preferred for FoxQuiz because the repository has moved
far beyond the original scaffold.

## Phase 5: Inspect Every Change

Immediately after the command finishes:

```bash
git status --short
git diff --stat
git diff --check
git diff
```

Review the following preservation checklist explicitly.

### Application and ADK

- Gemini model names and locations are unchanged.
- `App` and security plugin callback registration remain active.
- workflow nodes, edges, retry behavior, deterministic validation, and
  LLM-as-a-judge routing remain intact.
- request context, localized errors, budget accounting, and fail-closed
  behavior remain intact.

### Persistence and Security

- Firestore collections and Time To Live (TTL) fields remain compatible.
- shared quizzes, feedback, quality failures, security events, bans, and token
  budgets retain their existing semantics.
- private security values are not introduced into versioned files.
- operational logs remain privacy-minimized.

### Frontend and Build Identity

- custom HTML, translations, mascots, icons, manifests, and social previews
  are preserved.
- `COMMIT_SHA`, `AGENT_VERSION`, and `BUILD_TIME` continue to reach the
  application footer, `/version`, logs, and telemetry.
- the pinned `uv` Docker version is changed only when the upgrade explicitly
  requires and justifies it.

### Deployment

- Cloud Run remains the deployment target.
- project name `foxquiz` and region `us-east1` are preserved.
- manual production settings and public IAM post-configuration remain
  documented.
- the new `foxquiz-dev` workflow remains compatible with `--service-name`.
- removed Terraform files are not silently reintroduced.
- no deployment command has executed.

### CI, Tests, and Licensing

- CI continues to use locked dependency synchronization.
- credential-free and Google-dependent test boundaries remain explicit.
- Apache-2.0, CC BY 4.0, and CC0-1.0 boundaries remain accurate.
- project-created files do not receive inappropriate third-party copyright
  headers.

Resolve conflicts surgically. Do not accept an entire generated file when only
a small template change is needed.

## Phase 6: Synchronize Dependencies Deliberately

If `pyproject.toml` changed, inspect the dependency changes before updating the
lockfile:

```bash
git diff -- pyproject.toml uv.lock
uv lock
uv sync --locked
```

If `pyproject.toml` did not change, prefer:

```bash
uv sync --locked
```

Do not treat unrelated dependency upgrades as an automatic part of the
scaffold update. Move them to a separate commit or pull request when they are
not required by the new scaffold.

## Phase 7: Verify the Upgraded Project

Run the complete credential-free suite:

```bash
uv run pytest tests/unit tests/integration tests/browser -m "not google_cloud"
```

Run project linting:

```bash
agents-cli lint
```

Verify both deployment targets without deploying:

```bash
agents-cli deploy \
  --project quiz-buddy-501017 \
  --region us-east1 \
  --service-name foxquiz \
  --no-confirm-project \
  --dry-run

agents-cli deploy \
  --project quiz-buddy-501017 \
  --region us-east1 \
  --service-name foxquiz-dev \
  --min-instances 0 \
  --max-instances 2 \
  --no-confirm-project \
  --dry-run
```

Inspect both rendered commands for service name, region, sizing, authentication
mode, environment variables, source directory, and build metadata behavior.

If the scaffold changed ADK behavior, run the relevant local Google-dependent
integration tests and `agents-cli eval` workflow before considering the upgrade
complete.

## Phase 8: Review and Commit

Keep the scaffold upgrade isolated in a dedicated commit. A suggested commit
shape is:

```text
chore(scaffold): upgrade project to agents-cli <VERSION>

- apply reviewed scaffold updates
- preserve FoxQuiz deployment and security customizations
- verify production and DEV deployment dry-runs
```

The pull request must include:

- old and new scaffold versions;
- installed CLI version;
- files generated, changed, preserved, or intentionally rejected;
- conflict resolutions;
- dependency changes;
- test, lint, eval, and dry-run results;
- confirmation that no deployment occurred.

Do not merge until the full diff has received human review.

## Recovery Strategy

If the preview or applied upgrade is unsafe:

1. stop before staging or committing;
2. preserve any diagnostic output needed to report the issue;
3. inspect exactly which generated changes are unsuitable;
4. manually revert only the upgrade-generated changes or abandon the dedicated
   upgrade branch after confirming it contains no unrelated work;
5. report the incompatibility instead of repeatedly rerunning the same command.

Do not use destructive repository-wide reset commands when unrelated work is
present.

## Completion Criteria

- `agents-cli info` no longer reports the old scaffold mismatch.
- the manifest records the intended new scaffold version.
- all FoxQuiz-specific code and documentation decisions are preserved.
- credential-free tests and `agents-cli lint` pass.
- required local Google-dependent checks pass.
- production and DEV `agents-cli deploy --dry-run` commands are correct.
- no deployment or cloud infrastructure mutation occurred during the upgrade.
- the upgrade is reviewable as an isolated pull request.
