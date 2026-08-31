# Token Usage Observability Baseline Report

## Report metadata

| Field | Value |
|---|---|
| Report version | `1.0.0` |
| Report status | Final baseline |
| Evaluation date | 2026-08-17 |
| Evidence window | 2026-08-17 15:42:09–16:07:03 UTC |
| Environment | Public DEV Cloud Run service |
| Service URL | `https://<GCLOUD_RUN_DEV_URL>.run.app` |
| Service name | `foxquiz-dev` |
| Cloud Run revision | `<GCLOUD_RUN_DEV_REVISION_NAME>` |
| FoxQuiz version | `1.1.0-dev` |
| Source commit | `f7ed10125b831428f3bea0e444ea11361e55540d` |
| `agents-cli` version | `1.3.1` |
| Evaluation app name | `app` |
| Firestore database | `foxquiz-dev` |

This report is scoped exclusively to requests sent to the deployed DEV service.
Local evaluation results are deliberately excluded so that latency, failures,
token usage, and outcomes remain comparable with the earlier deployed tests.

## Executive summary

The deployed asynchronous Gemini-call revision removed the event-loop blockage
that had made session creation time out under concurrent quiz generation.

- All 50 requests in the planned pilot-plus-rollout cohort completed over HTTP.
- All 50 session-creation requests and all 50 streaming runs returned HTTP 200.
- Session creation had a 5 ms median, 8 ms p95, and 217 ms maximum latency.
- The earlier synchronous revision had produced 19–49 second session-creation
  latency under load and 30-second CLI timeouts for rollout cases 12, 17, and
  22. All three cases completed on the new deployed revision.
- The planned 50-invocation cohort produced 49 successful quizzes and one
  deterministic quality failure.
- Two controlled follow-up invocations brought the final authoritative count
  to 50 successful quizzes across 52 invocations. Both quality failures were
  retained in the evidence.
- The original 50-invocation cohort consumed 793,123 provider-reported tokens.
- No model call reported a context-cache hit.

The concurrency fix is therefore validated. Token usage remains high, and the
repeatable Mendelian-inheritance quality failure and `/app-info` compatibility
warning remain separate follow-up items.

## Scope and configuration

The tested revision was deployed from a clean detached worktree.

The deployed service retained the intended DEV configuration:

| Setting | Value |
|---|---:|
| Execution environment | Gen1 |
| Minimum instances | 0 |
| Maximum instances | 2 |
| Container concurrency | 8 |
| CPU | 1 |
| Memory | 1 GiB |
| Startup CPU boost | Enabled |
| A2A | Disabled (`ENABLE_A2A=FALSE`) |
| Access during testing | Public (`allUsers` has `roles/run.invoker`) |

`agents-cli deploy` preserved the Gen1, scaling, compute, startup-boost, and
service-account settings. It intentionally made the service private during
deployment, so the existing public DEV tester access was restored afterward
with a Cloud Run IAM binding. No `gcloud run services update` revision was
required.

Token and daily limits remained enabled. The controlled run was comfortably
below the 5,000,000-token global daily limit, and each evaluation case used an
independent session. Keeping the production-like limits enabled avoided
introducing another configuration difference into the baseline.

## Method

### Cohorts

| Cohort | Cases | Concurrency | Purpose |
|---|---:|---:|---|
| Pilot | 5 | 4 | Validate the async fix and check errors, retries, latency, and summaries |
| Rollout | 45 | 4 | Complete the planned controlled cohort |
| Mendelian retry | 1 | 1 | Retry the only failed scenario without hiding its original failure |
| Successful top-up | 1 | 1 | Reach at least 50 successful quiz summaries |

The first two rows form the original 50-invocation baseline. The two follow-up
rows are reported separately because adding them to the original cohort would
change its token and outcome distribution.

### Primary commands

Deployment:

```bash
agents-cli deploy \
  --project <GCLOUD_PROJECT_ID> \
  --region us-east1 \
  --service-name foxquiz-dev \
  --service-account <GCLOUD_PROJECT_NUMBER>-compute@developer.gserviceaccount.com \
  --cpu 1 \
  --memory 1Gi \
  --concurrency 8 \
  --min-instances 0 \
  --max-instances 2 \
  --no-confirm-project \
  --update-env-vars COMMIT_SHA=f7ed10125b831428f3bea0e444ea11361e55540d,AGENT_VERSION=1.1.0-dev,BUILD_TIME=2026-08-17T15:37:27Z,FIRESTORE_DATABASE_ID=foxquiz-dev,ENABLE_A2A=FALSE

gcloud run services add-iam-policy-binding foxquiz-dev \
  --project <GCLOUD_PROJECT_ID> \
  --region us-east1 \
  --member allUsers \
  --role roles/run.invoker
```

Pilot and rollout generation:

