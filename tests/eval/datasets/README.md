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

### Token-observability rollout

The token-observability baseline uses two versioned input datasets:

- `token-observability-pilot.json`: five representative cases;
- `token-observability-rollout.json`: the remaining 45 cases.

Regenerate both files and the ten-case behavioral subset after deliberately
editing the matrix:

```bash
uv run python tests/eval/generate_token_observability_datasets.py
```

After deploying the telemetry revision to `foxquiz-dev`, create an artifact
directory named for its short commit SHA. Run the pilot with two workers:

```bash
agents-cli eval generate \
  --url https://foxquiz-dev-zeuzcpbnba-ue.a.run.app \
  --app-name app \
  --dataset tests/eval/datasets/token-observability-pilot.json \
  --output artifacts/traces/token-observability/<REVISION>/pilot-c2.json \
  --concurrency 2
```

Check successful summaries, HTTP 429 and 5xx responses, timeouts, retries,
latency, and projected global token usage before running the remaining cases:

```bash
agents-cli eval generate \
  --url https://foxquiz-dev-zeuzcpbnba-ue.a.run.app \
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
  --url https://foxquiz-dev-zeuzcpbnba-ue.a.run.app \
  --app-name app \
  --dataset tests/eval/datasets/token-observability-pilot.json \
  --output artifacts/traces/token-observability/<REVISION>/pilot-c8.json \
  --concurrency 8
```

The 50-case run measures a telemetry distribution; it is not the routine
regression suite. A ten-case subset provides focused behavioral coverage:

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
