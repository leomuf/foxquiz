# Evaluation Datasets

This directory contains evaluation datasets for testing agent behavior.

## Running Evaluations

The standalone `quiz_structure` metric uses the same `emoji` package as the
application. When installing the CLI as an isolated uv tool, include this
dependency in its environment:

```bash
uv tool install --with 'emoji>=2.15.0,<3.0.0' google-agents-cli
```

### Which evaluation should I run?

Start with one or two cases from the relevant suite while iterating. Regenerate
and regrade after every behavioral change because grading old traces does not
exercise the new code. Before release, run a complete suite only when its
covered behavior changed after the latest passing full result. Documentation,
unit-test isolation, CI configuration, and deployment-script-only changes do
not invalidate an existing behavioral evaluation.

`quiz-answer-normalization-targeted-repair.json` is currently the only
repetition-based statistical suite. Run its complete 20 cases once against the
final behavioral candidate when answer normalization, option shuffling,
validation, Judge routing, repair, quiz publication, related prompts, model
configuration, or runtime dependencies changed. Do not repeat all 20 cases for
every subsequent release-PR update.

| Change or objective | Dataset and config | Evaluation cases | Execution boundary |
|---|---|---:|---|
| Quick smoke check after a general agent change | `basic-dataset.json` with `eval_config.yaml` | 2 | Local, mocked persistence |
| Wikipedia grounding, search selection, or topic-alignment changes | `grounding-relevance-dataset.json` with `grounding_eval_config.yaml` | 2 | Local, mocked persistence |
| Adaptive scoring, difficulty selection, or Hard-mode generation | `adaptive-hard-difficulty-dataset.json` with `adaptive_hard_eval_config.yaml` | 2 | Local, mocked persistence |
| Grade 1–4 generation prompts, schemas, validators, or pedagogy | `grades-1-to-4.json` with `grades_1_to_4_eval_config.yaml` | 8 | Local, mocked persistence |
| Primary-grade curriculum rejection or clarification | `grades-1-to-4-routing.json` with `grades_1_to_4_routing_eval_config.yaml` | 2 | Local, mocked persistence |
| Curriculum preflight, mascot guidance, or language-localization changes | `multilingual-curriculum-incompatible.json` with `multilingual_routing_eval_config.yaml` | 3 | Local, mocked persistence |
| Answer normalization, option shuffling, validation, Judge routing, repair, or public quiz output | `quiz-answer-normalization-targeted-repair.json` with its matching config | 20 | Local, mocked persistence; 20-case statistical release gate when affected |
| Broad prompt, model, workflow, adaptive, or token-instrumentation regression | `token-observability-regression.json` with `token_observability_eval_config.yaml` | 10 | Local, mocked persistence |
| Final smoke gate for the frozen production candidate | `token-observability-pilot.json` with `token_observability_eval_config.yaml` | 5 | Temporary DEV deployment; required before production |
| Establish a new telemetry, budget, latency, scaling, or concurrency baseline | `token-observability-rollout.json` after a healthy pilot | 45 | Temporary DEV deployment; conditional measurement campaign |
| Request parsing, security routing, HTTP envelopes, or token-call elimination | Structured-request safe suite, then the malicious case last | 5 safe + 1 malicious | Temporary DEV deployment only |

The five-case pilot is the deployed smoke gate for a frozen production
candidate. The 45-case rollout is a measurement campaign and is not required
for a routine release unless telemetry, budgets, latency, scaling, concurrency,
or their baseline changed. Do not run every local dataset for every code
change: use the table to select the behavioral boundary that changed, plus the
basic smoke suite for broad changes.

### Protect Firestore during local evaluations

Local behavioral evaluations call live Vertex AI models, but they must use
in-memory persistence and local logging. Export mock mode in the shell that
runs `agents-cli`; the generated local server inherits it:

```bash
export INTEGRATION_TEST=TRUE
```

Without this variable, a developer with Application Default Credentials can
write budgets, validated-quiz provenance, and quality diagnostics to the
Firestore database selected by `FIRESTORE_DATABASE_ID`, which defaults to
`(default)`. Do not use a real database merely to run a behavioral evaluation.
When Firestore itself is explicitly under test, use a separately approved
integration test against `foxquiz-dev`, never `(default)`, and unset
`INTEGRATION_TEST` for that test only.

