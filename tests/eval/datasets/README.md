# Evaluation Datasets

This directory contains evaluation datasets for testing agent behavior.

## Running Evaluations

### Default Dataset
```bash
# Generate traces using the default dataset
agents-cli eval generate
agents-cli eval grade
```

### Custom Dataset
```bash
# Generate traces for a custom dataset
agents-cli eval generate --dataset tests/eval/datasets/custom-dataset.json --output custom_traces/
agents-cli eval grade --metrics general_quality --traces custom_traces/
```

### Adaptive Hard-Mode Regression

These local-only behavioral cases reproduce a Grade 5 learner selecting the
hard follow-up after a perfect score. They verify that `🚀 Hard` is treated as
relative to Grade 5 and is not rejected merely because of the learner's grade.
The mathematics case additionally checks varied task forms, manageable manual
calculation, and misconception-based distractors instead of difficulty created
only through larger numbers or tightly clustered answer choices.

```bash
agents-cli eval generate \
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
agents-cli eval generate \
  --dataset tests/eval/datasets/grades-1-to-4.json \
  --output artifacts/traces/grades-1-to-4
agents-cli eval grade \
  --traces artifacts/traces/grades-1-to-4 \
  --config tests/eval/grades_1_to_4_eval_config.yaml \
  --output artifacts/grade_results/grades-1-to-4

agents-cli eval generate \
  --dataset tests/eval/datasets/grades-1-to-4-routing.json \
  --output artifacts/traces/grades-1-to-4-routing
agents-cli eval grade \
  --traces artifacts/traces/grades-1-to-4-routing \
  --config tests/eval/grades_1_to_4_routing_eval_config.yaml \
  --output artifacts/grade_results/grades-1-to-4-routing
```

These evaluations call live Vertex AI models but execute the application
locally; generated artifacts remain ignored and must not be committed.

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

After deploying the telemetry revision to DEV, set `GCLOUD_RUN_DEV_URL` in the
local shell without committing its real value. Create an artifact directory
named for the revision's short commit SHA, then run the pilot with two workers:

```bash
agents-cli eval generate \
  --url "${GCLOUD_RUN_DEV_URL}" \
  --app-name app \
  --dataset tests/eval/datasets/token-observability-pilot.json \
  --output artifacts/traces/token-observability/<REVISION>/pilot-c2.json \
  --concurrency 2
```

Check successful summaries, HTTP 429 and 5xx responses, timeouts, retries,
latency, and projected global token usage before running the remaining cases:

```bash
agents-cli eval generate \
  --url "${GCLOUD_RUN_DEV_URL}" \
  --app-name app \
  --dataset tests/eval/datasets/token-observability-rollout.json \
  --output artifacts/traces/token-observability/<REVISION>/rollout-c4.json \
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
  --output artifacts/traces/token-observability/<REVISION>/pilot-c8.json \
  --concurrency 8
```

The 50-case run measures a telemetry distribution; it is not the routine
regression suite. The overlapping ten-case subset provides focused behavioral
coverage and is graded with its dedicated configuration:

```bash
agents-cli eval generate \
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
