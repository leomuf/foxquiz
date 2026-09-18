# Quiz Answer Normalization and Targeted Repair — Implementation Plan

## Status

Implemented in v1.4.0. This document serves as a historical architecture and
design record. For current system behavior, invariants, and API contracts,
consult `specs/my_spec.md`. For quantitative latency and token metrics,
consult `docs/reports/targeted-repair-efficiency-v1.4.0.md`.

### Implementation progress

- [x] Create the implementation branch from `main`.
- [x] Add internal `correct_answer` generation and repair contracts.
- [x] Add the common deterministic answer normalization boundary.
- [x] Convert initial, adaptive, full-retry, targeted-repair, and option-repair
  generation paths away from LLM-supplied indices.
- [x] Enforce universal emoji rejection in question text and options.
- [x] Add Firestore-backed validated-quiz provenance for safe reinforcement
  reuse, including fingerprints, context checks, and short-lived records.
- [x] Complete structured Judge routing, fail-closed parsing, bounded repair
  history, and privacy-safe schema-v2 diagnostics.
- [x] Scope Judge emoji review to question text and answer options, and make
  task-variety rejection conditional on the topic naturally supporting more
  distinct cognitive forms.
- [x] Add deterministic and mocked regression coverage for normalization,
  routing, targeted repair, provenance, and public-field safety.
- [x] Synchronize `specs/my_spec.md` and the README workflow diagram and
  surrounding contract text with normalization, Judge routing, and Firestore
  reinforcement provenance.
- [x] Run local unit/browser tests (`229` unit and `8` browser tests), lint,
  formatting, type, compilation, and diff checks.
- [x] Add the two production-derived live regression scenarios with ten
  repetitions each and run the post-fix evaluation. Grade 2 and Grade 7 both
  completed 10/10. All 20 released quizzes passed deterministic validation,
  contained no `correct_answer`, and had no question or option emojis.
- [x] Complete performance-baseline verification. Live integration suite
  achieved 18 passed out of 18 (including Grade 5 Portuguese hard mode and
  Grade 12 economics broad-topic routing). Targeted repair verified against
  full regeneration with 87.3% candidate token reduction (1,517 -> 192
  median), 48.6% total token reduction (4,481 -> 2,304 median), and 54.7%
  latency reduction (17.49s -> 7.93s median). All 20 production-derived eval
  runs passed with 1.0 structure and 5.0 quality scores.

## 1. Motivation

During the first two production days of FoxQuiz 1.3.2, two invocations ended
in quality_failure. Both candidates passed deterministic validation and were
then rejected twice by the academic Judge.

The pattern was the same in both incidents:

1. The first candidate contained a localized defect.
2. The Judge described that defect correctly.
3. FoxQuiz regenerated the complete ten-question quiz.
4. The second candidate removed the original defect but introduced a different
   one and was blocked.

One incident moved from a prohibited negative question to question emojis that
revealed several answers. The other moved from two incorrect
correct_option_index values to a new incorrect index in another question. In
that second candidate, the explanation named the correct answer while the index
pointed to a different option.

The incidents expose two design problems:

- the LLM performs positional bookkeeping that application code can do
  deterministically;
- full regeneration is too broad when only one or a few questions are faulty.

## 2. Goals

- Remove correct_option_index from every LLM generation and repair schema.
- Have the LLM return correct_answer and let application code derive the final
  0-based index deterministically.
- Apply this contract to initial generation, adaptive generation, full retries,
  targeted question repairs, and option repairs.
- Prohibit emojis in question text and options for every grade.
- Continue allowing emojis in titles, explanations, difficulty presentation,
  mascot messages, and other non-answer-bearing UI elements.
- Replace free-text-only Judge failures with structured, question-addressable
  issues.
- Repair only affected questions when a failure is local.
- Keep the browser and persisted public quiz format backward compatible.
- Retain the academic Judge for factual and pedagogical verification.

## 3. Non-Goals

This work will not:

- remove the academic Judge;
- claim that deterministic index derivation proves factual correctness;
- change the LLM model;
- change question or option counts;
- change the frontend quiz-solving contract;
- migrate existing quizzes only to replace their indices;
- increase the retry budget without supporting measurements;
- deploy FoxQuiz or modify Google Cloud infrastructure.

## 4. Internal Generation Contract

Introduce internal models used only for LLM output:

    class GeneratedQuizQuestion(BaseModel):
        question: str
        options: list[str]
        correct_answer: str
        explanation: str


    class GeneratedQuiz(BaseModel):
        title: str
        questions: list[GeneratedQuizQuestion]
        difficulty: str | None = None

