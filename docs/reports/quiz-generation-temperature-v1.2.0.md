# Quiz-Generation Temperature Evaluation for v1.2.0

Date: 2026-08-19

Release baseline: `159648e`

Decision: use temperature `0.6` for full quiz generation

## Objective

Determine whether reducing only the full quiz generator temperature from `0.7`
to `0.6` lowers total token usage and retry frequency without reducing quiz
delivery or measured quality. The model, prompts, schemas, validation rules,
repair budgets, targeted-repair temperature, and academic Judge were unchanged.

Temperature `0.8` was also screened once. It was not advanced because it used
more tokens, caused more academic retries, and produced a hard-fractions quiz
with an incorrect answer index that the independent evaluation Judge detected.

## Method

The evaluation used a local FoxQuiz server with mocked persistence and live
Vertex model calls. Cases ran sequentially with concurrency `1`. Each output
was graded using the deterministic `quiz_structure_validity` metric and the
LLM-based `quiz_request_fulfillment` metric.

The initial screen contained five cases:

- German Grade-5 water cycle;
- English Grade-8 linear equations;
- Portuguese Grade-10 Mendelian inheritance;
- German hard Grade-10 Redox reactions; and
- Portuguese hard Grade-5 fractions.

The confirmation phase added two repetitions at both temperatures for the
three historically sensitive cases: Mendelian inheritance, Redox reactions,
and fractions. Including the corresponding screen results produced three
observations per sensitive case and eleven observations per temperature across
the complete campaign.

Generated traces and grade artifacts remain ignored local evidence under
`artifacts/`; they are not release inputs.

## Initial Five-Case Screen

| Measure | `0.7` control | `0.6` candidate |
|---|---:|---:|
| Delivered quizzes | 5/5 | 5/5 |
| Structure-validity mean | 1.0 | 1.0 |
| Request-fulfillment mean | 5.0 | 5.0 |
| Cases with an academic retry | 2/5 | 0/5 |
| Total tokens | 115,445 | 79,641 |

The candidate reduced measured tokens by 31.0% in the screen while preserving
delivery and both quality scores. The control retried Redox and fractions; the
candidate delivered all five quizzes on the first generation.

## Three-Repetition Sensitive Cohort

| Measure | `0.7` control | `0.6` candidate |
|---|---:|---:|
| Delivered quizzes | 8/9 | 8/9 |
| Structure-validity mean | 0.889 | 0.889 |
| Request-fulfillment mean | 4.556 | 4.556 |
| Cases requiring any retry | 4/9 | 1/9 |
| Total tokens | 248,845 | 175,716 |
| Tokens per delivered quiz | 31,106 | 21,965 |

Both temperatures failed closed on one hard-fractions observation and received
identical independent grades. Temperature `0.6` reduced total tokens and tokens
per delivered quiz by 29.4% while requiring three fewer retry paths.

Token totals by sensitive case show where the difference occurred:

| Case, three repetitions | `0.7` tokens | `0.6` tokens |
|---|---:|---:|
| Mendelian inheritance | 47,896 | 45,410 |
| Redox reactions | 102,462 | 58,587 |
| Hard fractions | 98,487 | 71,719 |
| **Total** | **248,845** | **175,716** |

## Complete Campaign Result

Across all eleven observations per temperature, including the two stable
controls, each arm delivered 10/11 quizzes with identical aggregate quality.
The `0.7` arm used 278,519 tokens and the `0.6` arm used 201,885 tokens, a
27.5% reduction.

This is a small, deliberately focused stochastic evaluation rather than a
population estimate. Nevertheless, the candidate cleared the planned 25%
total-token reduction gate in both the sensitive cohort and the complete
campaign without a measured quality or delivery regression. The evidence also
shows a consistent operational mechanism: fewer expensive generation and
Judge retries, especially for hard Redox quizzes.

## Decision and Rollout Constraint

Set only the full quiz generator temperature to `0.6`. Retain the mascot and
targeted-repair temperatures, models, prompts, schemas, validators, retry
budgets, and academic Judge unchanged.

Before production deployment, run the normal unit and integration suites. A
future deployed cohort should remain grouped by deployment revision and should
verify delivery, independent quality, retry rate, and provider-reported tokens.
If deployed quality or delivery regresses, restore the `0.7` generator
temperature independently of the other v1.2.0 improvements.
