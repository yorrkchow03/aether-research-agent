# Memory v2.1: saved-library coverage and passage retrieval

This continuation preserves Deep Agents/LangGraph, Doubao Pro/Agent Plan,
12 business tools, four skills and all source/numeric publication checks.
The optional static multilingual encoder runs on local CPU. No training,
new model/provider request, paid-data purchase, schedule, trade or Git push.

## Implementation

- Semantic retrieval uses overlapping 1,400-character original passages with
  a 1,200-character stride. The best passage ranks each source. Fused candidates
  carry exact source offsets and hashes into evidence packing, which reopens
  originals and revalidates every hint. Similarity does not certify support.
- Local encoder calls are bounded to128passages. Cache identity includes actual
  model, preprocessing, source content and passage construction/coverage bound.
  A full lexical pool no longer discards all independent semantic candidates;
  known conflict/update dependencies remain together at the candidate cap.
- Manual source import freezes an inventory, journals each version, resumes
  interruptions and replays entries missing from a restored database. A failed
  source cannot stop independent sources. No source or frozen dataset is edited.
- Same-document lookup is indexed. Fresh bulk canonical reads share SQLite
  connections and deduplicate hashing of unchanged shared raw files within a
  call. No persistent trust cache or skipped raw verification is introduced.
  Current library exclusions are checked before embedding; a corrupted primary
  capture cannot silently fall through to a fallback copy.
- Existing content-revision links are imported at their actual annotation time.
  Similarity and metadata-only revisions are not promoted to correction links.
  Source/card/link acquisition checks still precede ranking under strict mode.
- Research guidance lets the agent choose clearer entity/source-language
  recall queries and re-read original context. It does not force event names,
  fixed indicator recipes, translations or an additional business tool.

## Actual saved-data result

The fixed selected half-year archive contains8,944saved document versions.
The manual inventory indexes8,885eligible originals and separately records59
excluded versions, spanning9configured source families. Those counts include
forum posts, translations and content versions; they are not independent news
events or complete global news coverage. Numeric contracts and known data
permission gaps are unchanged.

The local representation cache contains24,640exact passages over all8,885
eligible versions. None reaches the100,000character per-document bound.
Queries cap source versions at10,000and report any future cap. Existing282
annotated cards remain a separate partial layer.16existing content-version
links were imported without gaps; two catalog links have excluded endpoints
and were not imported.

## Verification and limits

- Existing frozen16source/8query diagnostic: both lexical and passage hybrid
  retain8/8required source spans;8strict probes,4actual notice episodes and
  8synthetic contracts pass. Its existing ceiling is not semantic superiority.
- Actual full-library SDK/evidence-pack checks found the required official
  Hoodi postponement and its exact sentence under an explicit English query
  and a Chinese query retaining the entity name. Every returned excerpt was
  reconstructed from its reopened source offsets and verified against SHA256.
  Original evidence was read via the SDK; a pre-acquisition strict query
  excluded the later-captured target. Semantic service sidecars were off.
- A broad Chinese query returned unrelated results. This failure is retained;
  no query-specific thresholds or automatic event-name assumptions were added.
  Labels/examples are AI-authored diagnostics, not independent human truth.
- Individual whole-library source reads took36–39seconds. Fresh bulk reads
  yielded one cold11.314second query; SDK navigation took11.942and8.747seconds.
  Few local runs include different warm/cold states and are not a statistical
  throughput comparison or proof of generated-report improvement.
- The563test main run passed561; two SDK socket tests were blocked by sandbox
  permissions. Their existing six-test harness module passed after permitting
  local sockets. The exported source final suite is checked separately.
- Independent SQLite backup/restore reproduced source/card/link/vector counts
  and8exact-card reads. Monthly and half-year frozen manifest/database hashes
  match their prior records. Original report contents and source bundles remain
  authoritative; no new business report or human review was generated this round.

Private receipts are under `local/memory-upgrade/v2_1/`; they retain initial
latency, failures, full SDK packs, original-source hashes and restore results.
Public delivery excludes models, corpus, private receipts and credentials.