Example model output:

    {
      "question": "What is the next number: 2, 5, 8, 11?",
      "options": ["13", "14", "15", "16"],
      "correct_answer": "14",
      "explanation": "The sequence increases by 3 each time."
    }

The public QuizQuestion stays unchanged:

    {
      "question": "What is the next number: 2, 5, 8, 11?",
      "options": ["16", "13", "14", "15"],
      "correct_option_index": 2,
      "explanation": "The sequence increases by 3 each time."
    }

correct_answer is an internal construction field. It must not be returned to
the browser or written into normal shared-quiz and feedback records.

## 5. Deterministic Candidate Normalization

Create one normalization function as the only supported conversion from an
LLM-generated question into the public question model.

For every question it must:

1. Validate the grade-specific option count.
2. Normalize options and correct_answer with the existing Unicode and
   whitespace rules used for duplicate detection.
3. Preserve meaningful case distinctions such as scientific notation.
4. Require correct_answer to match exactly one option after normalization.
5. Reject zero matches as correct_answer_not_in_options.
6. Reject multiple matches as ambiguous_correct_answer.
7. Shuffle options using application code.
8. Find the answer in the shuffled list and derive correct_option_index.
9. Remove correct_answer when constructing the public model.

Use this boundary for every LLM-producing path:

- initial quiz generation;
- standard adaptive practice generation;
- hard-mode progression generation;
- full academic regeneration for quiz-wide issues;
- targeted question repair;
- duplicate-option repair.

Reinforcement mode may continue shuffling an already validated public quiz if
the existing helper moves its known valid index with the options.

### Correctness boundary

The conversion guarantees that correct_option_index points to the option the
generator selected as correct. It does not prove that this option is objectively
correct. The academic Judge must still independently verify every answer. A
Judge-suggested correction must also pass deterministic validation and a full
academic review before release.

## 6. Universal Emoji Policy

Prohibit emojis in question text and answer options for all grades. This is a
universal rule because emojis can reveal answers at any age or difficulty.

Align every enforcement layer:

1. Update generation model descriptions and prompt instructions.
2. Set every grade policy to disallow question emojis, or replace the
   grade-dependent setting with one global rule.
3. Deterministically reject every emoji in question text.
4. Keep rejecting emojis and correctness cues in options.
5. Make the Judge verify the same simple rule instead of deciding whether an
   individual emoji is semantically revealing.

The Judge must inspect only question text and answer options for this issue.
Emojis remain valid in titles, explanations, difficulty badges, mascot text,
success messages, and other presentation-only UI elements; an emoji in one of
those fields must not produce `emoji_in_question`.

## 7. Structured Judge Result

Replace passed plus a free-text-only reason with structured issues and an
overall readable summary:

    class JudgeIssue(BaseModel):
        code: JudgeIssueCode
        question_indices: list[int]
        explanation: str
        repair_instruction: str


    class JudgeAssessment(BaseModel):
        passed: bool
        summary: str
        issues: list[JudgeIssue]

Initial issue codes should include:

- factual_error;
- correct_answer_mismatch;
- negative_question;
- emoji_in_question;
- grade_scope_violation;
- difficulty_mismatch;
- language_mismatch;
- task_variety_failure;
- explanation_error;
- other.

Validate these invariants:

- passed=true requires an empty issue list;
- passed=false requires at least one issue;
- every question index is an integer from 0 through 9;
- quiz-wide issues use an empty question_indices list;
- free text is never parsed to infer indices or routing decisions.

Route only the following local codes to targeted repair when every issue has a
valid, non-contradictory 0-based question index:
`factual_error`, `correct_answer_mismatch`, `negative_question`,
`emoji_in_question`, and `explanation_error`. Route
`grade_scope_violation`, `difficulty_mismatch`, `language_mismatch`, and
`task_variety_failure` to full regeneration. Mixed issues, `other`, or missing,
invalid, or contradictory indices use full regeneration. A malformed Judge
response or Judge exception fails closed. The existing academic repair budget
allows at most one academic repair after the initial rejection; deterministic
repairs retain their separate budget.

## 8. Targeted Repair Routing

### Question-local repair

