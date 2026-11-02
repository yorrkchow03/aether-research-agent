# Correction-aware recall and versioned experience

This extension joins existing report feedback into subsequent research recall.
It keeps original reports, citations, source records and review receipts intact.
The notices remain attributed findings; a user flag is not a fact and an AI
review is not human verification. No model training, network calls or business
tools are introduced by these modules.

## Report recall

`KnowledgeStore.search_reports(query='', limit=5, cutoff=None)` and
`search_research(query='', limit=8, cutoff=None)` retain their existing call forms.
They filter the actual saved output creation time before selecting results. A
cutoff is also passed to library-output queries before their limit, followed by
an additional host filter. Saved output creation time does not authenticate
historical public availability or an unknown import/acquisition time.

Bind the existing local store in the runtime:

```python
knowledge.report_feedback = feedback_store
# Alternative: knowledge.notice_provider = callable(report_id, cutoff=...)
```

Returned report rows preserve `summary` and add:

- `source_evidence_ids` and `original_source_evidence_ids`, including key-change
  citations, excluding reviewer-only quotations and private user-context IDs;
- `memory_notices`, with affected claim ID, attributed reviewer kind,
  assessment, correction preview, its actual review time, evidence pointers and
  receipt hash;
- `memory_status`, `next_report_ids` and a bounded `notice_context` suitable for
  a reminder in the author's context;
- `unreviewed_concern_count`, without turning the user's raw allegation into a
  research instruction or factual correction.

The join reconstructs eligible review/resolve events rather than trusting a
callback's current `latest_review` or status. The original report-body hash must
match its feedback binding. A replacement pointer is exposed only after the
resolution's actual known time; when its body is accessible the replacement hash
and creation time are rechecked. Individual claims remain affected rather than
invalidating a whole report. An optional notice lookup failure preserves the
original research row and marks the lookup unavailable. Large notice/correction
previews explicitly say they are incomplete; read the full saved review before
applying omitted conditions.

The caller must keep returned `notice_context` / `memory_notices` with the old
summary. Merely adding database fields does not ensure an author attends to them.
The host handles runtime prompt/SDK integration. This module does not erase old
source material, prove that a newer source is true, or globally invalidate every
answer sharing one citation. Strict source eligibility remains separately checked
when original evidence is read or published.

## Experience lifecycle

`ExperienceMemory.recall(...)` remains compatible and returns a bounded
`prompt_context`. The host can now inject that context directly, rather than
relying exclusively on an agent choosing to open a notes file.

Original `corrections` entries stay immutable. Lifecycle changes use the new
append-only event table:

```python
store.record_lifecycle(
    correction_id,
    status='superseded',          # active / retired / invalidated / superseded
    reason='Reviewed replacement preserves the missing applicability condition.',
    receipts=[saved_review_path],
    replacement_id=new_correction_id,
)
```

Create the revised correction through `record_correction` first, then link it.
No original entry or receipt is overwritten. A distinct, existing, already-known
replacement and a real saved text receipt are required; replacement cycles are
rejected. Retired, invalidated and superseded entries are omitted from current
recall, with an inspectable exclusion reason. An `active` event can reinstate an
entry without deleting its prior lifecycle history. Cutoff queries filter events
before they alter eligibility, corpus statistics or ranking. A corrupt lifecycle
receipt quarantines its target instead of silently reviving an old experience.

## Conservative usefulness feedback

This optional API is for the host's evaluator, not a model-exposed tool:

```python
store.record_outcome(correction_id, paired_check_path, check_id='window-check')
```

The actual saved JSON must use this shape:

```json
{
  "schema": "aether-workflow-memory-feedback-v1",
  "verification": "programmatic_paired_check",
  "correction_id": "an-existing-process-correction-id",
  "task_id": "a-frozen-task-id",
  "check_id": "window-check",
  "check_type": "numeric_units",
  "with_memory_passed": true,
  "without_memory_passed": false
}
```

Allowed checks concern numeric units, source binding, time cutoffs, recipe replay,
task completion or condition preservation. Real optional evidence IDs require a
resolver. Helpful/harmful/neutral is derived from the paired booleans; success
in both arms is neutral. Financial return, popularity, confidence, a raw AI
rating and an unpaired successful task are not accepted reward types. These
receipts are attributed evaluation records, not cryptographic proof that the
checker or a causal attribution was correct.

Only `process` corrections receive usefulness adjustments; source corrections
are never made more or less true by such scores. For each task/check pair, only
the latest eligible feedback counts. The smoothed utility is
`(helpful - harmful) / (4 + paired_checks)`. It can adjust a nonzero lexical
relevance score by at most 15%, and cannot make an unrelated memory relevant.
`recall(..., utility_weight=0)` disables that adjustment. No verified outcomes
means the original TF-IDF ordering remains. No broad quality gain is claimed.

## Verification

The dedicated suites cover real feedback-store joins with synthetic reports,
cutoff propagation, future callback leakage, original hashes, preview disclosure,
source IDs, lifecycle history, replacement cycles, receipt corruption,
paired-feedback validation and bounded usefulness. Existing research, desk,
experience and feedback tests remain applicable.

Private `local/memory-upgrade/v2/corrections/` contains a separate read-only join
of four existing production review notices across three old reports, reading the
three originals and their one source-reviewed update. Two old notes expose the
newer update link; the two inconclusive month-report claims remain open. This
propagates existing model findings; it does not create human truth labels or
reassess financial accuracy. The parent performs full runtime, publication and
end-to-end acceptance.
