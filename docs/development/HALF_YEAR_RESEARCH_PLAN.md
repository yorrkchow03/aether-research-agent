# Half-year evidence and memory upgrade

Authorized2026-10-27. Fixed window:[2026-04-26T00:00:00Z,2026-10-26T00:00:00Z),183days,4,392closed hours per asset. ETH primary,BTC reference; Ethereum,Arbitrum,Base,Optimism. Existing monthly v4 and all prior frozen evaluations/source ZIPs stay immutable. No new schedule,purchase,trading,public release or GitHub push.

## Parallel ownership

- data_foundation:src/research_data/library/,new data CLI/scripts,tests/test_library*,new tests/test_halfyear*,docs/development/HALF_YEAR_DATA_STATUS.md. Work in data/library-halfyear-20260426-20261026 and local/halfyear/data/. Own window parameterization,preflight,reuse/capture,text/numerical coverage,verification/freeze/restore. Do not edit application runtime/tools/knowledge or Jev/memory modules.
- jev_pipeline:src/research_agent/investment/jev.py,new src/research_agent/evidence_judgments.py and related internal modules,tests/test_jev*/test_evidence_judgments*,scripts/evaluation/*jev*,docs/development/JEV_PIPELINE_STATUS.md. Own cached classification/relevance/relation/support judgments,provider comparisons and bounded true API checks. Do not edit library or memory/runtime/tools/server.
- memory_quality:src/research_agent/knowledge.py,memory_selection.py,new working_memory.py/experience_memory.py/evidence_memory.py modules,tests/test_memory*/test_research_memory*/test_working_memory*,docs/development/MEMORY_QUALITY_STATUS.md. Own task evidence packs,context compaction,events/revisions,source-grounded correction experience,freeze/replay quality checks. Do not edit library/Jev/runtime/tools/server.
- parent:shared interface below,main runtime/tools/server/frontend integration,common docs/requirements,EXECUTION_STATE,real combined verification and clean export. Agents save their own named status files; parent alone appends EXECUTION_STATE. No agent silently reverts another's edits. No concurrent writer per acquisition/model-job identity.

## Internal judgment contract

Jev lane exposes EvidenceJudgmentService(root, data_dir=None, provider='jev') in research_agent.evidence_judgments. Method assess(task, *, question='', evidence, criterion_version='v1') returns a plain JSON-compatible dict. tasks:classification,relevance,relation,support. Evidence is a list of existing normalized source rows with record_id,text or excerpt,values/units,source.sha256,publication/event/acquisition times. Do not require a new model-visible business tool.

Result keys:status(ok/abstained/unavailable/error),task,label(optional),labels(list),scores(dict),confidence(optional),model,requested_model,evidence_ids,source_hashes,input_hash,criterion_version,cache_key,cache_hit,created_at,latency_seconds,usage(optional),error(optional),scope. Actual serialized judged snippets/query/criteria determine input_hash,not just source URL. Raw-source hashes and snippet hashes remain distinct. Failed/unknown judgments are not negative labels; raw confidence is not calibrated correctness. No private portfolio/user facts sent to Jev. Optional provider='doubao' uses configured Pro/Plan only; no fallback masquerades as Jev. Network/model calls are explicit,never from import or merely opening UI.

Memory code accepts an injected callable matching assess; deterministic/no-provider mode is always available. Before deciding discard/merge,match asset,event,time and numerical scope. Retain critical contrary evidence and unresolved conflict; preserve source-backed updates/corrections. Classification/summary/judgment is derived metadata,not a replacement for original evidence.

## Data and resource policy

Inventory and reuse monthly captures; do not change fixed monthly constants in place or overwrite old windows. All native frequencies and venue/contracts stay independent. Preflight each half-year source,finite retries,checkpoint each page/group. Permissions,calendar nonobservations,pending publication and unresolved missing remain distinct. API/source errors do not halt unrelated groups. No invented data or old snapshots relabelled as history.

Current available space is about44GiB. Do not start a half-year full raw execution-header sweep by default; first complete low-volume metrics/text and report the raw-header/storage/time gap. Existing frozen sources and original private state are never cleanup targets. No new paid data rights. A partial available-data freeze must retain explicit missing/core_complete=false where appropriate.

## Quality and integration

Long-term evidence keeps originals,versions,event changes and citation lineage. Working memory keeps current goal/plan,evidence facts,conflicts,unknowns and active artifacts; large tool outputs are stored and recalled by ID. Correction experience is source/review-backed process guidance,not a fabricated market pattern or user fact.

Preserve Deep Agents/LangGraph,Doubao Pro and authorized /api/plan/v3. Maintain existing general terminal/SDK and publication boundaries. Jev semantic judgments never replace code arithmetic/exact dates/hash checks. Prefer an optional sidecar/shadow rollout,record actual versions,input/output usage,cache,latency,failures and abstention. Validate with frozen same-input baselines/ablations and separate human-pending labels; no same-model truth grading or single profitability proof.

Finish with half-year coverage/source/gap/query/restore evidence,three-stage Jev components and bounded live checks,memory/error regressions and real Pro report/source audit,English UI affordances for evidence/context status,clean source ZIP and handoff. No need to repeat unaffected full-month validation. Only claim stages actually verified; unavailable paid data remains an explicit limitation.
