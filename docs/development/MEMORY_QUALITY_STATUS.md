# Memory and context quality handoff

Implemented and verified on 2026-10-27. This lane changes evidence recall and task context; it does not replace the existing Deep Agents harness, source lookup, arithmetic checks, or publication review. The base deterministic diagnostic is pinned in `local/halfyear/memory/pilot-v4/`; the integration follow-up below covers the subsequent compatibility fix.

## Components and integration

| Component | Persistent contents | Main entry points |
| --- | --- | --- |
| `KnowledgeStore` and attached `ResearchLibrary` | Original evidence IDs, raw hashes, article versions, reviewed event revisions, prior reports and answers | `candidate_news(...)`, `get(record_id)`, existing event/report methods |
| `EvidenceMemory` | No second source of truth; returns a task-specific pack and judgment receipts for the caller to save | `pack(...)` |
| `WorkingMemory` | Current goal/cutoff, plan, todos, evidence-linked notes, unknowns, conflicts, active artifacts, redacted full tool outputs | `begin`, `update`, `record_tool`, `context`, `read_artifact` |
| `ExperienceMemory` | Immutable snapshots of actual review/failure receipts and applicable process corrections | `record_correction`, `recall`, `seed_project_corrections` |

Caller examples:

```python
evidence = EvidenceMemory(knowledge, judge=judgment_service.assess)
packet = evidence.pack(
    query="Hoodi upgrade activation status", asset="ETH", cutoff=cutoff,
    mode="observed", budget=3200, limit=5, semantic_mode="shadow",
    original_only=True, material_only=True,
)
# Model-visible tool results should omit packet["prompt_context"] when they
# already include results: it is a second short presentation of those snippets.

working = WorkingMemory(task_root, redact=settings.redact)
working.begin(job_id, goal, cutoff, plan=plan, mode="observed")
working.update(job_id, todos=todos, unknowns=unknowns,
               conflicts=conflicts, active_artifacts=artifact_ids)
receipt = working.record_tool(job_id, tool_name, output)  # str or JSON
short_state = working.context(job_id, budget_chars=3000)["text"]

experience = ExperienceMemory(experience_root, redact=settings.redact,
                              evidence_lookup=knowledge.get)
seed_receipt = seed_project_corrections(project_root, experience)
process_context = experience.recall(query, cutoff=known_by)["prompt_context"]
```

All constructors avoid model/network calls. A judgment callable is explicitly injected; no provider is selected implicitly. The host must validate a public question before forwarding it to a semantic provider; public source filtering alone does not make a personal query public. Parent-owned `ResearchTools` supplies that boundary. `context` returns a dictionary with `text`, character count, truncation metadata and a labelled token estimate, not a string.

## Evidence and time behavior

- Recall filters source availability before BM25/TF-IDF corpus statistics, ranking and candidate caps. A later acquired article cannot alter a strict historical ranking. The attached library's instance window is used, allowing monthly or half-year stores without changing monthly constants.
- `strict` means acquired by the cutoff, with no later publication. `observed` permits retrospective reconstruction from currently acquired versions whose publication is within the research cutoff; it must not be represented as the system's earlier knowledge.
- Eligible latest versions are selected within the cutoff. Explicit reviewed event updates expand required source dependencies rather than erasing the earlier source. The data lane separately fixed library latest-version publication filtering.
- A supplied relation is acquired metadata too: strict relations require a valid `known_at` at or before the cutoff. Future relation endpoints are excluded before endpoint lookup or prompt generation.
- Selected excerpts are exact source slices, not model summaries. Offsets, slice hashes, excerpt hashes, truncation flags, original ID, URL and source metadata remain attached. Hash/read failures produce a gap; they cannot silently fall back to an old search snippet.
- Explicit unresolved conflicts are a protected pair: include both sides and their required corrections, or return an explicit context gap when they cannot fit. A model suggestion remains unverified metadata. It cannot silently delete or merge original records.

`pack` retains the existing `results`/`selection` shape and accepts existing `original_only`/`material_only` filters, or a caller-supplied frozen candidate pool. Retrieval and selection are bounded at 120 recalled sources (150 explicit sources maximum); normal model context is much smaller. A short prompt view has a hard character bound and gap counts; full structured gaps and source IDs remain available.

## Selection and semantic rollout

For a nonempty query the default remains deterministic relevance selection. Available comparison policies are relevance, MMR, facility coverage, recency, and an explicitly enabled semantic coverage variant. The semantic variant combines lexical relevance with valid returned relevance scores and preserves dependency constraints; its weighting is an experimental choice, not a performance guarantee.