When all issues name valid question indices and can be corrected locally, send
only the affected complete questions, their 0-based indices, structured issue
records, grade, subject, topic, language, curriculum guidance, expected
difficulty, previous score, selected progression difficulty, relevant grounding
context, and general generation rules to a targeted repair call. Include only
the question texts of unaffected questions for duplicate prevention; do not
send their options or explanations. The response must contain one complete
generated question with `correct_answer` for each requested index and no
additional indices.

Normalize every repaired question through the common correct_answer conversion
before replacing it in a copy of the candidate. Typical local repairs include:

- rewriting a negative question positively;
- removing an emoji from question text;
- correcting a factual answer or explanation;
- replacing ambiguous or invalid options.

### Quiz-wide regeneration

Keep full regeneration for genuinely global issues such as wrong language
throughout the quiz, broad grade-scope mismatch, materially repetitive task
design when the topic naturally supports additional distinct forms, or globally
wrong difficulty. A narrow topic may use fewer than four task forms when that
is the strongest natural variety available; do not manufacture artificial forms
or reject solely because a fixed numeric minimum was not reached.

### Validation after repair

After either route:

1. Run deterministic validation on the complete assembled quiz.
2. Run the academic Judge on the complete assembled quiz.
3. Release only after both layers pass.
4. Preserve fail-closed behavior when the repair budget is exhausted.

Start with the existing single academic repair budget. Do not add another full
retry until production evidence shows a need and acceptable cost.

## 9. Server-Backed Reinforcement Provenance

ADK session state is not a reliable provenance boundary because the current
production configuration uses `InMemorySessionService`. After a quiz passes
deterministic validation and the academic Judge, persist a short-lived record
in the Firestore `validated_quizzes` collection containing:

- a cryptographically random `validated_quiz_id`;
- the normalized public quiz;
- a deterministic quiz fingerprint;
- a context fingerprint covering grade, subject, topic, and language;
- the validation-contract version;
- the FoxQuiz version;
- creation and expiration timestamps.

Return only `validated_quiz_id` alongside the public quiz. For an adaptive
request, the browser sends this identifier instead of treating
`previous_quiz_json` as trusted provenance. The server loads the Firestore
record and never treats client-provided quiz data as authoritative merely
because an identifier is present.

Reinforcement mode may reuse and shuffle the stored quiz directly only when the
record is present, unexpired, bound to the exact current context, compatible
with the current validation contract, and valid under current deterministic
validation. Otherwise, fall back to ordinary generation and academic Judge
review. The direct-reuse path still performs deterministic validation again and
does not send the client quiz to the model. Firestore TTL cleanup is configured
on `expires_at` with one-day retention; reads enforce expiration independently.

Do not store user IDs, session IDs, raw prompts, or rejected candidate snapshots
in provenance records. ADK state may cache the identifier for convenience, but
Firestore remains the source of truth.

## 10. Diagnostics and Privacy

Use diagnostic schema version 2 with these fields: `failure_type`,
`quiz_context`, `generation_attempts`, `judge_attempts`,
`academic_repair_attempts`, `deterministic_repair_attempts`, `judge_history`,
`repair_history`, `normalization_failures`, `usage_summary`, `duration_ms`,
`service_version`, `deployment_revision`, `grounding_title`,
`grounding_discarded`, and `timestamp`. Each Judge-history entry contains
`attempt`, `passed`, `issue_codes`, `question_indices`, and `selected_route`.
Each repair-history entry contains `attempt`, `kind` (`targeted` or
`full_regeneration`), `issue_codes`, `question_indices`, and `result`.
The usage summary contains `model_call_count`, `prompt_token_count`,
`candidate_token_count`, `thoughts_token_count`, `total_token_count`, and
`stage_total_token_counts`.

Do not store raw prompts, complete model responses, user or session IDs, or
rejected candidate snapshots by default. Retain only bounded, privacy-safe
diagnostics.

Rejected candidate snapshots should be retained only after an explicit privacy
and retention review. If enabled, use a strict bounded schema, exclude raw
prompts and user/session identifiers, define a retention period, and never emit
the snapshot to Cloud Logging. Without snapshots, failure categories can be
counted but Judge claims cannot always be independently verified later.

## 11. Implementation Sequence

### Phase 1: Answer model and normalization

1. Add the internal generation models.
2. Add missing and ambiguous answer normalization issue codes.
3. Implement the common normalization boundary.
4. Convert the initial generator to correct_answer.
5. Convert all remaining LLM generation and repair paths.
6. Verify that public, shared, feedback, and frontend payloads retain only
   correct_option_index.

Checkpoint:

    uv run pytest tests/unit -q

### Phase 2: Universal emoji prohibition