The `--url` campaigns below execute persistence in the deployed service. The
local `INTEGRATION_TEST` value does not alter that remote service; verify that
the DEV revision reports `FIRESTORE_DATABASE_ID=foxquiz-dev` before starting.

### Result policy

Deterministic metrics such as `quiz_structure` and
`structured_request_outcome` score either 0 or 1; every applicable case must
score 1. LLM-judge metrics score from 1 to 5. A score below 5 requires review
before release, and a score of 3 or below is a failed case. A suite-specific
gate below takes precedence when it is stricter. Always inspect the judge
explanation and final trace rather than relying only on the aggregate mean.

### Default smoke dataset

The no-argument commands select `basic-dataset.json` and `eval_config.yaml`.
Use explicit paths so grading cannot combine JSON traces left by earlier
default runs in `artifacts/traces/`:

```bash
INTEGRATION_TEST=TRUE agents-cli eval generate \
  --dataset tests/eval/datasets/basic-dataset.json \
  --output artifacts/traces/basic-smoke.json
agents-cli eval grade \
  --traces artifacts/traces/basic-smoke.json \
  --config tests/eval/eval_config.yaml \
  --output artifacts/grade_results/basic-smoke
```

This is an exploratory smoke suite: manually confirm that the school-topic case
returns a usable quiz and the off-topic weather request does not produce one.
It is not a substitute for the focused suites below.

### Custom Dataset
```bash
# Generate traces for a custom dataset
INTEGRATION_TEST=TRUE agents-cli eval generate \
  --dataset tests/eval/datasets/custom-dataset.json \
  --output artifacts/traces/custom.json
agents-cli eval grade \
  --traces artifacts/traces/custom.json \
  --config tests/eval/eval_config.yaml \
  --output artifacts/grade_results/custom
```

### Grounding relevance

Run this suite after changes to `decision_and_search`, Wikipedia grounding,
grounding discard rules, or prompts that use retrieved context. It checks that
unrelated search material cannot move the quiz away from the requested topic
and that an underspecified Grade 12 request is clarified appropriately.

```bash
INTEGRATION_TEST=TRUE agents-cli eval generate \
  --dataset tests/eval/datasets/grounding-relevance-dataset.json \
  --output artifacts/traces/grounding-relevance.json
agents-cli eval grade \
  --traces artifacts/traces/grounding-relevance.json \
  --config tests/eval/grounding_eval_config.yaml \
  --output artifacts/grade_results/grounding-relevance
```

Both cases should receive `quiz_topic_alignment` score 5. A safe failure is
preferable to an unrelated quiz but does not satisfy the highest-score gate.

### Adaptive Hard-Mode Regression

These local-only behavioral cases reproduce a Grade 5 learner selecting the
hard follow-up after a perfect score. They verify that `🚀 Hard` is treated as
relative to Grade 5 and is not rejected merely because of the learner's grade.
The mathematics case additionally checks varied task forms, manageable manual
calculation, and misconception-based distractors instead of difficulty created
only through larger numbers or tightly clustered answer choices.

```bash
INTEGRATION_TEST=TRUE agents-cli eval generate \
  --dataset tests/eval/datasets/adaptive-hard-difficulty-dataset.json \
  --output artifacts/traces/adaptive-hard
agents-cli eval grade \
  --traces artifacts/traces/adaptive-hard \
  --config tests/eval/adaptive_hard_eval_config.yaml \
  --output artifacts/grade_results/adaptive-hard
```

The run uses live Vertex AI and must remain local because Google credentials
are not stored in GitHub.

### Grades 1–4

The primary-school campaign contains eight successful quiz requests across
German, English, and Portuguese plus two separate curriculum-routing cases.
The quiz config combines deterministic grade-aware structure checking with an
LLM rubric for factual, linguistic, and pedagogical quality. The routing config
checks that an unsuitable topic is rejected and an overly broad foundational
topic is clarified before generation.

Start with one or two successful cases while iterating, then run the complete
eight-case quiz dataset and both routing cases:

```bash
INTEGRATION_TEST=TRUE agents-cli eval generate \
  --dataset tests/eval/datasets/grades-1-to-4.json \
  --output artifacts/traces/grades-1-to-4
agents-cli eval grade \
  --traces artifacts/traces/grades-1-to-4 \
  --config tests/eval/grades_1_to_4_eval_config.yaml \
  --output artifacts/grade_results/grades-1-to-4

INTEGRATION_TEST=TRUE agents-cli eval generate \
  --dataset tests/eval/datasets/grades-1-to-4-routing.json \
  --output artifacts/traces/grades-1-to-4-routing
agents-cli eval grade \
  --traces artifacts/traces/grades-1-to-4-routing \
  --config tests/eval/grades_1_to_4_routing_eval_config.yaml \
  --output artifacts/grade_results/grades-1-to-4-routing
```

