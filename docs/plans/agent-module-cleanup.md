# Agent Module and Public Contract Cleanup — Implementation Plan

## Status

Planned for the version after v1.4.0. The v1.4.0 release keeps the current
module boundaries while closing the share-integrity and payload-size findings.

## Problem

The FastAPI delivery layer imports the public `Quiz` schema from `app.agent`.
Importing that schema therefore also loads the ADK workflow and its supporting
infrastructure. At the same time, `app/agent.py` owns public models, prompt
construction, adaptive policy, provenance loading, generation, targeted
repair, academic review, output persistence, and workflow wiring. Its
file-level Ruff exemption prevents normal lint feedback across all of those
responsibilities.

This coupling makes endpoint tests harder to isolate, increases the impact of
changes, and obscures which functions implement domain rules versus workflow
or infrastructure concerns.

## Goals

- Give the public quiz contract a lightweight, framework-independent module.
- Let FastAPI and the ADK workflow depend on the same domain contract without
  importing each other.
- Split `app.agent` along existing responsibilities while preserving workflow
  behavior, prompts, model settings, repair budgets, and state keys.
- Remove the file-level Ruff exemption and apply focused suppressions only
  where an external API requires them.
- Preserve all public API, persistence, evaluation, and frontend contracts.

## Non-Goals

- Change the LLM model or generation parameters.
- Change adaptive difficulty, reinforcement reuse, retry limits, or Judge
  acceptance criteria.
- Redesign the frontend or Firestore collections.
- Combine the refactor with new product behavior.
- Deploy FoxQuiz or modify cloud infrastructure.

## Proposed Module Boundaries

### 1. Public quiz contracts

Create `app/domain/quiz.py` containing the bounded `Quiz`, `QuizQuestion`, and
shared metadata contracts. The module may depend on domain types such as
`DifficultyLevel`, but must not import FastAPI, ADK, Google clients, or
Firestore.

Update both `app/fast_api_app.py` and the workflow output boundary to import
these models directly. Keep request-specific envelopes such as
`ShareQuizRequest` in the delivery layer unless another delivery mechanism
also needs them.

### 2. Adaptive and provenance policy

Move deterministic decisions into small domain modules:

- expected difficulty and adaptive mode selection;
- validated-record context and fingerprint checks;
- question and option shuffling.

Keep Firestore reads in an application service. Pass loaded records into the
domain checks so policy remains credential-free and easy to test.

### 3. Generation and repair orchestration

Move generator and Judge prompt construction into dedicated modules. Extract
targeted repair planning, response normalization, and repair-history creation
into a repair service with explicit input and result types.

The extracted code must retain:

- independent deterministic and academic repair allowances;
- full deterministic validation after every repair;
- complete academic review after a targeted academic repair;
- fail-closed handling when parsing, generation, or repair fails;
- privacy-safe diagnostics without quiz content.

### 4. Workflow assembly

Leave `app/agent.py` as the composition root containing ADK nodes, their small
state transitions, and the `Workflow` edge declaration. Nodes should delegate
domain work and external operations instead of constructing prompts or
performing persistence directly.

Document state keys used across nodes. Prefer typed input/result objects for
newly extracted functions, while avoiding a simultaneous rewrite of all ADK
state handling.

### 5. Exception and lint cleanup

Replace broad exception handling with the expected exception types at schema,
Firestore, and model-response boundaries. Where a terminal workflow boundary
must catch unexpected failures to fail closed, retain the broad catch, log the
exception type with stack information, and explain the boundary in a comment.

Remove `# ruff: noqa` from `app/agent.py`. Resolve violations or add the
narrowest possible per-line rule with a reason.

## Implementation Sequence

1. Add characterization tests for public quiz serialization, workflow output,
   adaptive routing, targeted repair, and failure routes.
2. Extract the public quiz models and update imports without changing behavior.
3. Extract pure adaptive and provenance rules.
4. Extract prompt builders and repair orchestration one responsibility at a
   time.
5. Reduce `app.agent` to workflow composition and small node adapters.
6. Remove the file-level Ruff exemption and address the resulting findings.
7. Update architecture documentation and module ownership notes.

Each extraction should be reviewable independently. Avoid mixing prompt text
or model configuration changes into mechanical moves.

## Verification

- Run focused unit tests after each extraction.
- Run `uv run pytest tests/unit tests/integration` before the release PR.
- Run browser tests because quiz serialization feeds the frontend directly.
- Run `agents-cli lint` and the repository formatting checks.
- Compare the final pre-release eval suites against the v1.4.0 baseline if an
  extraction changes prompt construction or serialized model input, even when
  the intended prompt text is unchanged.

## Completion Criteria

- FastAPI can import and validate public quiz data without importing
  `app.agent`.
- `app.agent` contains workflow composition and thin node adapters rather than
  domain contracts, prompt bodies, and persistence implementations.
- No file-level Ruff exemption remains.
- Public quiz JSON, Firestore documents, retry budgets, model settings, and
  observed adaptive behavior remain compatible with v1.4.0.
- Unit, integration, browser, lint, and required evaluation checks pass.