```bash
agents-cli eval generate \
  --url https://<GCLOUD_RUN_DEV_URL>.run.app \
  --app-name app \
  --dataset tests/eval/datasets/token-observability-pilot.json \
  --output artifacts/traces/token-observability/f7ed101/pilot-c4-deployed.json \
  --concurrency 4

agents-cli eval generate \
  --url https://<GCLOUD_RUN_DEV_URL>.run.app \
  --app-name app \
  --dataset tests/eval/datasets/token-observability-rollout.json \
  --output artifacts/traces/token-observability/f7ed101/rollout-c4-deployed.json \
  --concurrency 4
```

Both trace files were graded with:

```bash
agents-cli eval grade \
  --traces <DEPLOYED_TRACE_FILE> \
  --config tests/eval/token_observability_eval_config.yaml \
  --output <REVISION_SPECIFIC_GRADE_DIRECTORY>
```

The follow-up cases used the same deployed URL and grading configuration, with
`--concurrency 1`.

### Evidence sources

- `agents-cli eval generate` traces from the deployed URL;
- `agents-cli eval grade` results using `quiz_request_fulfillment`;
- Cloud Run HTTP request logs for status and latency;
- privacy-minimized `llm_token_usage` call events;
- authoritative `llm_invocation_token_summary` outcome events;
- allowlisted deterministic-validation issue codes.

The application telemetry did not contain prompts, quiz content, learner data,
IP addresses, session IDs, or persistent client identifiers.

## Reliability and concurrency results

### Original 50-invocation cohort

| Endpoint | Requests | HTTP 200 | Median | p95 | Maximum |
|---|---:|---:|---:|---:|---:|
| Session creation | 50 | 50 | 5 ms | 8 ms | 217 ms |
| `/run_sse` | 50 | 50 | 54.362 s | 88.252 s | 144.098 s |

There were no 429 responses, 5xx responses, CLI generation failures, session
timeouts, or stream timeouts. A streaming request could take more than 120
seconds in total while remaining healthy because streamed events prevented the
per-read inactivity timeout from expiring.

The CLI made two `/apps/app/app-info` discovery requests, both of which returned
HTTP 400. These were metadata-discovery warnings rather than quiz invocation
failures. The generated traces omitted `agent_data.agents`, which can degrade
metrics that require agent metadata.

### Comparison with the synchronous revision

| Observation | Synchronous revision `4460017` | Async revision `f7ed101` |
|---|---:|---:|
| Session creation while streams were active | 19–49 s | median 5 ms, p95 8 ms |
| CLI session timeout threshold | 30 s | 30 s |
| Known failed rollout case indices | 12, 17, 22 | none |
| Completed planned rollout | No; cancelled after repeated session failures | Yes; 45/45 |

This is direct evidence that replacing synchronous Gemini calls in async
request paths restored event-loop responsiveness. It does not prove that every
long-running quiz request is faster; it proves that one request no longer
blocks unrelated session creation and concurrent progress.

## Invocation outcomes

### Planned 50-invocation baseline

| Outcome | Count | Rate |
|---|---:|---:|
| `success` | 49 | 98% |
| `quality_failure` | 1 | 2% |
| Total | 50 | 100% |

### Successful-summary target

The failed Mendelian-inheritance pilot case was retried once. It failed again,
and the failure was retained rather than reclassified. A separate, previously
reliable linear-equations case supplied the successful top-up.

| Outcome after follow-ups | Count |
|---|---:|
| `success` | 50 |
| `quality_failure` | 2 |
| Total deployed invocations | 52 |

Both Mendelian failures exhausted two generator attempts. Each emitted three
`duplicate_option` validation findings on the second attempt. This repeatability
makes the scenario a useful regression case rather than noise to discard.

## Token baseline

The following figures cover only the original 50-invocation cohort.

### Aggregate usage

| Metric | Value |
|---|---:|
| Provider-reported total tokens | 793,123 |
| Model calls | 209 |
| Prompt tokens | 292,229 |
| Candidate tokens | 128,075 |
| Thinking tokens | 372,819 |
| Thinking share | 47.01% |
| Cache-hit model calls | 0 |
| Retry calls | 10 |
| Retry tokens | 65,567 |
| Retry share of all tokens | 8.27% |

### Usage by workflow stage

| Stage | Calls | Prompt | Candidate | Thinking | Total | Share |
|---|---:|---:|---:|---:|---:|---:|
| `quiz_generator` | 56 | 87,429 | 100,229 | 146,218 | 333,876 | 42.10% |
| `academic_judge` | 48 | 147,886 | 11,537 | 160,392 | 319,815 | 40.32% |
| `curriculum_evaluator` | 50 | 36,848 | 15,809 | 55,756 | 108,413 | 13.67% |
| `security_classifier` | 50 | 18,227 | 50 | 10,074 | 28,351 | 3.57% |
| `parameter_extractor` | 5 | 1,839 | 450 | 379 | 2,668 | 0.34% |

The generator and academic Judge together consumed 82.42% of all tokens. The
absence of cache hits means this run provides no evidence that implicit context
caching reduced cost.

### Successful invocation distribution

The original cohort contained 49 successful invocation summaries.

| Statistic | Total tokens |
|---|---:|
| Average | 15,883.49 |
| Minimum | 9,281 |
| Median | 14,295 |
| p95 | 24,408 |
| Maximum | 39,797 |

