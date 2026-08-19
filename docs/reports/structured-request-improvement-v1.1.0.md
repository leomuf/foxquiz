# Structured-Request Improvement Measurement (v1.1.0)

## Decision

Accept the structured-request enforcement for its deterministic contract,
safety, and call-removal benefits. Do not count it toward the open 25% total
token-reduction milestone: its measured direct saving is only 0.34% of the
version 1.0.0 baseline, and the deployed five-case comparison was dominated by
generator and Judge retry variance.

The 45-case rollout was intentionally not run. The five-case expansion gate did
not establish a material total-token improvement, and a full cohort could not
plausibly turn the known 0.34% stage-removal floor into a 25% reduction.

## Scope and provenance

- Runtime candidate: commit `e4ff39f4140568a7037fb9bbbd3782e8be5a24e4`
- Campaign date: 2026-08-19
- Deployment: new random-name temporary public DEV service
- Runtime: 1 CPU, 4 GiB memory, concurrency 8, zero minimum instances, two
  maximum instances, generation 1 execution environment, startup CPU boost
- Persistence: isolated DEV Firestore database
- A2A and prompt-response span capture: disabled
- Cleanup: the exact temporary service was deleted after the final security
  case; the unrelated existing DEV service remained present

No project ID, project number, service name, or service URL is retained in this
report.

## Deployment verification

The deployed revision reported the exact candidate commit through `/version`.
The root page and ADK session creation returned HTTP 200, while the disabled A2A
agent-card route returned HTTP 404. Runtime identity, resource limits,
concurrency, scaling, Firestore database selection, and feature flags matched
the campaign configuration.

`/apps/app/app-info` returned the known HTTP 400 response for a Workflow root.
All evaluation datasets carried truthful seeded Workflow metadata, so this did
not prevent trace generation or grading.

Pre-public verification also caught two deployment metadata variables inherited
from an earlier ignored `.env` file. They were corrected to the new temporary
service before public invocation, and the resulting revision was re-verified.

## Structured-request contract results

The safe contract suite ran at concurrency 1 and covered free-form, malformed,
incomplete, clarification-required, and clarification-follow-up requests.

| Result | Value |
|---|---:|
| Cases | 5 |
| Valid deterministic grades | 5 |
| Errors | 0 |
| Mean score | 1.0 |
| `parameter_extractor` calls | 0 |
| `mascot_prompt` calls | 0 |

The three invalid inputs were rejected before producing any LLM token event.
The clarification-only invocation used 1,893 total tokens, and the successful
clarification follow-up used 15,159 total tokens. Neither path invoked the
extractor or mascot fallback.

The first live grade exposed that the evaluator accepted only the bare fixed
invalid-request message, while production emits the same message inside an
`INVALID_REQUEST` security envelope. The metric now accepts both exact forms
and rejects envelopes with another block type. Grading the unchanged live
traces improved the result from 2/5 to the correct 5/5.

## Five-case quality and token pilot

The existing token-observability pilot ran at concurrency 2. It contains three
initial requests and two adaptive requests.

| Quality metric | Valid cases | Errors | Result |
|---|---:|---:|---:|
| Quiz structure validity | 5/5 | 0 | mean 1.0 |
| Quiz request fulfillment | 5/5 | 0 | mean 5.0 |

Provider-reported token usage was:

| Stage | Calls | Prompt | Candidate | Thinking | Total | Retry calls | Retry tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| `security_classifier` | 5 | 2,115 | 5 | 1,088 | 3,208 | 0 | 0 |
| `curriculum_evaluator` | 5 | 3,706 | 1,329 | 5,184 | 10,219 | 0 | 0 |
| `quiz_generator` | 8 | 11,812 | 17,367 | 20,924 | 50,103 | 3 | 18,657 |
| `academic_judge` | 6 | 17,672 | 1,413 | 20,176 | 39,261 | 2 | 12,064 |
| **Total** | **24** | **35,305** | **20,114** | **47,372** | **102,791** | **5** | **30,721** |

All five invocations succeeded. Their average was 20,558.20 total tokens; the
minimum was 8,941, median 22,806, p95 25,978, and maximum 25,978.

The earlier same-pilot run on revision `f7ed101` used 70,118 tokens. The new raw
total is 32,673 tokens (46.60%) higher, but this is not attributable to the
structured-request change: the candidate run spent 30,721 tokens on five
generator/Judge retry calls. Removing those retry calls leaves 72,070 tokens,
only 1,952 (2.78%) above the earlier raw pilot. Model output, thinking, and
retry behavior are nondeterministic, so neither difference is a causal estimate
of the request-contract change.

## Direct before/after bound

The immutable version 1.0.0 50-invocation baseline used 793,123 total tokens.
Its five natural-language initial requests each invoked the parameter extractor:

| Removed stage | Calls | Prompt | Candidate | Thinking | Total |
|---|---:|---:|---:|---:|---:|
| `parameter_extractor` | 5 | 1,839 | 450 | 379 | 2,668 |

The deployed structured pilot recorded zero extractor calls. Holding downstream
calls constant, converting those five requests therefore removes 2,668 tokens,
or 533.6 tokens per converted request. This is 0.34% of baseline tokens and 5
of 209 baseline model calls (2.39%). The baseline had no mascot calls, so it
provides no measurable mascot-removal saving.

## Security result

The isolated malicious case ran last at concurrency 1. Its first version used a
free-form injection and was rejected as `INVALID_REQUEST`, which meant it did
not exercise semantic security. The dataset was corrected to place the same
injection inside a schema-valid structured request, with regression coverage
that preserves this property.

The corrected live case reached the security classifier, returned
`block_type: MALICIOUS`, and scored 1/1 with zero grading errors. It consumed
441 provider-reported tokens in one security-classifier call and terminated as
blocked. No generator, Judge, extractor, or mascot call occurred.

## Conclusion

Structured requests are worth retaining: they provide a strict machine-readable
contract, reject unsupported input before model work, preserve quiz quality,
retain semantic security for valid requests, and deterministically remove one
extractor call from each converted initial request. They are not a major token
optimization. The open 25% milestone must target the dominant generator and
academic-Judge stages, especially first-pass success and thinking-token usage.
