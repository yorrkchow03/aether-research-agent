# Research memory v2

The local Deep Agents/LangGraph business harness, Doubao Pro/Agent Plan and
source-checked publication remain unchanged. Memory does not place trades or
turn previous interpretations into financial facts.

## What changes during research

- Earlier report recall includes claim-level review notices, their dates and
  source-reviewed update links. Reader flags remain unverified; an AI review is
  attributed, not human truth. Original summaries and source IDs remain intact.
- Relevant source/process corrections are supplied directly to each author
  request. They no longer depend solely on the model opening a notes file.
  Experience retirement/replacement and programmatic paired-check utility are
  versioned; profitability or model confidence is not a validity reward.
- Task state retains original source pointers, attributed numeric/review checks,
  unresolved gaps/conflicts, publications and explicit unverified notes.
  Corrections remain visible even in a compact tool view.
- Large tool results have complete redacted artifacts and concise navigation.
  Explicit file reads expand those artifacts and are not recursively offloaded.
  The terminal SDK still receives full numeric input pages for computation.
- A separate SQLite source/card index stores exact source spans, dependencies
  and dirty derived summaries. It can be rebuilt from originals. Incremental
  capture indexes text without an LLM; similarity never confirms event identity.

## Queries and time

Use the existing `recall_memory` tool for `news`, `research`, `events` or `task`.
The terminal `ResearchClient` additionally supports `memory-state`, `memory-note`
and `memory-cards`; they do not add separate LLM business tools. Notes remain
unverified. Read originals before factual publication.

Observed queries reconstruct source states retrospectively. Strict queries
filter source acquisition, card/link clocks and output acquisition receipts.
An output's declared creation date alone is insufficient. Old imported output
history without acquisition receipts is excluded from strict recall; receipts
created on import are dated now and are not backdated. Later review/retirement/
utility feedback cannot change an earlier cutoff view.

## Optional local semantic candidates

Independent lexical and local-encoder candidates can be fused with reciprocal
rank scores and passed through the existing evidence budget/source checks.
This supplements full-library lexical retrieval over a **partial** source index.
A missing encoder or locked/corrupt optional index keeps lexical research usable
and records a gap. Pretrained vectors are retrieval hints, not source confidence.

The optional CPU package is separate from core installation:

```bash
.venv/bin/python -m pip install -c requirements.lock.txt -r requirements.memory.lock.txt
.venv/bin/python scripts/data/prepare_memory_encoder.py
```

The explicit preparation command downloads public data files at a pinned
repository revision; the runtime never downloads weights or needs an embedding
API key. `AETHER_MODEL_MANIFEST.json` binds all local payload hashes before use.
No model is trained. Network failure leaves the optional model unavailable,
not falsely enabled. The owner's fixed-revision multilingual model was completed on 2026-10-28
through validated mirror/CDN ranges and the official whole-weight SHA256. The
four runtime files total 530,983,537 bytes; local 256D encoding and the existing
frozen diagnostic pass. The running service now reports `local_pretrained_static`.
The selected six-month library now has 8,885 eligible captured original versions
in the derived source index and 24,640 exact overlapping semantic passages.
These are versions/posts/translations, not 8,885 independent articles or events.
The earlier 282 annotated source cards remain a separate partial navigation
layer. No general semantic improvement is claimed.

Manually index one bounded batch of already captured material:

```bash
.venv/bin/python scripts/data/build_memory_index.py --source ethereum --limit 20 --receipt local/memory-index-batch.json
```

Repeat with explicit source/offset and a new receipt to continue. This does not
fetch news, rewrite the library, create historical reports or schedule a job.
`GET /api/memory/status` shows partial index/model status; `GET /api/memory/cards`
returns exact source navigation. Existing frozen datasets remain independent.

For an explicit resumable import of the entire selected saved library:

```bash
.venv/bin/python scripts/data/index_memory_library.py \
  --library data/library-halfyear-20260426-20261026 \
  --batch local/memory-library-import --max-items 10000 --warm
```

Reusing that batch directory checks the same frozen inventory and resumes its
append-only journal. `--retry-errors` retries only recorded source failures.
A restored source index replays journal entries missing from the database.
Choose a new batch directory if source contents or the inventory changed.
This command reads already-saved originals, verifies raw hashes and builds a
derived index/cache; it does not download news, read provider keys or schedule
work. It imports only already-declared content-version links, dated now.
Version identity is not evidence that a publisher issued a correction.

Semantic retrieval ranks exact overlapping passages, then fuses source ranks
with lexical candidates. The evidence pack reopens originals and checks hinted
offsets/hashes before including source slices. Source eligibility precedes any
embedding. Bulk reads share database connections and hash a shared unchanged
raw object once per call; there is no persistent source-trust cache.
Current queries admit at most 10,000 source versions and 100,000 characters per
document; any truncation is disclosed. None of the current 8,885 versions hit
the character bound. New source capture is incrementally indexed; new passages
are encoded locally when required. `/api/memory/status` includes cache coverage.

## Costs and evaluation

Full-request context counts include tool schemas. A cached o200k tokenizer is
an offline **proxy**, calibrated against actual provider input usage, not the
exact Doubao tokenizer. Missing usage/cost is unknown. Native compaction retains
full history, while structured task state is independently reinjected.

See [evaluation protocol](../development/MEMORY_V2_EVALUATION.md). The 16-source,
8-query diagnostic has a ceiling: both lexical and unavailable-encoder paths
retrieve all required source spans. It is not evidence of semantic superiority.
Labels are AI-authored diagnostics, not human annotations. Separate sequence and
fault tests check future corrections, source clocks, invalidation retry, corrupt
receipts, artifact expansion and misleading terminal output.

The v2.1 whole-library SDK checks found the saved official Hoodi postponement
and its exact sentence for explicit English/Chinese entity queries, while a
broad Chinese question still returned unrelated material. Approximate local
navigation latency was 9–12 seconds after bulk validation, versus 36–39 seconds
with individual source reads. These are few diagnostic runs, not a statistical
benchmark or a measured improvement in generated-report accuracy. Prefer a
clear entity/event query and verify originals; the agent may choose an English
query independently when that matches its sources.

The research directions draw on [APEX-MEM](https://aclanthology.org/2026.acl-long.749/),
[Memora](https://arxiv.org/abs/2602.03315),
[ACE](https://arxiv.org/abs/2510.04618), and
[Anthropic's context engineering report](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents).
This is a scoped adaptation, not a reproduction of all their models or scores.
