# Quiz Quality Analysis Script — Implementation Plan

## 1. Goal

Create a reusable, read-only command-line script that summarizes FoxQuiz's
privacy-minimized deterministic validation events from Google Cloud Logging.
The default report covers the previous seven days and helps maintainers assess
how reliably generated quiz candidates satisfy the deterministic quality rules.

The script must not deploy infrastructure, create log-based metrics, use
Terraform, modify Cloud Logging, or write quiz content to disk.

## 2. Proposed Deliverables

- `scripts/analyze_quiz_quality.py`: production-quality Python CLI.
- `tests/unit/test_quiz_quality_analysis.py`: credential-free calculation,
  parsing, privacy, and error-handling tests.
- A short `CONTRIBUTING.md` section describing prerequisites and example usage.
- Optional sanitized JSON fixtures under `tests/fixtures/` if inline test data
  becomes difficult to read.

Python is preferred over Bash because percentage calculations, JSON parsing,
validation, unit testing, and cross-platform behavior are more reliable. The
script will be launched through `uv`, so no new runtime dependency should be
needed.

## 3. Existing Event Contract

The script will read entries whose `jsonPayload.phase` is
`deterministic_quiz_validation` and recognize these event names:

| Event | Meaning |
|---|---|
| `quiz_validation_passed` | The first generated candidate passed deterministic validation. |
| `quiz_validation_failed` | The first candidate failed deterministic validation and another generation attempt was allowed. |
| `quiz_validation_retry_passed` | The second generated candidate passed deterministic validation. |
| `quiz_validation_retry_exhausted` | The second candidate failed deterministic validation and the quiz was blocked. |
| `quiz_final_invariant_failed` | The final defensive validation failed before browser output. |

Permitted fields are limited to:

- `event`;
- `phase`;
- `generation_attempt`;
- `issue_count`;
- `issue_codes`;
- `service_version`;
- `deployment_revision`;
- Cloud Logging metadata such as timestamp and Cloud Run revision.

The script must ignore unexpected fields and must never print or persist an
entire log entry.

## 4. Important Interpretation Boundaries

The report measures deterministic candidate validation, not complete end-user
quiz success. The LLM judge, curriculum preflight, security checkpoint, network
errors, and frontend completion are separate stages.

A second generation attempt can be caused by either deterministic validation or
the LLM judge. Therefore, `quiz_validation_retry_passed` must be described as a
"second-attempt deterministic pass," not necessarily as recovery from a prior
deterministic failure.

The current events intentionally contain no request or session identifier. The
script can calculate aggregate event statistics but cannot reliably reconstruct
individual request timelines. It must not attempt to correlate entries using
quiz text or user information.

`issue_codes` contains unique categories affecting a candidate. Counting these
codes reports the number of validation events affected by each category, not
the total number of individual occurrences. `issue_count` remains the total
number of issues found in the candidate.

Events close to the selected time-window boundary may represent only part of a
multi-attempt request. The report should state its exact UTC start and end times
so this limitation is visible.

## 5. Command-Line Interface

Initial interface:

```bash
uv run python scripts/analyze_quiz_quality.py \
  --project <YOUR_PROJECT_ID>
```

Supported options:

- `--project PROJECT_ID`: required Google Cloud project ID. Do not hard-code it.
- `--days DAYS`: positive integer lookback period; default `7`.
- `--service SERVICE`: Cloud Run service name; default `foxquiz`.
- `--limit LIMIT`: maximum number of entries; default `100000`.
- `--output text|json`: output format; default `text`.
- `--group-by-revision`: additionally show outcome counts per deployed commit.

Possible later options, after the base version is stable:

- explicit `--start` and `--end` UTC timestamps;
- `--version` or `--revision` filters;
- CSV output;
- thresholds suitable for CI or monitoring automation.

The script must use the user's existing `gcloud` authentication. It must not
read, create, or store service-account keys or other Google credentials.

## 6. Data Acquisition

Invoke `gcloud logging read` through `subprocess.run` with an argument list and
without `shell=True`. This avoids shell-injection and quoting problems.

The filter should constrain all of the following:

- `resource.type="cloud_run_revision"`;
- `resource.labels.service_name="<service>"`;
- `jsonPayload.phase="deterministic_quiz_validation"`;
- the selected UTC time window.

Request JSON output and parse it in memory. Check the subprocess return code,
surface a concise error, and never echo raw stderr if it might contain
unexpected data. Distinguish these cases:

- `gcloud` is not installed;
- no active authentication or insufficient log-reading permission;
- project or service is invalid;
- the query returns no matching entries;
- the configured limit may have truncated the result;
- malformed or unknown log entries are skipped.

Unknown event names should be counted separately and reported as a compatibility
warning rather than causing the whole report to fail.

## 7. Calculations

For the selected period, calculate:

### Outcome counts

- first-attempt passes;
- first-attempt deterministic failures;
- second-attempt passes;
- exhausted second attempts;
- final invariant failures;
- unknown validation events.

### Rates

Use zero-safe division and display both numerator/denominator and percentage:

- **First-attempt pass rate** = `quiz_validation_passed` divided by
  (`quiz_validation_passed` + `quiz_validation_failed`).
- **First-attempt deterministic failure rate** = `quiz_validation_failed`
  divided by the same first-attempt total.