These evaluations call live Vertex AI models but execute the application
locally; generated artifacts remain ignored and must not be committed.

### Answer normalization and targeted repair

This regression dataset contains two production-derived scenarios: a Grade 2
language quiz that historically exposed negative-question and question-emoji
defects, and a Grade 7 number-sequence quiz that historically exposed an
answer/explanation/index mismatch. Each scenario appears ten times so the live
quality gate measures the required repetition count.

Run locally with live Vertex AI credentials:

```bash
INTEGRATION_TEST=TRUE agents-cli eval generate \
  --dataset tests/eval/datasets/quiz-answer-normalization-targeted-repair.json \
  --output artifacts/traces/quiz-answer-normalization-targeted-repair \
  --concurrency 2

agents-cli eval grade \
  --traces artifacts/traces/quiz-answer-normalization-targeted-repair \
  --config tests/eval/quiz-answer-normalization-targeted-repair_eval_config.yaml \
  --output artifacts/grade_results/quiz-answer-normalization-targeted-repair
```

The release gate is at least 9 successful completions out of 10 for each
scenario. A successful completion has `quiz_structure` score 1 and
`answer_normalization_targeted_repair_quality` score 5. No released quiz may
contain an incorrect answer or index, including in the one allowed failed run.
Compare token and latency medians against the full-regeneration baseline when
targeted-repair traces are available.

### Multilingual Curriculum Routing & Language Purity

This dataset tests curriculum validation and mascot guidance across English,
Portuguese, and German when a topic is cognitively inappropriate or outside
the requested grade (e.g. Grade 5 Differential Equations). The LLM-as-a-judge
metric verifies rejection without quiz output, encouraging mascot tone,
age-appropriate suggested topics, and strict 100% target language purity with
zero foreign language leakage.

```bash
INTEGRATION_TEST=TRUE agents-cli eval generate \
  --dataset tests/eval/datasets/multilingual-curriculum-incompatible.json \
  --output artifacts/traces/multilingual-curriculum-incompatible
agents-cli eval grade \
  --traces artifacts/traces/multilingual-curriculum-incompatible \
  --config tests/eval/multilingual_routing_eval_config.yaml \
  --output artifacts/grade_results/multilingual-curriculum-incompatible
```


### Token-observability rollout

The three token-observability files do not represent three independent
traffic cohorts:

| Dataset | Cases | Purpose |
|---|---:|---|
| `token-observability-pilot.json` | 5 | Small post-deployment safety check at concurrency 2. |
| `token-observability-rollout.json` | 45 | Remaining measurement cohort at concurrency 4. |
| `token-observability-regression.json` | 10 | Reusable behavioral subset for generate-and-grade regression checks. |

The pilot and rollout datasets contain 50 unique cases together. The
regression dataset intentionally reuses all five pilot cases and five selected
rollout cases; it is not an additional ten-case measurement cohort:

```text
50-case observability baseline
├── pilot: 5 cases
└── rollout: 45 different cases

10-case behavioral regression suite
├── all 5 pilot cases
└── 5 selected rollout cases
```

Across the 50 unique cases, the matrix covers 35 initial structured quiz
requests and 15 structured adaptive follow-ups (five each for easy, medium,
and hard). It varies languages, grades, subjects, and topics so the baseline is
not dominated by one request shape. The five-case
pilot is a compact cross-section: three initial requests in German, English,
and Portuguese, one adaptive easy request, and one adaptive hard request.

`generate_token_observability_datasets.py` is the human-readable source of
truth for this matrix. Edit that generator instead of editing the generated
JSON files directly, and then regenerate all three files:

```bash
uv run python tests/eval/generate_token_observability_datasets.py
```

Each generated case also seeds `agent_data.agents.root_agent` as the actual
`Workflow` root and leaves `agent_data.turns` empty. ADK's `/app-info` route
only describes `LlmAgent` roots and returns HTTP 400 for this application, so
agents-cli cannot discover the metadata automatically. Empty turns keep the
top-level prompt unambiguous, and agents-cli preserves the seeded metadata
when it appends the generated trace events.