After the successful top-up, the 50-success distribution had an average of
15,856.92 tokens; its minimum, median, p95, and maximum were unchanged.

### Retry overhead

| Stage | Retry calls | Retry tokens |
|---|---:|---:|
| `quiz_generator` | 6 | 41,066 |
| `academic_judge` | 4 | 24,501 |
| Total | 10 | 65,567 |

Six invocations had at least one retry. Some had both a generator and Judge
retry, so invocation and retry-call counts are intentionally different.

## Evaluation grading

| Trace set | Total | Valid judge results | Judge errors | Valid-score summary |
|---|---:|---:|---:|---|
| Pilot | 5 | 4 | 1 | Three `5.0`, one `1.0`; mean `4.0` |
| Rollout | 45 | 44 | 1 | All 44 scored `5.0`; mean `5.0` |
| Mendelian retry | 1 | 1 | 0 | `1.0` |
| Successful top-up | 1 | 1 | 0 | `5.0` |

Two LLM-as-judge responses violated the metric's required JSON-only response
format. In both cases the returned prose explicitly evaluated the generated
quiz as `5/5`, but the CLI correctly recorded a judge error instead of silently
coercing that prose into a valid score. These are evaluation-format errors, not
agent invocation failures.

Across all four trace sets there were 50 valid judge results: 48 scores of
`5.0` and two scores of `1.0`. The two `1.0` scores correspond to the retained
Mendelian quality failures.

## Same-pilot token comparison

The earlier deployed five-case pilot on revision `4460017` used 85,391 tokens.
The same five input cases on revision `f7ed101` used 70,118 tokens. The 15,273
token difference must not be interpreted as an async optimization effect:
model output and thinking are nondeterministic, the old pilot used concurrency
2, and the new pilot used concurrency 4. The async change did not intentionally
alter models, prompts, thinking budgets, retry rules, or workflow routing.

## Conclusions

1. The async Gemini-call change resolves the deployment's concurrency blocker.
2. Cloud Run can serve at least four concurrent quiz generations while keeping
   session creation responsive on the tested Gen1, 1-CPU configuration.
3. The deployed observability implementation accounts for all model calls and
   produces one authoritative terminal summary per invocation.
4. Generator and Judge calls dominate token usage and are the best candidates
   for future optimization experiments.
5. Thinking tokens represent nearly half of total usage and warrant controlled
   experiments before changing any thinking configuration.
6. There is no evidence of context-cache benefit in this cohort.
7. Retry behavior adds material cost and should be analyzed without weakening
   deterministic validation or academic quality gates.

## Follow-up work

1. Diagnose the repeatable Portuguese Mendelian `duplicate_option` failure and
   add or retain it as a regression case.
2. Fix `/apps/app/app-info` returning HTTP 400 so generated traces include full
   agent metadata.
3. Make the custom LLM judge more resistant to prose-wrapped JSON, or use a
   deterministic structural metric alongside it.
4. Use this report as the pre-optimization baseline for controlled experiments
   on generator/Judge prompts, retries, thinking budgets, or explicit caching.
5. Repeat a smaller deployed cohort at concurrency 8 if a separate capacity
   experiment is desired; do not merge those results into this baseline.

## Artifact locations

The generated artifacts are intentionally ignored by Git because they can be
large and are reproducible. Paths are relative to the repository root.

| Artifact | Path |
|---|---|
| Pilot traces | `artifacts/traces/token-observability/f7ed101/pilot-c4-deployed.json` |
| Rollout traces | `artifacts/traces/token-observability/f7ed101/rollout-c4-deployed.json` |
| Mendelian retry traces | `artifacts/traces/token-observability/f7ed101/pilot-retry-mendelian-deployed.json` |
| Top-up traces | `artifacts/traces/token-observability/f7ed101/success-count-topup-deployed.json` |
| Pilot grade JSON | `artifacts/grade_results/token-observability/f7ed101/pilot-c4-deployed/results_20260817_174515.json` |
| Rollout grade JSON | `artifacts/grade_results/token-observability/f7ed101/rollout-c4-deployed/results_20260817_180118.json` |
| Mendelian retry grade JSON | `artifacts/grade_results/token-observability/f7ed101/pilot-retry-mendelian-deployed/results_20260817_180531.json` |
| Top-up grade JSON | `artifacts/grade_results/token-observability/f7ed101/success-count-topup-deployed/results_20260817_180736.json` |

Tracked inputs and configuration:

- `tests/eval/datasets/token-observability-pilot.json`
- `tests/eval/datasets/token-observability-rollout.json`
- `tests/eval/datasets/token-observability-regression.json`
- `tests/eval/token_observability_eval_config.yaml`

## Report versioning policy

This report is an immutable baseline for application commit `f7ed101`.

- Patch version: factual or formatting correction that does not change the
  evidence or conclusions.
- Minor version: additional analysis of the same deployed revision and cohort.
- Major version: a new deployment revision, changed agent behavior, changed
  model configuration, changed observability schema, or a new baseline cohort.

Create a new versioned report file for later baselines instead of overwriting
this file. This preserves an auditable comparison history.
