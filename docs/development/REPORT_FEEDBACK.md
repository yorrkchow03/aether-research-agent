# Report feedback and correction review

`ReportFeedbackStore` stores attributed feedback without editing an existing
report. It performs no network requests, model calls, automatic report generation,
or trading actions. An initial complaint is not accepted as a correction or added
to research experience merely because a user submitted it.

## Construction and integration

```python
from research_agent.report_feedback import ReportFeedbackStore

feedback = ReportFeedbackStore(
    state_dir / 'report-feedback',
    report_lookup=knowledge.report,
    evidence_lookup=lookup_saved_record,
    redact=redact_text,
    experience=experience_memory,  # optional ExperienceMemory instance
)
```

`root` is the private **store directory**, not the project root. It contains
`report-feedback.sqlite` and content-hashed `receipts/` copies. The application must
supply a lookup that can resolve saved evidence across sessions, including derived
calculations and task-parameter records. A lookup returns the actual record dict
with its original `record_id`, not an API wrapper or an approximate replacement.
`report_lookup` returns the immutable body returned by `KnowledgeStore.report`.

The permitted source-receipt locations are anchored to the source package's project
root, independent of the selected store directory. They are:

- `local/report-quality or local/memory-upgrade/**`
- `runtime/sessions/<session_id>/workspace/reviews/**`

Only bounded UTF-8 `.md`, `.json`, and `.txt` files are accepted. JSON must remain
parseable after redaction. Configuration, credential, `.env`, and secret paths are
rejected. Resolved symlinks must remain inside a permitted directory. Copies are
redacted, hashed, and retained separately; later edits to the submitted receipt do
not alter its saved copy. The application should pass its configured redactor;
the module also scrubs common provider-key formats.

## Calls and returned fields

```python
item = feedback.submit(
    report_id, 'This observation may use an outdated announcement.',
    claim_id='O1', category='outdated', actor='user',
)
items = feedback.list_for_report(report_id, cutoff='2026-10-28T00:00:00Z')
checked = feedback.review(
    item['feedback_id'], reviewer_kind='independent_model',
    receipt_path=project_root / 'local/report-quality/review.json',
    evidence_ids=['page-actual-saved-id'], assessment='needs_correction',
    correction='Use the revised announcement and explain the changed status.',
)
resolved = feedback.resolve(item['feedback_id'], replacement_report_id)
```

The example IDs are placeholders; every report, target, receipt, and evidence ID
must actually exist. Submission accepts `outdated`, `source`, `calculation`,
`unclear`, or `other`. Its default target is summary `S1`, category `other`, and
actor `user`. It rejects unknown report IDs or claim IDs from `review_targets`.
The report and target content hashes bind the feedback to that exact saved version.
Repeated identical submissions are idempotent.

`submit`, `review`, and `resolve` return a plain JSON-compatible feedback snapshot
with `feedback_id`, `report_id`, `claim_id`, `report_sha256`, `target_sha256`,
`target`, `note`, `category`, `actor`, `known_at`, `status`, `events`, `reviews`,
`latest_review`, `resolution`, `experience`, `last_event_id`, `last_known_at`, and
`scope`. Those mutating methods also return a `duplicate` flag. `list_for_report`
returns a list of snapshots. Invalid requests raise `ValueError` or a normal typed
validation error; the HTTP adapter is responsible for translating errors.

The state meanings are deliberately limited:

| State | Meaning |
| --- | --- |
| `unverified` | A saved complaint with no review finding yet. |
| `reviewed` | A finding with receipt and real evidence exists; it is not automatically resolved or guaranteed correct. |
| `resolved` | An accepted correction finding is linked to a distinct, already source-reviewed replacement report. The original remains available. |

Reviewers are labelled `independent_model`, `programmatic`, or `user`; assessments
are `needs_correction`, `needs_more_evidence`, or `not_reproduced`. A correction
assessment requires nonblank correction text. All reviews require actual evidence
IDs, whose returned record IDs and hashes are saved alongside the receipt hash.
An AI finding is explicitly not labelled human review or independent truth.

## Resolution gate

A complaint or empty comment cannot be resolved on its own. The latest review
must require a correction and include correction text, an intact saved receipt,
and unchanged, resolvable evidence. The original must still match its bound hash.
The replacement must be distinct, real, known by the resolution time, and cite
every correction-review evidence ID.

The module re-runs the report's structural/numeric validation and source-review
quote checks. A saved `status='reviewed'` flag alone is insufficient. Every current
draft target must have a source-review verdict; quotes must be exact excerpts of
that target's eligible records, and no verdict can still require revision.

When a real saved review includes `targets`, they must match re-created draft
targets field by field. The only allowed addition is the runtime reviewer's
task-context evidence on a target with kind `summary` or `judgment`. Such extra IDs
must resolve to `context-*` records in `local.user_research_parameters`, with a
real profile and acquisition no later than the saved review. Other claims cannot
gain arbitrary new sources through the saved-target metadata. If the review also
saved `evidence_text`, it must match serialization of the actual records before
quote validation is repeated. This preserves compatibility with actual
`EvidenceReviewer` behavior without trusting an altered target or prose snapshot.
Published report bundles can omit the full `targets` and `evidence_text` payload.
For those reports, the module reconstructs only the permitted summary/judgment
context additions from their saved quote IDs and verifies those actual context
records before repeating quote checks. It does not broaden factual targets or
accept arbitrary extra news/data sources from a quote.

Resolution appends a new event containing `replacement_report_id`, its hash,
`review_event_id`, `evidence_ids`, `source_review_checked`, and `known_at`. Repeating
the same resolution is idempotent. It cannot silently point to another replacement
or reopen resolved feedback.

## Append-only history, cutoff, and experience

Feedback and events are inserted into separate SQLite tables. Update/delete
triggers prevent accidental mutation. Snapshot state is reconstructed from eligible
events; a cutoff requires an explicit timezone. Feedback, review, resolution, and
experience delivery are all filtered by their actual `known_at` time. A later
review or resolution cannot appear in a historical snapshot, including at
subsecond boundaries. Receipt capture time is not backdated to an article's date.

A source-backed `needs_correction` finding can be sent to the optional
`ExperienceMemory.record_correction` as `kind='source_correction'`, with the saved
receipt, evidence IDs, affected report, and a bounded applicability note. It
remains source-linked research-process guidance, not a learned market law.
Inconclusive findings and initial complaints are not added. The review event is
committed first. A failed experience write creates an `experience_failed` event
without losing the review; a successful write records its correction ID. Repeating
an identical review does not duplicate experience. This module does not schedule
retries, generate corrected reports, or call a reviewer model itself.

## Verification

`tests/test_report_feedback.py` uses local synthetic records, a real
`KnowledgeStore`, and local receipt files. It covers unknown targets, empty notes,
idempotence, append-only guards, redaction, receipt escape attempts, missing or
future evidence, strict historical snapshots, insufficient review, source-reviewed
replacement requirements, target/context integrity, unchanged originals,
tampering, and successful/failing experience writes. One test exercises the real
`ExperienceMemory` callback and recall. No test calls a model or data provider.

Test logs and a machine-readable receipt are kept in
`local/report-quality/v1/feedback/`. They establish storage, provenance, and gate
behavior; they are not a human adjudication of a financial claim. Source truth and
the semantic merit of a proposed correction still require careful review. UI,
authentication, HTTP endpoints, reviewer execution, and replacement generation are
provided by the application integration.