The apparent JSON-inside-JSON structure is intentional. Agents CLI requires
the outer `prompt.parts[].text` message envelope, while FoxQuiz's frontend
normally sends structured quiz parameters as JSON text. For example:

```json
{
  "prompt": {
    "role": "user",
    "parts": [
      {
        "text": "{\"grade\":\"Klasse 5\",\"subject\":\"Naturwissenschaften\",\"topic\":\"Wasserkreislauf\",\"preferred_language\":\"de\"}"
      }
    ]
  }
}
```

FoxQuiz receives the decoded `text` value as the same structured message sent
by the browser. Adaptive cases are longer because they also carry realistic
`previous_score`, `previous_questions`, `previous_quiz_json`, or
`selected_difficulty` context. That extra context is required to measure the
different token profile of adaptive quiz generation.

After deploying the frozen production candidate to DEV, set
`GCLOUD_RUN_DEV_URL` in the local shell without committing its real value. Set
`RELEASE_REVISION` to the deployed commit's short SHA, then run the pilot with
two workers:

```bash
export RELEASE_REVISION="$(git rev-parse --short "${RELEASE_CANDIDATE_COMMIT:-HEAD}")"

agents-cli eval generate \
  --url "${GCLOUD_RUN_DEV_URL}" \
  --app-name app \
  --dataset tests/eval/datasets/token-observability-pilot.json \
  --output "artifacts/traces/token-observability/${RELEASE_REVISION}/pilot-c2.json" \
  --concurrency 2

agents-cli eval grade \
  --traces "artifacts/traces/token-observability/${RELEASE_REVISION}/pilot-c2.json" \
  --config tests/eval/token_observability_eval_config.yaml \
  --output "artifacts/grade_results/token-observability/${RELEASE_REVISION}/pilot-c2"
```

The pilot passes automatically when all five cases complete without dropped
cases, HTTP 429 or 5xx responses, or timeouts; every
`quiz_structure_validity` score is 1; and every `quiz_request_fulfillment`
score is 5. A fulfillment score of 4 requires documented human review and
acceptance before release, while 3 or below fails the gate. Confirm in Cloud
Logging that events identify `RELEASE_REVISION`, token summaries exist, and no
unexpected budget or persistence failures occurred. Also confirm that DEV
writes appear only in `foxquiz-dev`, never `(default)`.

Stop here for a routine production release. Run the remaining 45 cases only
when establishing a new telemetry, budget, latency, scaling, or concurrency
baseline, and only after the pilot is healthy:

```bash
agents-cli eval generate \
  --url "${GCLOUD_RUN_DEV_URL}" \
  --app-name app \
  --dataset tests/eval/datasets/token-observability-rollout.json \
  --output "artifacts/traces/token-observability/${RELEASE_REVISION}/rollout-c4.json" \
  --concurrency 4
```

Do not add a fixed `X-Anonymous-ID` header. With agents-cli 1.3.1, each remote
case uses independent HTTP requests without retaining FoxQuiz's anonymous
cookie, so FoxQuiz assigns a transient budget identity. A fixed header would
place the complete run under one 150,000-token user budget. Global budget
enforcement remains enabled throughout the rollout.

An optional concurrency experiment must repeat the same five pilot cases so
case mix cannot be mistaken for a concurrency effect:

```bash
agents-cli eval generate \
  --url "${GCLOUD_RUN_DEV_URL}" \
  --app-name app \
  --dataset tests/eval/datasets/token-observability-pilot.json \
  --output "artifacts/traces/token-observability/${RELEASE_REVISION}/pilot-c8.json" \
  --concurrency 8
```

The 50-case run measures a telemetry distribution; it is not the routine
regression suite. The overlapping ten-case subset provides focused behavioral
coverage and is graded with its dedicated configuration:

```bash
INTEGRATION_TEST=TRUE agents-cli eval generate \
  --dataset tests/eval/datasets/token-observability-regression.json \
  --output artifacts/traces/token-observability-regression
agents-cli eval grade \
  --traces artifacts/traces/token-observability-regression \
  --config tests/eval/token_observability_eval_config.yaml \
  --output artifacts/grade_results/token-observability-regression
```

Generated traces and grades can contain prompts and quiz content. They remain
under the ignored `artifacts/` directory and must not be committed. Cloud
Logging's privacy-minimized invocation summaries are the authoritative count
of successful rollout quizzes.

