# Jev evidence pipeline status — 2026-10-27

Internal components and a bounded real-source pilot are verified. Parent owns
application integration and final whole-product acceptance. This lane made no
changes to the business tool list or mandatory publication gate.

## Available API

```python
from research_agent.evidence_judgments import EvidenceJudgmentService
service = EvidenceJudgmentService(project_root, data_dir=private_sidecar_dir,
                                  provider="jev")
result = service.assess("classification", question="", evidence=[source_row],
                        criterion_version="v1")
```

Tasks are classification,relevance,relation,support. Classification asks asset,
principal topic and explicit event status together. Relevance retains directly
contrary/corrective evidence as relevant and records source-instruction risk.
Relation distinguishes duplicate,supersedes,update,contradicts,different_scope,
unrelated,insufficient; exactly two documents are required. Support distinguishes
supported,contradicted,insufficient and explicitly prospective conditional claims.

Only status=ok has a usable label. Abstained/unavailable/error never means false
or irrelevant. Even an ok judgment does not prove truth,calibration or causation.
Raw-source hashes,actual judged snippet/query/criterion hash,actual/requested
model,versions,probabilities,confidence,latency,usage and attempts are retained.
Originals are never rewritten. Explicit time/math/hash checks remain code work.

The SQLite cache key includes provider/model,input,criteria and abstention policy.
Floating model aliases have bounded cache lifetimes. cache_only=True permits
explicit offline replay including stale aliases without provider requests.
Cache usage is zero for the new local replay,with original_usage preserved
separately. Missing provider usage remains unknown. No dollars are guessed.

No credentials,context-/portfolio/user records are sent. Exact configured-key
text is rejected before external calls. Parent must provide a public research
intent rather than personal amounts/holdings inside question. Classification
should use an empty/fixed question so labels reuse across unrelated user tasks.
Provider calls occur only in explicit assess; import/construction never calls a
model. Text over12,000 characters per source is explicitly marked truncated.

provider="doubao" is restricted to existing Pro/Plan settings and retains actual
response identity. Its scores are self-reported LLM output. Deterministic shape
adaptation accepts only exact requested question maps,adds only the request-known
choice type,and never guesses or changes a choice/probability. Probabilities,
selected maxima and exact option sets are validated. Length-truncated/nonfinite
responses fail safely and remain recorded. provider="lexical" is only a simple
term-overlap relevance diagnostic; other tasks abstain. It is not the stronger
existing memory-selection baseline.

## Tests and real pilot

-15 internal contract tests pass:cache invalidation/raw-vs-input hashes,actual
 model,privacy/credential boundaries,confidence abstention,offline cache,
 alias expiry,bounded retry,malformed/nonfinite responses,Pro shape adaptation,
 inconsistent choice/probability rejection and sidecar queue continuation.
- Existing3 Jev adapter tests still pass; investment/jev.py remains unchanged.
-12 real frozen publisher cases use monthly-v4 originals with checked raw hashes.
 Each case's normalized input_hash matches across Jev/Pro. Cases cover all four
 tasks and explicitly include false premises,postponement versus occurrence,
 causal overreach and prospective monitoring.
- Actual Jev1.14.0:12 valid typed responses,10ok and2low-confidence abstentions.
 Sequential provider wall time22.35seconds; reported input25,230/output1,275tokens.
- Original Pro first3 responses failed the strict wrapper shape. Original JSON
 and failures are immutable in pilot-v1. Those three maps were wrapped offline
 with no semantic edits or requests; only the remaining9 cases were then called.
 Total actual first requests remain12Jev+12Pro. Actual Pro identity is
 doubao-seed-2-1-pro-260915; after adaptation11valid judgments and1inconsistent
 choice/probability error remain. Real request wall time39.49seconds. All Pro
 usage is unknown even after requesting streamed usage; never count it as0.
- Security-versus-upgrade topic choice and prospective conditional support remain
 informative ambiguous/abstaining cases. These labels are provisional assistant
 expectations; human_labels.jsonl is pending independent adjudication. No human
 accuracy,F1,calibrated-confidence,dollar-cost or significance claim is made.
-26 cached successful/abstained judgments including lexical relevance replay
 exactly,with0 provider requests. Pilot-v2 cache near-zero time is NOT inference
 latency. CONTROLLED_COMPARISON.json uses original provider request times.
