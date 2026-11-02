# Source-aware memory upgrade v2

Authorized 2026-10-28. Improve how the existing research agent uses saved evidence,
updates, corrections and task state. Preserve local Deep Agents/LangGraph,
configured Doubao Pro/Agent Plan, general terminal access and normal publication
checks. No new business tool, trading, data purchase, recurring task or Git push.

## Work

1. Correction-aware research recall and directly injected bounded process memory.
   Keep original outputs, source/claim-level notices, later correction clocks,
   replacement pointers and uncertain findings distinct from authenticated facts.
2. Source-span event cards and a versioned derived SQLite index, separate from
   the immutable library. Hybrid independent lexical/semantic candidate retrieval
   with optional local pretrained multilingual embeddings; no model training.
   Original text remains authoritative. Never infer event identity or cancellation
   from similarity, and never erase the other side of a conflict.
3. Structured task state: saved source observations, validation/review outcomes,
   unresolved gaps/conflicts, artifact pointers and publication state. Successful
   tool execution is not fact verification. Large outputs remain on disk and are
   progressively disclosed without dropping important rejection feedback.
4. Calibrated context accounting, incremental index refresh and explicit dirty
   states. Optional memory/model failure preserves source research and current
   lexical retrieval. Relevance remains the default until comparisons justify a
   more elaborate selector.

## Ownership

- memory_correction_integration: output recall part of knowledge.py,
  experience_memory.py and new memory_notices.py, dedicated tests/docs.
- memory_event_index: new event_memory_index.py/hybrid_memory.py, dedicated tests.
- memory_working_state: working_memory.py/research_state.py, dedicated tests.
- root: runtime/tools/SDK/API/context integration, local encoder/configuration,
  evaluation, source delivery, service acceptance and checkpoints.

## Acceptance

Freeze a small source-grounded diagnostic sequence before final tuning; separate
real captured sources from synthetic counterexamples and model labels from truth.
Compare current lexical pipeline, hybrid candidates, event-card retrieval and
working context with the same sources/model/budget. Include later corrections,
misleading dates, entity/network differences, conflicts, corrupt spans, malformed
results, failed optional dependencies and restore/idempotence. Keep inspected
prior benchmark results as development data, not a new held-out test.

Run targeted tests, then final regressions, actual local encoder retrieval and
source/clock checks. A bounded business Pro acceptance uses the same local
harness with at most 20 initial author responses, 2 source-review responses; one targeted continuation
of at most10 author responses is permitted only after a concrete reproduced
runtime defect is fixed and tested. Retain both jobs; no blind
repeated campaign. A scope-bounded independent model review may inspect saved
outputs only, not label the corpus as human ground truth. Preserve failed jobs,
unknown usage and abstention. No automatic complete claim from unit tests alone.

Back up mutable memory SQLite stores before integration; never change existing
frozen libraries or reports. Record each meaningful result/issue under
local/memory-upgrade/v2/ and in EXECUTION_STATE. Final delivery distinguishes
implemented contracts, measured diagnostic results, optional components and
remaining semantic/coverage limits.
