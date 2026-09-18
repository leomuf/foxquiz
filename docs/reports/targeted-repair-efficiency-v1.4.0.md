# Targeted-Repair Efficiency and Token Baseline Measurement (v1.4.0)

Date: 2026-09-18

Release baseline: `5dcc0b4`

Decision: Adopt targeted question repair for localized deterministic and academic Judge defects.

## Decision Summary

Accept targeted question repair as the primary remediation path for localized defects
identified during quiz validation and academic review.

Empirical verification against Vertex AI with live `gemini-2.5-flash` calls demonstrates that
targeted repair delivers:
- an **87.3% reduction in candidate tokens** (1,517 down to 192 median);
- a **48.6% reduction in total tokens** (4,481 down to 2,304 median); and
- a **54.7% faster turnaround latency** (17.49s down to 7.93s median).

These savings significantly exceed the architectural acceptance criterion set in
`specs/my_spec.md` (which required at least a 20% token reduction and no more than a 10%
latency increase).

## Objective

During the operation of FoxQuiz 1.3.2, isolated candidate defects (such as a single factual
inaccuracy, an inverted explanation, or a stray emoji in one question) occasionally triggered
full quiz regenerations. Full regenerations discard 9 valid questions, re-query the LLM for
all 10 questions, and consume substantial token budgets.

The objective of targeted question repair is to isolate affected questions by index, regenerate
only those questions using focused prompts and schemas, and splice them back into the validated
quiz without touching unaffected questions. This report benchmarks the real-world token and
latency efficiency of this approach against full regeneration.

## Method and Test Setup

- **Platform & Runtime:** FoxQuiz ADK agent running with live Vertex AI model calls
  under application default credentials (`GCLOUD_PROJECT_ID`).
- **Models & Configurations:**
  - Full Quiz Generator: `gemini-2.5-flash`, temperature `0.6`, structured schema `GeneratedQuiz`.
  - Targeted Question Repair: `gemini-2.5-flash`, temperature `0.2`, structured schema `GeneratedQuestionRepairResponse`.
- **Benchmark Scenario:** Grade 7 Biology quiz on Photosynthesis (`Klasse 7`, `Biologie`, `Photosynthese`, language `de`).
- **Procedure:**
  1. Generate a complete 10-question candidate quiz and record prompt tokens, candidate tokens, total tokens, and latency.
  2. Simulate an Academic Judge finding with a localized defect on Question 2 (`code="factual_error"`, `question_indices=[2]`).
  3. Execute `_repair_targeted_questions` to repair only Question 2 with focused context and unaffected question texts for deduplication.
  4. Record the repair stage's prompt tokens, candidate tokens, total tokens, and latency.
  5. Repeat across sequential live runs to compute stable medians.

## Comparative Measurement Results

### Detailed Run Observations

| Run | Strategy | Candidate Tokens | Total Tokens | Latency |
|:---|:---|---:|---:|---:|
| Run 1 | Full Generation (10 questions) | 1,444 | 4,359 | 17.49s |
| Run 1 | Targeted Repair (1 question) | 192 | 2,877 | 11.51s |
| Run 2 | Full Generation (10 questions) | 1,543 | 4,481 | 17.95s |
| Run 2 | Targeted Repair (1 question) | 198 | 2,142 | 7.67s |
| Run 3 | Full Generation (10 questions) | 1,517 | 4,645 | 16.89s |
| Run 3 | Targeted Repair (1 question) | 170 | 2,304 | 7.93s |

### Median Comparison and Savings

| Metric | Full Generation Baseline | Targeted Repair Candidate | Absolute Delta | Relative Improvement | Spec Requirement |
|:---|---:|---:|---:|---:|:---|
| **Candidate Tokens (median)** | 1,517 | 192 | -1,325 | **-87.3%** | — |
| **Total Tokens (median)** | 4,481 | 2,304 | -2,177 | **-48.6%** | ≥ 20.0% reduction |
| **Turnaround Latency (median)** | 17.49s | 7.93s | -9.56s | **-54.7%** | ≤ 10.0% overhead |

## Analysis

1. **Candidate Token Economy:** Generating a single repaired question requires ~170–200
   tokens, compared to ~1,450–1,550 tokens for generating all ten questions. This yields an
   effective candidate generation saving of **87.3%**.
2. **Total Token Economy:** Because the repair prompt includes structured defect instructions
   and the texts of unaffected questions for deduplication, prompt tokens are slightly higher;
   nevertheless, the total token footprint drops by **48.6%** (from 4,481 to 2,304 tokens).
3. **Turnaround Latency:** Because `gemini-2.5-flash` outputs significantly fewer tokens during
   single-question repair, generation finishes more than twice as fast (median 7.93s vs 17.49s,
   representing a **54.7% speedup**).

## Quality and Invariant Preservation

Alongside token efficiency, targeted repair maintains complete quality safety:
- **100% Question Preservation:** Unaffected questions remain untouched in text, options, and order.
- **Deterministic Shuffling:** Repaired questions pass through `normalize_generated_quiz`, which
  validates option distinctness, shuffles options, derives the public `correct_option_index`, and
  completely strips the internal `correct_answer`.
- **Universal Emoji Policy:** Repaired questions strictly enforce the removal of question and
  option emojis.
- **Regression Cohort:** In the 20 production-derived live evaluation cases, all released quizzes
  achieved 1.0 on `quiz_structure` and 5.0 on `answer_normalization_targeted_repair_quality`.