- Exact configured credential scan passed all private lane files. MANIFEST.json
 hashes frozen outputs; editable human labels remain separate. No API call is
 still running and no further model request is necessary for this handoff.

Receipts:local/halfyear/jev/HANDOFF.json,contract-tests.log,legacy-tests.log,
CREDENTIAL_SCAN.json,pilot-v1/ original attempts,pilot-v2/REPLAY.json,
pilot-v2/CONTROLLED_COMPARISON.json,pilot-v2/REVIEW.md and human_labels.jsonl.
Both original protocol-pinned implementation bytes and final source are retained.

## Read-only document index batches

Inventory only,no provider request:

```sh
.venv/bin/python scripts/evaluation/classify_jev_documents.py --library data/datasets/aether-month-20260926-20261026-v4 --limit 5
```

This actually enumerated the existing1,384 original documents. Explicit bounded
classification,when the parent chooses to run it:

```sh
.venv/bin/python scripts/evaluation/classify_jev_documents.py --library data/library-halfyear-20260426-20261026 --output local/halfyear/jev/halfyear-index-v1 --run --limit 20
```

No whole-corpus classification was launched. Annotation files/cache/state live
in the separate output; the source library is opened read-only. Repeat the same
command to continue after completed/abstained versions; failures stay pending.
Each batch stops after three provider failures while other project work proceeds.
Change output when changing model/criteria or intentionally reclassifying old
versions. --cache-only forbids provider requests.

Pilot reproduction:

```sh
.venv/bin/python scripts/evaluation/run_jev_evidence_pilot.py summary --output local/halfyear/jev/pilot-v2
.venv/bin/python scripts/evaluation/run_jev_evidence_pilot.py replay --output local/halfyear/jev/pilot-v2
.venv/bin/python scripts/evaluation/review_jev_evidence_pilot.py
```

Official protocol references checked this run:[Choice API](https://docs.typesafe.ai/primitives/choice),
[RAG passage classification](https://docs.typesafe.ai/cookbooks/classifying_rag_passages),
[citation check](https://docs.typesafe.ai/cookbooks/citation_check).
Official example outcomes do not establish Aether performance.

Transport isolation follow-up: the service accepts optional `timeout_seconds`
and `max_attempts`. Defaults retain Jev 45 seconds/two HTTP attempts and the
existing Pro 120 seconds/two SDK attempts; a task shadow can use 12 seconds/one
attempt. Bounds are 1–120 finite seconds and 1–3 integer attempts. These are
transport-operation budgets, not a hard total wall-time guarantee. The runtime
is responsible for its overall time budget and circuit breaker. Errors preserve
`error_type`, `http_status`, `timed_out`, and transport policy; unknown usage
remains unknown. Cache hits retain the original policy and require no provider
call. Twenty contract tests pass, including five transport follow-up checks;
see `local/halfyear/jev/transport-contract-tests.log`. No further external calls
were made and the frozen pilot receipts remain unchanged.

Half-year source-library integration follow-up: a separate fixed sample of24
public originals from9 sources was pinned before requests, including raw-source
and exact-text hashes. At selection the active library had7,280 searchable
original versions; this finite batch does not classify the whole corpus. The
actual model was `jev-1.14.0` in all24 responses:14 usable classifications,
9 abstentions and1 malformed probability distribution rejected as an error.
No retry was made and no Pro call was added. Provider-reported input/output
usage was63,418/4,980tokens; total_tokens and dollar cost remain unknown.
Sequential request latency summed to42.16seconds;3 inputs were explicitly
truncated to12,000characters. The source database was accessed read-only.

All23 successful/abstained versions have resumable completion markers; a
subsequent inventory-only invocation skipped them and returned only the one
pending error, making zero provider requests. The new fixed-sample/access-stop
contract test brings the contract suite to21 passing cases. Protocol, pinned
implementations, original responses/cache, annotations, cursor, separate blank
human labels and `ACCEPTANCE.json` are under
`local/halfyear/jev/halfyear-classification-v1/`. Human semantic accuracy remains
pending. Do not retry or extend the live sample as part of this acceptance.

To inspect the remaining cursor without provider requests:

```sh
.venv/bin/python scripts/evaluation/classify_jev_documents.py --library data/library-halfyear-20260426-20261026 --output local/halfyear/jev/halfyear-classification-v1 --sample-file local/halfyear/jev/halfyear-classification-v1/sample.json --limit 24
```