The default semantic mode is `shadow`. It records bounded relevance/pair judgments without changing selected IDs. `apply` is opt-in; unavailable, abstained or errored judgments retain deterministic behavior and an explicit gap. Only `status=ok` is usable. Different known event IDs or disjoint asset scopes are not submitted as likely conflict pairs. Even an `ok` label is not proof that an event occurred or that a claim is financially correct.

## Working state and correction experience

Tool output is stored as a redacted, hash-verified, content-addressed artifact; identical outputs deduplicate while retaining tool provenance. Neither a tool result nor an evidence-looking ID is automatically promoted into a verified fact. Fact notes require source references and still need normal source review. Updates are transactional; restart and concurrent identical-output tests pass.

The bounded working view includes active artifacts, todos, unresolved questions and conflicts. Per-field or total omissions are marked. `compact_messages` provides an optional protocol-preserving model view with whole assistant/tool groups and lossless archived originals; it does not modify checkpoints. The parent's official Deep Agents summarization integration is a separate mechanism and requires its own combined acceptance evidence.

Five real saved failures were imported successfully, each with review/source receipt snapshots:

1. Terminal-relative versus virtual file-tool paths and recipe paths.
2. A prose year that disagreed with the original observation year.
3. Relative benchmark return versus percentage-point subtraction.
4. Thirty closes versus thirty close-to-close return intervals.
5. Prospective monitoring/invalidation conditions misread as already occurred facts.

These are research-process guidance, not learned market laws or invented user preferences. Receipt bytes/hashes are checked, originals can change without overwriting the saved receipt, tampering excludes the experience, and acquisition time is never backdated. Missing seed receipts are returned as gaps.

## Verification and actual diagnostic results

`local/halfyear/memory/FINAL_TESTS.json` records **53 tests passed**, zero failures/errors/skips, covering memory, knowledge, research recall and working state. Tests include strict time filtering before semantic calls/statistics, future relation hiding, exact late source passages, source tampering, provider failure fallback, both-or-neither conflicts, shadow invariance, private source exclusion, idempotence, restart and artifact recovery.

The final pilot freezes three previously inspected questions and actual monthly-v4 candidate sources, dataset manifest hash, five module hashes and source snapshots. Each question uses the same candidate pool, budget 3,200 estimated units and limit five across comparable policies. The prefix-only ablation deliberately changes source chunking. There are 24 question/policy/time-mode comparisons, **zero model calls and zero data network calls**.

| Variant | Required source selected | Required exact passage selected | Mean short context characters | Mean estimated cost | Mean local selection time |
| --- | ---: | ---: | ---: | ---: | ---: |
| Relevance | 3/3 | 3/3 | 4,839 | 3,100 | 0.115 s |
| MMR | 2/3 | 2/3 | 4,692 | 3,047 | 0.118 s |
| Facility coverage | 1/3 | 1/3 | 4,451 | 3,078 | 0.114 s |
| Prefix-only + MMR ablation | 2/3 | 1/3 | 5,399 | 2,895 | 0.120 s |

All strict variants select zero future captures. Backfilled sources are correctly unavailable in strict mode, rather than receiving credit as historical knowledge. The results support keeping relevance as the default; coverage did **not** outperform it. They do not establish generalization or semantic/report quality: this is a tiny previously inspected diagnostic, not an independent held-out test. Human semantic adjudication remains pending. Jev-versus-Pro accuracy/latency/cost is measured by the separate Jev lane, not this deterministic run.

The saved real candidate output is 1,100,890 bytes; task context is 572 characters, with full artifact hash recovery verified and no automatically created facts. Source-slice metadata no longer duplicates slice text. Costs use serialized UTF-8 bytes/3, explicitly an estimate rather than provider tokens or billing.

`pilot-v4/REPLAY.json` confirms an offline replay of all 24 comparisons reproduces exact source IDs, passage flags, character counts, cost estimates, gaps and strict-time results; variable wall-clock timing is excluded. `pilot-v1` retains the initial diagnostic and matching source bytes; v2 is a prepared intermediate freeze and v3 predates the final active-artifact view fix. None is relabelled as final evidence.

Reproduce the final version while its pinned source files are unchanged:

```bash
.venv/bin/python scripts/evaluation/verify_memory_quality.py replay \
  --output local/halfyear/memory/pilot-v4
```

