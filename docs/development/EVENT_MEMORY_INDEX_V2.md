# Source-span event index and hybrid memory retrieval

This optional, rebuildable SQLite index holds navigation over public saved
sources. It does not alter raw files, canonical evidence IDs, report drafts,
source review or publication. It performs no model call, network request,
automatic fact extraction or automatic event equivalence.

## Source/card operations

```python
from research_agent.event_memory_index import EventMemoryIndex

index = EventMemoryIndex(state_dir / 'event-memory', source_lookup=knowledge.get)
index.upsert_source(actual_evidence_id)
card = index.record_card(
    actual_evidence_id, start=actual_start, end=actual_end,
    entity_keys=['ethereum:protocol'], event_key='protocol:event-candidate',
)
packet = index.query('activation', asset='ETH', limit=8)
original_span = index.read_card(card['card_id'])
```

The IDs and offsets must resolve to actual saved evidence. An index source ID
cannot be rebound to different content; supplied rows are reopened through
`source_lookup` and compared. Private/context records and explicitly quarantined
records are rejected. The configured lookup should also enforce any
application-specific library quarantine policy.

Cards bind the source snapshot/text hashes, an exact character span and its hash,
and every source/card dependency hash. A card is a source claim/navigation aid,
not a new factual evidence record. Parent code can split exact paragraphs
without claiming it has extracted verified atomic facts. Reopen the original
record before citing it. Entity/event keys are explicit bounded metadata;
shared keys or semantic similarity never confirm the same event, contradiction
or cancellation. Missing ETH/BTC asset tags can use a labelled lexical navigation
hint, which is not a verified classification.

`record_card` optionally accepts a `summary` and `depends_on` IDs. Summaries are
unverified derived text. Actual host `known_at` timestamps cannot be supplied by
the caller to backdate a card. `event_time`, `valid_from`, and `valid_to` are
separate source-claimed annotations; the return value explains their relation to
the query cutoff and never treats a future schedule as occurrence. Historical
cards remain available; an expired claimed interval is not silently deleted.

A different later-acquired version of the same document marks only dependent
summaries dirty. A late import of an older capture does not reverse version
order. Explicit source links also invalidate affected summaries locally.
Invalidation propagates through dependent cards, and late backfilled summaries
cannot become fresh after an update was already indexed. Original spans and old
summary content remain unchanged. A refreshed summary explicitly depending on the
replacement and created after that update was known can remain fresh navigation;
it is still semantically unverified. A new later relationship can make it dirty
again. A dirty summary is labelled `stale` and must
not stand in for a current conclusion; use original evidence instead.

```python
index.link_sources(
    newer_source_id, earlier_source_id, 'related_candidate',
    receipt_id=actual_saved_source_receipt_id,
)
```

Every link needs a resolvable source receipt/hash. Its meaning remains an
attributed annotation, not an independently established fact. Supported relations
include `related_candidate`, `same_event_candidate`, `corrects`, `supersedes`,
`content_revision_of`, `contradicts` and `unresolved_conflict`. A source naming a
postponed date does not establish cancellation of another date.

## Independent keyword and vector retrieval

```python
from research_agent.hybrid_memory import HybridMemoryRetriever

retriever = HybridMemoryRetriever(
    index, encoder=local_encoder, encoder_id=actual_model_revision,
    cache_fingerprint=actual_preprocessing_and_weight_fingerprint,
)
packet = retriever.query('recent protocol changes', asset='ETH', limit=8)
```

The optional encoder exposes `encode(list[str]) -> finite matrix`. The parent
loads its chosen pretrained local model; this module does not download weights,
construct a provider, train anything or assume a model is installed. With no
encoder or a failed encoder, the return value explicitly says `keyword_only` and
retains a gap. Synthetic test vectors demonstrate the retrieval plumbing only.

The vector branch independently ranks all eligible indexed sources, including
sources missed by keyword search. It is not a rerank of the BM25 shortlist.
Keyword and vector ranks combine with reciprocal rank fusion. Complete explicit
conflict/update groups are selected together or left out with an explicit gap
when they exceed the result limit. Candidate links do not create these hard
constraints. Source similarity does not establish source independence or support.

Embeddings are cached in the same SQLite database, keyed by encoder/preprocessing
namespace, source ID and exact encoded-content hash. Vector JSON has an integrity
hash. The caller is responsible for accurate model/weight identity. Query vectors
are encoded separately, document vectors are reusable, and neither is fitted to
corpus statistics. The default representation is the title and first24,000
characters; truncation is counted. A source later than the cutoff cannot enter
the encoder's candidate input or keyword corpus.

Queries enumerate at most2,000 eligible indexed sources by default, configurable
up to10,000, and expose any cap. This is coverage of the installed derived index,
not coverage of the owner's full archive or all relevant news. Explicit entity
filters are bounded to30 matching cards and expose that cap. Existing sources
outside the index remain accessible through ordinary research tools.

## Availability and integrity

`mode='strict'` filters acquisition clocks before lexical corpus construction
and ranking. Source acquisition, publication, card creation, relationship creation
and invalidation clocks remain distinct. Later card/relationship annotations do
not become past knowledge; their future IDs are not disclosed in strict results.
`mode='observed'` allows retrospective source captures and labels their clocks;
it does not authenticate what the system knew when the event occurred.

Every source/card read reopens and verifies the saved original. Corrupted vectors
cannot silently alter retrieval; the vector branch reports unavailable and the
keyword path can continue. Corrupt or missing originals are excluded with an
explicit gap, not reconstructed from a cached summary. Concurrent duplicate
imports/cards are idempotent and preserve the first immutable content binding.

## Verification scope

The focused tests cover duplicate/concurrent writes, source/receipt/version/span
bindings, private/quarantined sources, unknown clocks, strict future exclusion,
local summary invalidation, dependency propagation, independent dense candidate
retrieval, model/content cache binding, fallback and conflict-pair preservation.
They do not prove semantic effectiveness or market truth.

The private two-record read-only probe under `local/memory-upgrade/v2/index/`
checks saved Hoodi original snapshots and raw hashes. Explicit candidate keys
retain both documents. Plain BM25 `Hoodi` misses the older roadmap because its
captured text joins adjacent words. This is retained as a real retrieval
limitation, not counted as an automatic semantic improvement. No encoder/provider
is used by that probe; the parent runs any real pretrained-model comparison.
