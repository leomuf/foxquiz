# Token Usage Observability — Implementation Plan

## Status

Implemented and verified in source; deployment and the controlled DEV baseline
remain pending. The implementation does not change the current token budget,
model configuration, quiz workflow, or release behavior.

## 1. Problem

FoxQuiz currently accumulates `usage_metadata.total_token_count` from direct
Gemini calls and writes one invocation total to the per-user and global
Firestore budgets. This protects the service from unbounded usage, but the
single total does not explain which workflow stage consumed the tokens.

One observed production session generated three quizzes and accumulated
117,958 tokens. The available data could confirm the invocation count and one
academic-Judge retry, but it could not separate:

- prompt tokens;
- generated response tokens;
- model thinking tokens;
- cached input tokens;
- security, curriculum, generator, and Judge usage;
- first-attempt and retry usage.

Without this breakdown, changing thinking budgets, prompts, retries, context
caching, or the daily user limit would be based on assumptions rather than
production evidence.

## 2. Goal

Add privacy-minimized, structured token telemetry for every direct Gemini call
and one aggregate event per FoxQuiz invocation. The data should make it
possible to answer:

- Which workflow stages use the most input, output, and thinking tokens?
- How much additional usage is caused by generator or Judge retries?
- Is Vertex AI implicit context caching producing cache hits?
- What is the typical and high-percentile token cost of one successful quiz?
- Does the current 150,000-token daily user limit permit the intended number of
  study sessions?
- Would prompt restructuring or explicit context caching be worthwhile?

The implementation must not log user prompts, quiz content, personal data,
security rules, or persistent client identifiers.

## 3. Non-Goals

The initial implementation will not:

- further change the 150,000-token per-user or 5,000,000-token global daily
  limits;
- change how cached tokens count toward those limits;
- enable explicit context caching;
- change thinking budgets, prompts, models, retries, or workflow routing;
- store token telemetry in Firestore;
- add Terraform or create log-based metrics;
- deploy FoxQuiz;
- log prompts, model responses, quiz questions, answer options, explanations,
  grade, subject, topic, language, IP addresses, or anonymous browser IDs.

Keeping measurement separate from optimization provides a reliable baseline
and makes later changes independently testable.

## 4. Token Metadata to Capture

Read the following values from each Gemini response's `usage_metadata`:

| Field | Meaning |
|---|---|
| `prompt_token_count` | Complete model input, including any cached input. |
| `cached_content_token_count` | Prompt tokens served from an implicit or explicit context cache. |
| `candidates_token_count` | Tokens in the model's returned candidates. |
| `thoughts_token_count` | Internal thinking tokens reported by the model. |
| `tool_use_prompt_token_count` | Tool-result tokens returned to the model, when applicable. |
| `total_token_count` | Total reported usage for the model call. |

Derive `uncached_prompt_token_count` as:

```text
max(prompt_token_count - cached_content_token_count, 0)
```

Treat missing or `None` metadata values as zero. Preserve the provider's
`total_token_count` instead of attempting to reconstruct it from fields that
might differ between SDK or model versions.

## 5. Workflow Stage Labels

Every direct Gemini call must supply a stable, allowlisted stage label to the
token-recording helper:

| Stage | Current purpose |
|---|---|
| `security_classifier` | Semantic SAFE, OFF_TOPIC, MALICIOUS, or PII classification. |
| `parameter_extractor` | Extraction from an unstructured conversational request. |
| `curriculum_evaluator` | Grade, subject, topic, and difficulty compatibility preflight. |
| `mascot_prompt` | Short conversational request for missing quiz parameters. |
| `quiz_generator` | Generation of a ten-question candidate quiz. |
| `academic_judge` | LLM-as-a-Judge review of a generated candidate. |

If another model call is added later, its stage must be added to the allowlist
and covered by a test. Do not accept arbitrary stage names originating from a
request or model response.

Where relevant, record bounded numeric attempt metadata:

- `generation_attempt` for `quiz_generator`;
- `judge_attempt` for `academic_judge`.

## 6. Structured Cloud Logging Events

Emit one `llm_token_usage` event after each successful model response. The
allowlisted payload should contain only:

```json
{
  "schema_version": 1,
  "event": "llm_token_usage",
  "phase": "model_usage",
  "call_stage": "quiz_generator",
  "model": "gemini-2.5-flash",
  "generation_attempt": 1,
  "prompt_token_count": 0,
  "uncached_prompt_token_count": 0,
  "cached_content_token_count": 0,
  "candidates_token_count": 0,
  "thoughts_token_count": 0,
  "tool_use_prompt_token_count": 0,
  "total_token_count": 0,
  "service_version": "1.1.0",
  "deployment_revision": "short-commit-or-revision"
}
```

Attempt fields should be omitted when they do not apply. Model and deployment
values must come from trusted application configuration, never request input.

At the end of the invocation, emit one `llm_invocation_token_summary` event
containing:

- the same schema, phase, version, and revision metadata;
- total model call count;
- total and per-category token counts;
- cache-hit model call count;
- generator call count;
- Judge call count;
- whether generator or Judge retries occurred;
- terminal outcome from the `success`, `needs_input`, `blocked`,
  `quality_failure`, and `error` allowlist. Only a validated quiz is
  `success`; a clarification response is `needs_input`.

Do not include an invocation, session, user, cookie, quiz, trace, or IP
identifier in the application payload. Cloud Run's normal platform metadata
remains sufficient for operational filtering without expanding FoxQuiz's
application-level data collection.

## 7. Internal Aggregation Design

Replace the current integer-only temporary state with a small typed usage
accumulator. It should retain only numeric counters and allowlisted stage
names. A possible shape is:

```python
{
    "totals": {"prompt": 0, "cached": 0, "candidate": 0, "thoughts": 0, "total": 0},
    "stages": {
        "quiz_generator": {"calls": 1, "total": 0},
        "academic_judge": {"calls": 1, "total": 0},
    },
}
```

Keep the existing exactly-once budget flush protection. The Firestore budget
must initially continue to receive the provider-reported total, so adding
observability cannot silently weaken the security and cost checkpoint.

ADK-managed usage events, if introduced later, must not be added a second time
when the same response was already recorded directly. Add an explicit source
or deduplication rule before supporting both paths for the same call.

## 8. Privacy and Security Requirements

- Build payloads from an explicit field allowlist.
- Store only integers, booleans, trusted enums, model name, and deployment
  metadata.
- Never serialize `response`, `contents`, prompt variables, callback state,
  complete exceptions, or `usage_metadata` wholesale.
- Never log the private semantic-security configuration.
- Never log disclosed PII, even when a security request is blocked.
- Do not add correlation identifiers that could reconstruct a learner's
  activity history.
- Ensure malformed provider metadata cannot inject additional log fields.
- Preserve the current best-effort behavior: telemetry failure must not replace
  a valid quiz with an error response.

## 9. Implementation Sequence

### 1. Introduce a typed token-usage value

- Define a small internal model or dataclass for normalized usage metadata.
- Add a pure conversion function from a Gemini response to that value.
- Clamp invalid negative values to zero and handle missing metadata.

### 2. Extend the recording helper

- Require an allowlisted `call_stage` argument.
- Accept optional bounded attempt numbers.
- Update the invocation accumulator.
- Emit the call-level structured event through the existing safe logging
  mechanism.
- Continue returning the provider-reported total for compatibility where
  useful.

### 3. Label every model call

- Update all current `record_token_usage` call sites.
- Confirm that every `generate_content` invocation is followed by exactly one
  recording call on success.
- Confirm that retries carry the correct attempt number.

### 4. Emit the invocation summary

- Extend the existing after-run and error-path flush.
- Emit one summary on success, expected security blocks, quality failures, and
  unexpected errors when usage exists.
- Preserve exactly-once Firestore budget updates and exactly-once summary
  logging.

### 5. Add maintainers' analysis commands

Document Cloud Logging queries in `CONTRIBUTING.md` for:

- token totals grouped by workflow stage;
- thinking-token share by stage;
- average token usage per successful invocation;
- generator and Judge retry overhead;
- implicit or explicit cache-hit rate;
- comparisons between deployment revisions.

Use aggregate queries or sanitized output. Do not instruct maintainers to dump
raw logs containing unrelated application entries.

## 10. Test Plan

All unit tests must run without Google credentials.