- **Second-attempt deterministic pass rate** =
  `quiz_validation_retry_passed` divided by
  (`quiz_validation_retry_passed` + `quiz_validation_retry_exhausted`).
- **Second-attempt exhaustion rate** = `quiz_validation_retry_exhausted`
  divided by the same second-attempt total.

Do not label event count as "number of successfully created quizzes." The event
contract does not prove that the LLM judge and all later workflow stages also
succeeded.

### Issue statistics

- affected-event count per `issue_code`, sorted descending;
- total `issue_count` across failed validation events;
- mean issues per failed validation event;
- maximum issue count in one event;
- malformed issue-code and issue-count field counts.

### Revision comparison

When `--group-by-revision` is enabled, show each deployed commit with its event
counts and first-attempt pass rate. Missing revisions should appear as
`unknown`, not be discarded.

## 8. Text Report

The default human-readable output should be compact and explicit, for example:

```text
FoxQuiz deterministic validation — last 7 days
Project: example-project | Service: foxquiz
Window (UTC): 2026-08-06T12:00:00Z to 2026-08-13T12:00:00Z

First attempts:                897
  Passed:                     845 (94.20%)
  Deterministic failures:      52 ( 5.80%)

Second attempts:               52
  Passed validation:           47 (90.38%)
  Retry exhausted:              5 ( 9.62%)

Final invariant failures:       0

Most frequent issue categories (affected events):
  emoji_in_option              31
  duplicate_option             14
  invalid_correct_index         7
```

If no entries exist, return a successful, clearly labeled empty report rather
than treating normal absence of traffic as an error.

## 9. JSON Report

`--output json` should return a stable, versioned schema suitable for later
automation. Include:

- `schema_version`;
- project, service, UTC start/end, and requested limit;
- total entries processed and entries skipped;
- event counts;
- rate objects containing numerator, denominator, and percentage;
- issue statistics;
- optional revision groups;
- warnings such as unknown events or possible truncation.

Do not include raw Cloud Logging entries in the JSON output.

## 10. Privacy and Security Requirements

- Use an allowlist when extracting fields from Cloud Logging.
- Never output prompts, grade, subject, topic, quiz titles, questions, options,
  explanations, model responses, IP information, or client/session identifiers.
- Never dump malformed raw entries for debugging.
- Never execute a shell command constructed from CLI input.
- Do not create local cache files by default.
- If future file export is added, write only the aggregated report.
- Keep the script strictly read-only with respect to Google Cloud.

## 11. Test Plan

All unit tests must run without Google credentials and without contacting Google
Cloud. Mock `subprocess.run` or separate acquisition from pure parsing and
calculation functions.

Minimum test cases:

1. Empty result produces a valid zero-value report.
2. Known events are counted correctly.
3. Percentages and zero denominators are handled correctly.
4. Second-attempt statistics are not mislabeled as complete quiz success.
5. Duplicate issue codes inside one event are counted once per affected event.
6. `issue_count` totals and averages are correct.
7. Unknown event names produce a warning and separate count.
8. Malformed entries are skipped and counted without exposing their content.
9. Missing revision becomes `unknown`.
10. Revision grouping is correct.
11. JSON output follows the declared schema and contains no raw entries.
12. Sentinel quiz text placed in unexpected input fields never appears in text
    or JSON reports.
13. User-provided CLI values are passed as subprocess arguments, never through a
    shell.
14. Missing `gcloud`, authentication failure, permission failure, and nonzero
    subprocess exit codes produce concise actionable errors.
15. Invalid values for `--days` and `--limit` are rejected before querying.

## 12. Implementation Sequence

### Phase 1: Pure analysis core

- Define recognized events and an aggregated report data model.
- Implement allowlisted parsing and calculations as pure functions.
- Add unit tests for counts, rates, malformed entries, and privacy sentinels.

### Phase 2: Google Cloud Logging adapter

- Build the precise log filter and UTC window.
- Invoke `gcloud logging read` safely.
- Add mocked tests for command arguments and failure handling.

### Phase 3: CLI and output

- Add argument validation.
- Implement text and versioned JSON renderers.
- Add revision grouping and truncation/compatibility warnings.

### Phase 4: Documentation and verification

- Add concise usage instructions to `CONTRIBUTING.md`.
- Cross-reference the existing manual `gcloud logging read` commands.
- Run credential-free unit, integration, and browser tests.
- Run `agents-cli lint` while leaving unrelated untracked files untouched.
- Perform one local manual invocation against a real project using the
  maintainer's existing credentials; do not store credentials or output data in
  the repository.

## 13. Acceptance Criteria

The work is complete when:

- the default command analyzes the previous seven days;
- it reads only the intended FoxQuiz Cloud Run validation events;
- all calculations are reproducible and correctly labeled;
- no quiz or user content can appear in reports;
- text and JSON modes work with empty and populated results;
- all automated tests run without Google credentials;
- a real read-only local invocation succeeds with maintainer credentials;
- contributor documentation explains usage and interpretation limitations;
- no Terraform, log-based metric, deployment, or Google Cloud mutation is
  introduced.

## 14. Optional Future Enhancements

These are intentionally outside the first implementation:

- scheduled reports;
- Cloud Monitoring dashboards or alerts;
- manually provisioned log-based metrics;
- trend charts and comparisons between releases;
- a privacy-safe workflow correlation mechanism, which would require a separate
  design and privacy review before changing the current event contract.