### Structured-request measurement

The structured-request milestone reuses `token-observability-pilot.json` for
three initial and two adaptive quiz requests. Two additional generated inputs
cover the request-contract branches:

| Dataset | Cases | Purpose |
|---|---:|---|
| `structured-request-contract-safe.json` | 5 | Free-form, malformed, incomplete, clarification-required, and clarification-follow-up requests. |
| `structured-request-contract-malicious.json` | 1 | A prompt-injection request expected to take the security-block branch. |

Obtain explicit human approval and deploy a new temporary public DEV campaign
before generating these traces. Run and grade the safe contract cases first:

```bash
agents-cli eval generate \
  --url "${GCLOUD_RUN_DEV_URL}" \
  --app-name app \
  --dataset tests/eval/datasets/structured-request-contract-safe.json \
  --output artifacts/traces/structured-request-contract-safe \
  --concurrency 1
agents-cli eval grade \
  --traces artifacts/traces/structured-request-contract-safe \
  --config tests/eval/structured_request_eval_config.yaml \
  --output artifacts/grade_results/structured-request-contract-safe
```

Run the malicious case separately, at concurrency one, and only after every
other campaign request. It intentionally creates a security event and may
activate Sheriff controls; never run it against production or a shared service:

```bash
agents-cli eval generate \
  --url "${GCLOUD_RUN_DEV_URL}" \
  --app-name app \
  --dataset tests/eval/datasets/structured-request-contract-malicious.json \
  --output artifacts/traces/structured-request-contract-malicious \
  --concurrency 1
agents-cli eval grade \
  --traces artifacts/traces/structured-request-contract-malicious \
  --config tests/eval/structured_request_eval_config.yaml \
  --output artifacts/grade_results/structured-request-contract-malicious
```

Use privacy-minimized Cloud Logging events to confirm that rejected request
cases emit no `parameter_extractor` or `mascot_prompt` call stage. The security
classifier remains expected for requests not caught by deterministic security
rules. Grade results validate response behavior; logs remain authoritative for
provider-reported token usage.

## Dataset Format

Each dataset file follows the Gemini Enterprise Agent Platform Evaluation
dataset format. An eval case may use **either** of two shapes — both are
valid input to `agents-cli eval generate`:

**Shape A — single-prompt case:**

```json
{
  "eval_cases": [
    {
      "eval_case_id": "unique_case_id",
      "prompt": {
        "role": "user",
        "parts": [{"text": "User message"}]
      }
    }
  ]
}
```

**Shape B — continued-conversation case (the "N+1" pattern):**
The case carries prior turns in `agent_data` and the last turn ends with a
user message; `eval generate` appends the next agent response.

```json
{
  "eval_cases": [
    {
      "eval_case_id": "unique_case_id",
      "agent_data": {
        "turns": [
          {
            "turn_index": 0,
            "events": [
              {"author": "user",  "content": {"role": "user",  "parts": [{"text": "First user message"}]}},
              {"author": "agent", "content": {"role": "model", "parts": [{"text": "First agent reply"}]}},
              {"author": "user",  "content": {"role": "user",  "parts": [{"text": "Follow-up user message"}]}}
            ]
          }
        ]
      }
    }
  ]
}
```

## Key Fields

- `eval_cases`: Array of evaluation cases.
- `eval_case_id`: Unique identifier for the evaluation case (optional).
- `prompt`: A single user message — Shape A.
- `agent_data.turns`: Prior conversation turns ending with a user message — Shape B.

## Creating Custom Datasets

You can create custom datasets in two ways:

1. **By Hand**: Copy `basic-dataset.json` as a template and manually add evaluation cases.
2. **Synthesize**: Use the synthetic dataset generation command to generate conversation scenarios:
   ```bash
   agents-cli eval dataset synthesize --count 10
   ```

## Discovering Metrics

You can discover available out-of-the-box evaluation metrics by running:

```bash
agents-cli eval metric list
```

## Beyond Generate and Grade

Once you have a baseline, the eval surface has a few more commands worth knowing about:

- `agents-cli eval compare BASE CAND` — diff two grade-results files (regression check).
- `agents-cli eval analyze RESULTS` — cluster failure modes from a grade-results file.
- `agents-cli eval optimize` — auto-tune your agent's prompts using eval data.

See the [Evaluation Guide](https://google.github.io/agents-cli/guide/evaluation/) for the full surface and metric reference.