Add tests covering:

- complete usage metadata normalization;
- missing, partial, `None`, negative, and zero metadata;
- `uncached_prompt_token_count` derivation;
- independent accumulation for every stage;
- generator and Judge attempt counters;
- exactly one call-level event per recorded response;
- exactly one invocation summary and Firestore budget flush;
- successful quiz preservation when telemetry logging fails;
- error-path usage flushing;
- rejection of unknown stage labels;
- payload field allowlisting;
- absence of prompts, responses, quiz fields, PII, IP, browser ID, session ID,
  and invocation ID in serialized events;
- no double counting between direct responses and ADK session events.

A local, credential-dependent integration test may make one minimal Vertex AI
request and assert that the SDK exposes the expected metadata attributes. It
must not run in GitHub Actions and must not assert exact token numbers.

Run before considering the implementation complete:

```bash
uv run pytest tests/unit tests/integration
agents-cli lint
```

Behavioral output should remain unchanged, so existing evaluations are the
primary regression check. If prompt ordering is changed later for implicit
caching, run the complete relevant `agents-cli eval` set locally with Google
credentials.

## 11. Rollout and Verification

Deploy telemetry separately from any token optimization. Observe a meaningful
sample before changing budgets or model behavior; start with at least 50
successful quiz invocations when traffic permits.

Verify:

- every successful model call produces one call event;
- each invocation produces at most one summary;
- summary totals agree with the Firestore budget increment;
- no forbidden fields appear in a sample of production events;
- logs show the prompt, candidate, thinking, and cached-token distribution;
- telemetry errors do not affect user-visible quiz creation.

Record a baseline by workflow stage and deployment revision. Compare later
optimizations against the same measures rather than only the overall token
total.

## 12. Later Context-Caching Decision

Vertex AI implicit context caching is already enabled for Gemini 2.5 Flash.
After telemetry is available:

1. Measure the current `cached_content_token_count` and cache-hit call rate.
2. If hits are rare, move large stable instructions to the beginning of each
   eligible prompt and dynamic grade, subject, topic, quiz, and user data to the
   end.
3. Re-run behavioral evaluations to ensure prompt reordering does not weaken
   security, curriculum alignment, or quiz quality.
4. Measure the change in cached input, latency, total cost, and quality.
5. Consider explicit caching only when a reusable static block exceeds Vertex
   AI's minimum cache size and is reused often enough to offset cache creation
   and storage costs.

Never place user-specific prompts, previous quizzes, disclosed PII, or other
learner data in a shared explicit cache. A cache key must include the model
version and a hash or version of the static prompt contract so changed rules
cannot accidentally reuse stale cached instructions.

## 13. Budget Interpretation

Context caching reduces the price of cached input tokens, but cached tokens
remain part of provider-reported token counts. Therefore, adding or improving
caching must not be presented as automatically increasing the number of
quizzes allowed by FoxQuiz's current daily token limit.

After production measurement, make a separate product and security decision:

- retain the current total-token definition and adjust the numeric limit;
- introduce a cost-equivalent budget that discounts cached input;
- or keep separate safety-volume and monetary-cost budgets.

That decision requires observed distributions and should not be bundled into
the telemetry implementation.

## 14. Acceptance Criteria

Source implementation is complete when:

- every direct Gemini call is assigned one stable workflow stage;
- call and invocation events expose the complete numeric token breakdown;
- Firestore budget behavior remains backward compatible;
- unit tests prove exactly-once behavior and privacy allowlisting;
- credential-free CI tests pass;
- local Google-dependent verification confirms real SDK metadata handling;
- maintainers can compare stages and revisions without viewing prompt or quiz
  content.

Rollout verification is complete when:

- the DEV telemetry revision produces at least 50 successful quiz summaries
  using the versioned five-case pilot and 45-case rollout datasets;
- the pilot runs at concurrency 2, the remaining cases at concurrency 4, and
  an optional concurrency 8 experiment repeats the same pilot cases;
- token limits remain enabled and HTTP failures, timeouts, retries, latency,
  cache hits, token distributions, and privacy-safe payloads are inspected;
- the collected baseline supports an evidence-based decision about thinking
  budgets, retry policy, daily limits, and context caching.
