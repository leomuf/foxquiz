# Coding Agent Guide

## Recurring FoxQuiz Maintenance

The main chat coordinates the assistant-independent roles defined in
[`agents/developer.md`](agents/developer.md),
[`agents/reviewer.md`](agents/reviewer.md), and
[`agents/release.md`](agents/release.md).
Follow [the shared workflow](agents/workflow.md) for task assignment,
handoffs, status reporting, and approval boundaries. The Developer hands off a
tested branch before PR creation. The Reviewer assesses deployment impact,
routes the PR to main or a release integration branch, and recommends the
version for releases. Batch dependency updates on one branch for evaluation.
The Release agent operates DEV and production. Merged runtime changes reach
users after the approved production redeployment.
Keep dependent phases sequential and isolate concurrent work in separate checkouts.
Use native subagents or separate sessions when available. Otherwise perform the
roles sequentially and disclose that same-session review has less independence.
Keep deployment credentials out of contributor work and review sessions.
Assistant-specific adapters, including `.codex/agents/`, load the shared roles.
The coordinator reports each agent's task, status, branch/PR, and next action.
Only ask for approval after preparing the concrete candidate and evidence.
Require explicit human approval for DEV deployment, PR merge, production
deployment, and separately for GitHub release publication. No agent may enable
auto-merge or treat another agent's recommendation as human authorization.
These roles run when assigned from chat; this configuration creates no schedule.

## Prerequisites

Install the CLI (one-time):
```bash
uv tool install google-agents-cli
```

---

## Development Phases

### Phase 1: Understand Requirements
Before writing any code, understand the project's requirements, constraints, and success criteria.

### Phase 2: Build and Implement
Implement agent logic in `app/`. Use `agents-cli playground` for interactive testing. Iterate based on user feedback.

### Phase 3: The Evaluation Loop (Main Iteration Phase)
Start with 1-2 eval cases, run `agents-cli eval generate`, then `agents-cli eval grade`, iterate by making changes and rerunning both commands until satisfied. Expect 5-10+ iterations. Once you have a baseline, reach for `agents-cli eval compare` (regression diffs), `agents-cli eval analyze` (cluster failure modes), and `agents-cli eval optimize` (auto-tune prompts). See [`tests/eval/datasets/README.md`](tests/eval/datasets/README.md) to select the correct FoxQuiz suite, isolate Firestore, and apply its release gate.

### Phase 4: Pre-Deployment Tests
Run `uv run pytest tests/unit tests/integration`. Fix issues until all tests pass.

### Phase 5: Deploy to Dev
For release candidates, the release agent previews the DEV plan with
`scripts/deploy.sh` and obtains explicit human approval before applying it.
Follow the [Final Pre-Release Gate](CONTRIBUTING.md#step-31-final-pre-release-gate).

### Phase 6: Production Deployment
The release agent previews the production plan for the verified merged commit
and obtains separate human approval before applying it with `scripts/deploy.sh`.
When setting up production infrastructure, ask the maintainer to choose a
single-project deployment or a full CI/CD pipeline with `agents-cli infra cicd`.

## Development Commands

| Command | Purpose |
|---------|---------|
| `agents-cli playground` | Interactive local testing |
| `uv run pytest tests/unit tests/integration` | Run unit and integration tests |
| `agents-cli eval dataset synthesize` | Synthesize multi-turn eval scenarios for your agent |
| `agents-cli eval generate` | Run agent on eval dataset, produce traces |
| `agents-cli eval grade` | Run agent evaluations on the traces |
| `agents-cli eval compare` | Compare two grade-results files (regression check) |
| `agents-cli eval analyze` | Cluster failure modes from grade results |
| `agents-cli eval metric list` | List built-in metrics available in the SDK |
| `agents-cli eval optimize` | Auto-tune agent prompts using eval data |
| `agents-cli lint` | Check code quality |
| `agents-cli infra single-project` | Set up project infrastructure (Terraform) |
| `agents-cli deploy` | Deploy to dev |
| `agents-cli scaffold enhance` | Add deployment target or CI/CD to project |
| `agents-cli scaffold upgrade` | Upgrade project to latest version |

---

## Operational Guidelines for Coding Agents

- **Code preservation**: Only modify code directly targeted by the user's request. Preserve all surrounding code, config values (e.g., `model`), comments, and formatting.
- **NEVER change the model** unless explicitly asked.
- **Model 404 errors**: Fix `GOOGLE_CLOUD_LOCATION` (e.g., `global` instead of `us-east1`), not the model name.
- **ADK tool imports**: Import the tool instance, not the module: `from google.adk.tools.load_web_page import load_web_page`
- **Run Python with `uv`**: `uv run python script.py`. Run `agents-cli install` first.
- **Stop on repeated errors**: If the same error appears 3+ times, fix the root cause instead of retrying.
- **Terraform conflicts** (Error 409): Use `terraform import` instead of retrying creation.

### Public Documentation Safety

- **Never add the real Google Cloud project ID or name, project number, or DEV
  Cloud Run URL to repository documentation.** This applies to plans, reports,
  READMEs, command examples, and other files intended for Git.
- Use `GCLOUD_PROJECT_ID`, `GCLOUD_PROJECT_NUMBER`, and
  `GCLOUD_RUN_DEV_URL` as the corresponding documentation placeholders.
- Use `<GCLOUD_PROJECT_NUMBER>-compute@developer.gserviceaccount.com` when a
  default Compute Engine service-account example is required.
- Runtime commands may obtain real values from the operator's environment, but
  those values must not be copied into versioned documentation.