After changing these modules, prepare a new named output before running it; never overwrite a frozen candidate pool or pretend that an old receipt validates new code. Parent-owned real Pro report, UI and full-suite acceptance remain separate final integration gates.

## Integration follow-up

The parent requested an isolated review of the current runtime integration after the diagnostic freeze. Three actual incompatibilities were identified and fixed:

- The legacy `KnowledgeStore.select_news` fallback lacked `material_only`, and always applied strict acquisition filtering even when the host used observed mode. It now accepts keyword-only `material_only=False, mode='strict'`; existing callers retain their strict default. Both candidate retrieval and selection use the selected mode. The parent passes the host mode from the tool fallback.
- Optional working-store construction previously failed the whole business task. The parent added a recorded memory-unavailable fallback; ordinary source research can continue.
- Strict memory could recall an acquired historical public source that `ResearchTools.lookup` then rejected solely because its ID was in persistent current knowledge. The parent added actual acquisition/publication/observation cutoff checks while retaining the historical no-live restriction.

`tests/test_memory_runtime_contract.py` passes **8 isolated tests**, recorded in `local/halfyear/memory/runtime-contract/TESTS.json`. Tests use temporary stores and a synthetic chat model; no real model or network requests occur. They cover the three fixes, source-time boundary negatives, per-call working context without system-prompt accumulation, and the actual Deep Agents graph with native summarization.

The graph test proves that the custom middleware replaces the default summarizer in its original slot, `{messages}` is expanded, and one summary and one ordinary model generation are distinct. Summary usage has separate events. The main model receives the shorter view, while all 26 original/result messages and a history file remain. Rebuilding the graph/model and reopening the SQLite saver then accepts a continuation and retains all 28 messages, including the earliest original.

The knowledge compatibility change intentionally changes its source hash after `pilot-v4` was frozen. The v4 figures apply to its retained source snapshot; the replay command against newer working code correctly refuses a hash mismatch. The new eight-test receipt validates the integration change. The earlier pilot was not overwritten, relabelled, or rerun as if it were evidence for new code. Current combined business/report acceptance remains the parent's responsibility.

## Bounded collection-source verification

`collection_sources.py` adds `read_collection_sources(library, record_ids, ...)` and `validate_collection_sources(library, saved_rows, dataset_id=..., start=..., end=..., cutoff=..., mode=...)`. Both return `records` in the requested order and a separate `validation` receipt. The latter also compares every saved row field exactly and checks its dataset/query interval. It does not replace collection identity, count or chronology checks performed by the caller.

For a real selected `ResearchLibrary` or router primary, the reader opens one read-only connection and explicitly begins a SQLite snapshot before chunked `IN` queries. It reads the requested observation IDs rather than asking for the newest observation at the same timestamp. Each distinct capture and raw object is verified during that invocation: capture identity/status, source hash and metadata, object path containment, byte count and raw SHA-256, normalized observation identity/content hash, dataset contract, acquisition and strict time bounds. Any quarantined member rejects the batch. Object file signatures are checked again before return to detect changes during validation. No verification result is cached across submissions. Unknown adapters use their existing `evidence()` boundary safely.

`tests/test_memory_batch_lineage.py` passes **10 tests** including a 4,392-row collection, exact old/new versions, quarantine, source/normalized/capture tampering, raw changes after an earlier successful call, path escape, strict acquisition/publication/observation bounds, fake adapters and read snapshot consistency during a later quarantine write. The next invocation sees that new quarantine and rejects it.

Actual read-only validation of the two formal-window Bitstamp hourly pages verified **4,392 ETH + 4,392 BTC observations**. ETH took 0.126 seconds and BTC 0.104 seconds on the shared host; each used one snapshot, 21 SQL statements and ten verified capture/raw objects. One hundred deterministic evenly spaced records per asset were compared with the old `library.evidence()` interface: all 200 complete dictionaries matched. The old-interface sample took about 0.14 seconds per 100 records; a full old-interface run was not timed, and these wall times are informational rather than an isolated benchmark claim.

Receipts: `local/halfyear/memory/batch-lineage/TESTS.json` and `ACTUAL_RECEIPT.json`. No source data was written or copied, and no model/network request occurred. Parent-owned caller caching must remain bounded to one review/submission and revalidate on the next submission. This optimization cannot reconstruct an exclusion relation previously deleted by a later discovery reconciliation; preservation of that historical journal is a separate data-layer issue.
