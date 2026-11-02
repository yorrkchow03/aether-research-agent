# Investor research report contract

A report first explains what happened, what the available evidence supports, and
what remains uncertain. It can help an investor make their own assessment without
forcing the agent to issue an investment judgment, forecast, or buying suggestion.
This contract changes the report structure and presentation; it does not establish
predictive accuracy or investment performance.

## Content and backward compatibility

`ReportDraft` retains the existing saved fields and adds these optional fields:

| Field | Default | Meaning |
| --- | --- | --- |
| `judgment` | `informational` | A source-grounded research brief with no forced investment view. Existing `consider`, `wait`, `avoid`, and `insufficient_evidence` values remain valid. |
| `judgment_reason` | Empty string | Optional for an informational brief; nonblank for every explicit judgment. Directional judgments also require a cited interpretation. |
| `key_changes` | Empty list; at most 6 claims | Important recent changes supported by saved records. A change must compare actual observations or documented event states, not an invented previous report. |
| `questions_to_watch` | Empty list; at most 6 nonblank strings | Open questions and proposed future checks, clearly separate from confirmed developments. |

Existing `observations`, `interpretations`, `forecasts`, `limitations`,
`purchase_decision`, and `advice` fields keep their names and semantics. A report
still needs at least one sourced observation and an explicit limitation. Forecasts,
decision context, and advice remain optional. Older saved JSON loads without
requiring the new fields and without rewriting the saved artifact.

`report_claims(draft) -> list[Claim]` returns observations, key changes, then
interpretations. Publication, review, and audit callers should use it rather than
assuming observations and interpretations are the only claim collections.

## Source checks and review integration

`validate_report` sends every claim, including a key change, through the existing
record lookup and exact numeric-field validation. Giving a report an informational
judgment does not relax missing-record, value, unit, advice, or time checks.
No scale conversion is applied to a claim's saved `value` or `unit`.

The publication/review integration must separately treat key changes as factual
claims and follow-up questions as proposed `research_action` items. A neutral
report with an empty reason has no judgment statement to review. All evidence IDs
from claims and optional forecasts, decisions, or advice must remain in the audit
and downloadable bundle, including contrary evidence and simulation records.

The structural checks do not establish the truth of every sentence or whether a
source supports a comparison. That remains a separate source-support review. In
particular, a follow-up question must not be used to hide a new factual assertion,
unsupported trading threshold, or promised outcome. An AI source review is always
labelled as such; it is not described as human inspection.

## Reading order

The Markdown reader shows only populated optional sections:

1. Title and summary.
2. Recent key changes, when available.
3. Main facts, then interpretation when available.
4. Open questions to follow up, when available.
5. Conditional scenarios, judgment, decision context, or advice only if supplied.
6. Limits and unknowns.
7. Publisher links with capture times and numbered links to saved evidence.

Source review verdicts, structured numeric-check counts, and archive cutoff details
remain under **Source checks and technical provenance**. A missing review or count
is labelled missing, never silently treated as passed or zero. The archive cutoff,
publication/event times, and later retrieval times remain distinct.

For a numeric claim with unit `fraction`, the renderer can additionally show an
explicitly approximate percentage. Its exact metric/value/unit remain inspectable
in a per-claim detail block; the underlying claim and numeric gate are unchanged.
Source list deduplication compares the full receipt, preserving differing source
versions or capture times. Numbered links retain exact evidence IDs.

## Verification and limits

The new `tests/test_investor_reports.py` covers neutral publication through the
real publisher with local fixtures and no model call, new-claim rejection for bad
values/units/sources, existing exact-field checks, explicit-judgment requirements,
legacy loading, optional sections, source links, and follow-up question framing.
Existing advice and cold-replay regressions are also run.

Local compatibility evidence is saved under
`local/report-quality/v1/report-format/`. Three existing reports are loaded,
validated against their original audit records, and rendered to separate examples.
The saved evidence and numeric-check counts match the original validation results,
and SHA-256 checks confirm the original JSON and Markdown were not changed. The
examples retain the original AI review; they do not claim a new model generation,
source fetch, or human quality review. The copied examples adapt source links to
the original local archive for inspection.

These checks verify the contract and presentation. They do not show that new
reports are well written, that every paragraph is semantically supported, or that
conditional investment advice is appropriate. New live reports, UI integration,
independent quality review, and frozen evaluation are separate acceptance work.
Runtime examples and absolute local archive paths are local evidence, not a
portable public dataset or a replacement for an exported audit bundle.
