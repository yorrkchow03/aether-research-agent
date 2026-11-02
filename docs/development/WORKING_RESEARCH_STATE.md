# Working research state and progressive tool views

This optional layer is task-scoped derived navigation, separate from immutable
source records, conversation checkpoints and long-term financial knowledge. It
uses ordinary Python/SQLite, without a model call or a new business tool.

## Host integration

The runtime tracer should call this on the **actual** tool result, including
errors. Use the actual call ID when available; SDK host operations can use the
`sdk:operation-name` role, while the Python SDK still receives its full result.

```python
working = WorkingMemory(task_memory_root, redact=settings.redact,
                        evidence_lookup=source_lookup)
working.begin(job_id, prompt, cutoff, mode=mode)
receipt = working.observe_tool(
    job_id, tool_name, actual_payload,
    status='success', tool_call_id=actual_call_id,
)
```

The result includes `observation_id`, `artifact`, `research_state_revision`,
`derived_counts` and `duplicate`. Duplicate same-call/same-body delivery does not
advance state. Reusing a call ID with different content raises a visible error;
the redacted raw artifact remains available. A new tool call ID is a new observed
attempt, even if its source content matches an earlier result. SQLite serializes
concurrent updates. Older task databases acquire the new table lazily and older
tasks gain structured state on their next observation; they are not relabelled
as verified history.

`observe_tool` is an internal host API, not a mutation endpoint for an LLM to
supply a made-up successful publication. Source/schema gates remain the authority
for admissible report evidence. Do not route arbitrary model-authored JSON as a
`publish_report` host result.

## State meanings

`research_state` keeps these separate:

- `sources`: source IDs, source clocks, numeric/unit previews and the artifact
  that supplied them. `candidate`, `opened` and `registered_derived` describe
  access/registration, not truth. Collections are one reproducible input pointer,
  not hundreds of automatically verified observations.
- `checks`: attributed numeric/source-contract and source-review results,
  including target IDs, status and actual quoted source IDs. A source-support
  review is an AI judgment, not proof of the publisher's truth or a good formula.
- `unknowns`: failed access, unavailable data, missing context, and unresolved
  source-review issues. Failure is never converted to a zero or a false event.
- `conflicts`: both evidence IDs, relation, basis and unresolved status. A
  model-suggested relation remains an unverified candidate.
- `publications`: the saved path and source-review status from an actual host
  publication receipt. A later save does not erase earlier failed attempts.
- `derived_notes`: explicit model/process notes, always marked unverified.
- `observations`: IDs, tool/call identity, payload hash, artifact and local
  `known_at`, preserving the chronology of received results.

Only recognized top-level host envelopes are interpreted. Page text, embedded
`facts` or fake `review` objects inside a source record are not read as state
instructions. Console output cannot impersonate a successful publication or an
accepted source review. The reducer does not extract factual propositions by
matching words in source text. It does not automatically settle an unresolved
conflict or establish that a later review fixed a different draft.

The reducer has bounded previews of result/gap/conflict lists and scalar metadata.
An overflow entry names the full artifact and omitted count. Full redacted input
is retained before any interpretation; omitted details are not treated as absent.
Numeric field and unit strings are not rounded or cut apart.

## Explicit derived notes

A host SDK adapter may expose the existing Python service for notes, without
adding another LLM business tool:

```python
working.add_note(job_id, 'Check whether the candidate updates the same event.',
                 evidence_ids=[real_source_id], kind='next_check')
```

Allowed kinds are `derived_claim`, `question`, and `next_check`. A derived claim
needs 1–20 syntactically valid evidence IDs. If `evidence_lookup` is configured,
each is reopened; otherwise `references_checked=False` stays explicit. Even a
successful source lookup never changes `unverified_derived_note` into a fact.
Notes are idempotent by content, source list, role and origin.

## Model-context view

`context(job_id, budget_chars=...)` puts unresolved errors, review issues and
conflicts ahead of long plans and todos. It never cuts through a whole conflict
pair, number/unit or review condition. When a field does not fit, it says the
view is incomplete and supplies a full state revision pointer. The JSON result
also supplies `state_file`, `state_sha256`, `omitted_fields`,
`field_truncations` and count metadata. Each full JSON revision is separately
saved under `states/<job_id>/revision-N.json`; existing revision content cannot
silently change. The SQLite state remains canonical. The context cost remains a
labelled UTF-8 heuristic; provider token counting belongs in the runtime.

## Progressive disclosure

```python
view = working.progressive_tool_view(
    job_id, tool_name, actual_payload, status='success', max_chars=6000,
)
```

This returns a **string** for model-facing `ToolMessage.content`. Preserve its
original ID/name/artifact/protocol fields. Do not replace the Python SDK return
value, numeric recipe inputs, original evidence or saved conversation with this
view. Small results, errors, source-review results and publication results are
returned complete after configured redaction; protected results may exceed the
soft target. Large source/console results get a source/clock/value/unit metadata
view, a clearly labelled partial preview and exact redacted artifact pointer.
All conflict/gap/review metadata is protected; if this alone exceeds the target,
`protected_metadata_exceeds_target=True` is explicit instead of dropping it.
A reader must reopen the original record/artifact before citing omitted material.

The saved artifact is the complete result **after configured redaction**. It is
not a claim that credentials were retained losslessly. The process uses the real
local filesystem, not an OS security sandbox.

## Validation

`test_research_state.py` and `test_working_memory_v2.py` cover injected page or
console review claims, source clocks/units, collection granularity, unknown/failure
states, conflict-pair retention, positive/negative review scopes, same-call
idempotency, concurrent replay, derived note semantics, full artifact recovery,
protected review output, state snapshots and migration compatibility. Existing
working-memory/runtime contracts remain separately exercised.

A small private saved-output replay reads two actual source records and one
previously published real report from its package. It replays envelopes offline,
preserves all input hashes and rechecks that no market facts were invented.
This is not another business agent run, a source-truth audit or a full-corpus
semantic benchmark. Receipts stay outside the public source export.
