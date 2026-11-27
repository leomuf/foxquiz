# FoxQuiz maintenance agents

The main chat coordinates three assistant-independent roles. The shared Markdown
files contain their responsibilities, quality criteria, and handoffs:

| Role | Definition | Responsibility |
| --- | --- | --- |
| Developer | [developer.md](../../agents/developer.md) | Implement features and dependency fixes that need code or constraint changes. Open PRs against `leomuf/foxquiz:main` from a writable branch. |
| Reviewer | [reviewer.md](../../agents/reviewer.md) | Review features and existing Dependabot PRs, define tests, assess evidence, and recommend a merge or changes. |
| Release | [release.md](../../agents/release.md) | Operate approved DEV and production deployments, run the release pilot, and draft release notes. |

An ordinary PR ends at human merge. For example, send a Dependabot PR that
only changes `uv.lock` to the reviewer first. A release candidate goes through
the full pre-release gate, with the release agent returning DEV evidence to
the reviewer before the coordinator requests a merge decision.

Read [AGENTS.md](../../AGENTS.md) and the [shared workflow](../../agents/workflow.md)
for coordination, candidate tracking, status reporting, and approval boundaries.
Update these shared files when changing behavior so every assistant receives the
same instructions.

## Using any coding assistant

Load `AGENTS.md`, `agents/workflow.md`, and the assigned role file from a
maintainer-trusted revision as context. Review proposed edits to those instructions
like other PR changes.
Use native delegation if supported, separate assistant sessions when possible,
or explicitly identified sequential phases in one session. A same-session review
has less independence. See the shared workflow for an invocation example and
handling unavailable tools.

Contributors choose models, providers, and reasoning settings in their own
assistant configuration. They can use the Markdown files directly without
installing a Codex adapter. These role settings do not change FoxQuiz's application
model. Use authenticated GitHub integrations or CLIs available in the environment.
Report unavailable permissions or capabilities and provide a manual handoff.

## Codex adapter

The three TOML files in `.codex/agents/` contain role names, discovery descriptions,
model settings, and instructions to load the shared Markdown. They deliberately
keep the substantive workflow in `agents/`.

| Codex role | Pinned model | Reasoning effort |
| --- | --- | --- |
| `foxquiz_developer` | `gpt-6.2-sol` | `high` |
| `foxquiz_reviewer` | `gpt-6.2-astra` | `xhigh` |
| `foxquiz_release` | `gpt-6.2-sol` | `high` |

`xhigh` represents the requested very high effort. Model IDs require an explicit
update when newer versions become available. If a configured model is unavailable,
report it instead of silently substituting. Open a new Codex session in the
repository to load these definitions. Project trust and environment permissions
still apply. See the [official custom-agent documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents#custom-agents).

## Adding another assistant adapter

Configure the assistant's supported role or instruction mechanism to read the
repository-root `AGENTS.md`, `agents/workflow.md`, and one role file. Keep only
assistant-specific discovery, model, and reasoning settings in the adapter.
Resolve paths from the repository root and report missing instruction files
before executing the role. If native adapters are unavailable, supply the same
files directly as context. Do not duplicate the shared instructions.

These definitions create no schedules or background jobs. Human approval remains
required for DEV deployment, PR merge, production deployment, and separately for
release publication. GitHub branch protection and deployment permissions provide
enforcement beyond the instructions themselves.