1. Align prompts, model descriptions, grade policies, deterministic validation,
   and Judge instructions.
2. Replace tests that currently allow decorative question emojis.
3. Test single-codepoint, flag, skin-tone, and joined Unicode emoji sequences.

Checkpoint:

    uv run pytest tests/unit/test_quiz_validation.py tests/unit/test_grade_policy.py tests/unit/test_agent_helpers.py -q

### Phase 3: Structured Judge issues

1. Add the issue enum and structured Judge models.
2. Update the Judge prompt and response validation.
3. Store structured issue history in request state.
4. Keep a fail-closed fallback for malformed or unavailable responses.
5. Update privacy-focused operational logging tests.

Checkpoint:

    uv run pytest tests/unit/test_agent_helpers.py tests/unit/test_operational_logging.py -q

### Phase 4: Targeted repair

1. Route between local repair, global regeneration, and terminal failure.
2. Implement the targeted repair schema and replacement logic.
3. Normalize repaired questions through the common boundary.
4. Revalidate and rejudge the complete assembled quiz.
5. Record repair history without logging quiz content.

Checkpoint:

    uv run pytest tests/unit tests/integration -q

### Phase 5: Regression evaluation

Create sanitized cases reproducing:

1. A Grade 2 language quiz with a negative question and answer-revealing
   question emojis.
2. A Grade 7 sequence quiz where selected answer, explanation, and shuffled
   index disagree.

Measure final acceptance, preservation of unaffected questions, absence of
question and option emojis, answer/index agreement after shuffling, grade and
language stability, model-call count, latency, and token usage.

For deterministic and mocked integration tests, require 100% correct index
derivation after shuffling, 100% emoji-free questions and options, 100%
preservation of unaffected questions, no quiz release without deterministic
validation and academic approval for the current quiz or trusted provenance of
a quiz that previously passed both gates, no public `correct_answer`, and
compatibility with existing persisted quizzes. For each production-derived
live scenario, run at least ten
repetitions and require at least nine successful completions out of ten. Any
released quiz with an incorrect answer or index remains a release blocker.
Existing quality metrics must not decline by more than 0.05 absolute points.
Targeted repair should use at least 20% fewer median tokens than full
regeneration, median latency should remain within 10% of baseline, and no
invocation may perform more than one academic repair.

Checkpoint:

    agents-cli eval generate
    agents-cli eval grade
    agents-cli eval compare BASELINE_RESULTS CANDIDATE_RESULTS

## 12. Minimum Test Coverage

1. A unique correct_answer produces the correct 0-based index.
2. Index derivation remains correct after shuffling.
3. Unicode and whitespace normalization find the intended answer.
4. Meaningful capitalization remains distinct.
5. Missing and ambiguous correct answers fail before the Judge.
6. correct_answer never appears in public or normal persistence payloads.
7. Every grade rejects question and option emojis.
8. Titles and explanations may still contain emojis.
9. Local Judge issues select targeted repair.
10. Global Judge issues select full regeneration.
11. Targeted repair changes only requested question indices.
12. Missing, duplicate, or out-of-range repair indices are rejected.
13. The assembled quiz is fully revalidated and rejudged.
14. Malformed Judge responses fail closed without content leakage.
15. Existing persisted quizzes containing correct_option_index still work.
16. Reinforcement shuffling preserves the answer/index relationship.

## 13. Acceptance Criteria

The change is ready for deployment consideration when:

- no LLM schema asks for correct_option_index;
- every generated or repaired question uses the common normalization boundary;
- the public API stays backward compatible;
- question and option emojis are rejected for every grade;
- local Judge failures repair only affected questions;
- full regeneration is reserved for global issues;
- both production-derived regression cases pass repeatedly;
- unit, integration, and evaluation checks pass;
- operational logs remain free of prompts, questions, options, explanations,
  and user/session identifiers.

## 14. Rollout Verification

Deploy only after explicit approval and normal pre-deployment testing. During
the first seven production days, compare these measures with the baseline:

- quality_failure count and rate;
- academic repair success rate;
- targeted versus full repair counts;
- normalization failure codes;
- median and high-percentile quiz latency;
- model calls and tokens per successful quiz;
- final invariant failures.

Disable targeted repair if it changes unaffected questions, leaks internal
fields, weakens fail-closed behavior, or increases terminal failures. A feature
flag is reasonable for targeted repair, while deterministic answer normalization
and the universal emoji prohibition should become mandatory boundaries after
validation.
