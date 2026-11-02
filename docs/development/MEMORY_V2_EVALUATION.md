# Memory v2 diagnostic evaluation

The evaluation freezes a small saved-source corpus before comparing retrieval.
It separates source/span retrieval, timestamp/notice contracts, controlled
synthetic streams, independent review and live business acceptance. It does not
run a provider LLM, download models, generate reports or measure investment value.

## Freeze and replay

With the owner's saved library and prior correction receipts installed:

```bash
python scripts/evaluation/evaluate_memory_v2.py --freeze
python scripts/evaluation/evaluate_memory_v2.py --run-name keyword-control
python scripts/evaluation/evaluate_memory_v2.py \
  --encoder-path local/models/potion-multilingual-128M --run-name local-encoder
```

Freezing requires a new output directory. It stores source copies, exact required
passages, eight query specifications, fixed protocol parameters and four actual
saved feedback episodes. `MANIFEST.json` and `MANIFEST.sha256` bind all inputs.
Replay rejects any changed frozen file or required source span. Existing run
artifacts are never overwritten. Provider credentials are neither read nor used.
An absent encoder produces `dense_not_available`; a configured encoder that fails
at query time produces `dense_unavailable`. Local weights are loaded through the
existing verified-directory adapter; this script never downloads an alternative
or claims that keyword fallback used semantic retrieval.

## Real-source scope

The private default freeze has sixteen captured publisher/forum documents and
eight AI-authored exact-span questions. Documents cover Ethereum Foundation,
Bitcoin Core, Aave governance, Lido discussion and Federal Reserve proposals.
One irrelevant short forum reply is retained as real noise. Source selection used
bounded source/topic discovery before the policy comparison. This is a diagnostic
corpus, not an independent random sample of the entire six-month archive.

The Hoodi and zkAPI query families are explicitly development/previously viewed.
New Bitcoin, Aave, regulatory and forum questions are labelled new AI-authored
diagnostics, not untouched held-out data or human ground truth. Full source text
and its source metadata are frozen; only exact-span presence is scored. Finding
that passage does not prove a generated answer or the publisher's claim is true.

A particularly important limitation is preserved: the saved October16 Hoodi
announcement capture already contains its October22 update. It is not an actual
uncorrected early snapshot. The evaluation does not backdate that capture or
pretend an article's publication date proves system knowledge then. Clean
original-then-correction histories are labelled controlled synthetic streams.

## Matched retrieval conditions

Both policies use actual `ResearchTools.news_pack`, the same isolated
`KnowledgeStore`, frozen originals, result cap4, context budget3600 and cutoff.
The lexical condition uses the existing BM25/TF-IDF evidence path without a local
retriever. The hybrid condition adds the independent indexed keyword/dense RRF
candidate path and the host-only `hybrid_relevance` score when dense results are
available. Jev is disabled for this diagnostic to avoid an extra judging variable.

Event/source cards are created uniformly as non-overlapping1800character windows,
without answer-aware anchors or query-specific event keys. Card results record
exact original offsets/hashes and their actual construction time. They remain
newly derived navigation, not evidence that those cards existed historically.
The strict card query before construction therefore correctly returns zero.

Per-query output retains policy order, rank/source IDs, score metadata, selected
exact spans, gaps, context byte estimates and latency. Required sources and exact
passages share the query denominator even when a policy returns nothing or errors.
Candidate metadata is read after timing the actual pack call, so that inspecting
rankings does not prewarm the timed first dense query. Cache/encoder status is
retained. Single-pass small-corpus latency is diagnostic, not a reliable throughput
benchmark. UTF-8 size/context cost is labelled as an estimate, not provider tokens.
Each current run records public implementation hashes and whether files changed
during its execution.

Four before-acquisition probes run under both policies. They test exclusion of a
specific target before its actual acquisition timestamp; they are not an answer
abstention score. Four actual saved feedback episodes test that later review and
resolution do not leak into earlier report recall, original summaries remain
unchanged and available update pointers appear later. Two feedback records refer
to original/offline-replay reports from the same authoring group: four episodes
must not be described as four independent report samples.

## Controlled synthetic cases

A separate denominator covers eight contracts:

1. Explicitly supplied entity keys distinguish the same name on different chains.
2. An original/declared correction stream retains both sides and marks a derived
   summary stale after the recorded update, without rewriting originals.
3. A source published earlier but captured later stays out of strict recall.
4. Future report-review notices do not affect earlier recall.
5. Repeated identical cards do not inflate the index.
6. Damaged source/span bindings fail closed.
7. Console JSON cannot impersonate an accepted source review or publication.
8. A failed tool followed by a successful retry remains recoverable and idempotent.

These contracts do not establish automatic entity resolution or semantic conflict
classification accuracy. They test how explicitly supplied metadata is handled.

## Interpretation and outstanding acceptance

The first no-encoder diagnostic completed all16query-policy outputs: both paths
found required sources/spans for8/8queries. All8before-acquisition probes,
4actual correction episodes and8synthetic contracts passed. Dense was absent;
these results show no semantic improvement. They also expose a ceiling in this
small source/query selection. A successful later local-encoder run must preserve
this baseline and frozen inputs; no harder questions or tuned labels are inserted
after inspecting the result.

`acceptance_status.json` initially keeps live Pro and independent review pending.
Only the parent may replace those fields with actual artifacts. Rule tests,
exact-span retrieval and optional independent model judgments remain distinct
from independent human semantic labels, long-horizon report quality, general
retrieval gains and financial outcomes. Future tuning needs a genuinely new
confirmation set; the current diagnostic must not be renamed held-out accuracy.
