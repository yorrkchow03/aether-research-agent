# Product feature inventory — Aether 0.2.0

Reviewed on **2026-10-16 (Asia/Shanghai)**. This is a local personal crypto
research application focused on ETH, with BTC comparison. A status marks the
stated implementation/evidence, not commercial readiness, source truth or
investment performance. Earlier real provider/model/browser checks are reused;
this release review reads saved state and performs offline software checks.

The complete machine-readable map is [FEATURE_STATUS.json](../development/FEATURE_STATUS.json).
See [verification scope](../development/VERIFICATION_REPORT.md),
[usage](PRODUCT_GUIDE.md) and [upload format](../development/RELEASE_CHECKLIST.md).

2026-10-26 status reconciliation: Jev now has one real adapter smoke test;
three-stage integration remains pending. The main implementation has not changed.
See the [current project review](../development/PROJECT_REVIEW_20261026.md).

| Status | Meaning |
|---|---|
| Implemented / verified | Implementation has the listed software, saved live or browser evidence; the row says which |
| Implemented / experimental | Working research component with unproven generalization or quality |
| Partial | Only the specified subset exists or is available |
| Adapter only / unconfigured | Offline integration contracts exist, but the actual external service has not been accepted |
| Not implemented | Not delivered as a working capability |


## Harness

### H01 — Local Deep Agents / LangGraph with Doubao Pro

**Status:** Implemented / verified within stated scope.

Real Pro research/report and follow-up jobs completed; configuration read back from running service.

**Boundary:** Authorized /api/plan/v3 only. Provider failures remain possible; no Lite or extra-billing fallback.

**Software checks:** [test_agent](../../tests/test_agent.py), [test_online](../../tests/test_online.py), [test_model_recovery](../../tests/test_model_recovery.py)

### H02 — Flexible tools, host terminal and Python SDK

**Status:** Implemented / verified within stated scope.

12 business tools and 8 framework tools asserted; real terminal/socket capture and registered custom calculation exercised.

**Boundary:** Trusted local host execution; not a multiuser OS sandbox. Analysis methods remain optional.

**Software checks:** [test_harness_sdk](../../tests/test_harness_sdk.py), [test_product](../../tests/test_product.py)

### H03 — Four progressively loaded research skills

**Status:** Implemented / verified within stated scope.

Packaged research, quantitative-analysis, strategy-research and portfolio-analysis skills; clean-wheel skill discovery checked.

**Boundary:** Skills guide methods; they do not force a fixed tool sequence. Synchronous subagent delegation is disabled.

**Software checks:** [test_harness_sdk](../../tests/test_harness_sdk.py)

### H04 — Session checkpoints, traces, stop and recovery

**Status:** Implemented / verified within stated scope.

SQLite conversation persistence, cancellation, provider-format repair and abandoned-job recovery tests; saved live traces retained.

**Boundary:** Continuing failed work is explicit; failed drafts do not become valid answers.

**Software checks:** [test_agent](../../tests/test_agent.py), [test_knowledge](../../tests/test_knowledge.py), [test_model_recovery](../../tests/test_model_recovery.py), [test_product](../../tests/test_product.py)

## Data

### D01 — ETH/BTC quotes and completed candles

**Status:** Implemented / verified within stated scope.

Saved real provider receipts and current dashboard endpoint inspected; normalization and unfinished-bar tests pass.

**Boundary:** Single-venue USD data, with source and retrieval times. Cached success is not proof of freshness.

**Software checks:** [test_product](../../tests/test_product.py), [test_data](../../tests/test_data.py), [test_strategy_history](../../tests/test_strategy_history.py)

### D02 — Returns, volume, volatility, drawdown and ETH/BTC comparison

**Status:** Implemented / verified within stated scope.

Window alignment, missing observations, completed-bar time and reproducible calculation contracts tested.

**Boundary:** Descriptive indicators, not predictive guarantees; volume and turnover are different quantities.

**Software checks:** [test_metrics](../../tests/test_metrics.py), [test_simulation](../../tests/test_simulation.py)

### D03 — Funding rates and open-interest snapshots

**Status:** Implemented / verified within stated scope.

Saved OKX source availability inspected; same-interval funding and same-contract OI arithmetic tested.

**Boundary:** One venue/contract. OI change uses an explicit saved interval and includes repricing, not proven capital inflow.

**Software checks:** [test_metrics](../../tests/test_metrics.py)

### D04 — Visible spread/depth and hypothetical purchase cost

**Status:** Implemented / verified within stated scope.

Partial coverage, price bands, VWAP and full intended-fee handling tested; saved real book available.

**Boundary:** Top saved levels only; no guaranteed fill, latency model or order placement.

**Software checks:** [test_metrics](../../tests/test_metrics.py), [test_investment_api](../../tests/test_investment_api.py)

### D05 — DeFi TVL, stablecoins, DEX activity and Aave fees

**Status:** Implemented / verified within stated scope.

Saved real source receipts and per-source dashboard status inspected; normalization tests preserve native units and gaps.

**Boundary:** Provider coverage varies; stocks, flows, fees and revenue are distinct metrics.

**Software checks:** [test_data](../../tests/test_data.py), [test_product](../../tests/test_product.py)

### D06 — Read-only Ethereum RPC and bounded Aave WETH analysis

**Status:** Implemented / verified within stated scope.

Saved real RPC/event capture and ABI/filter tests; available through terminal SDK.

**Boundary:** Specific pool/reserve/block window only. Historical liquidity may be unavailable; not full-chain inflow or buying pressure.

**Software checks:** [test_onchain](../../tests/test_onchain.py), [test_harness_sdk](../../tests/test_harness_sdk.py)

### D07 — Broader macro, ETF, liquidation and consensus coverage

**Status:** Partial.

Optional archive adapters and explicit missing-data behavior exist; comprehensive live coverage is not accepted.

**Boundary:** No claim of complete live coverage; unavailable or permission-limited fields remain gaps.

**Software checks:** [test_data](../../tests/test_data.py)

## News and memory

### N01 — Web search, publisher feeds and original-page capture

**Status:** Implemented / verified within stated scope.

Real Pro search/original-page workflow completed; citation annotations, extraction and raw-integrity tests pass.

**Boundary:** Search excerpts are separate from originals; publication dates may be unknown; search uses configured provider quota.

**Software checks:** [test_online](../../tests/test_online.py), [test_product](../../tests/test_product.py)

### N02 — Persistent news, event revisions and correction timeline

**Status:** Implemented / verified within stated scope.

Reviewed source-backed event revisions, cutoff tests and browser timeline accepted; running API retains saved versions.

**Boundary:** Event grouping/classification is an analyst judgment. Saved versions are not a large independently observed revision corpus.

**Software checks:** [test_knowledge](../../tests/test_knowledge.py), [test_desk](../../tests/test_desk.py)

### N03 — Cross-session recall of evidence and prior research

**Status:** Implemented / verified within stated scope.

News FTS/lexical recall, original source identity, answer-context index and restart behavior tested; actual saved context recalled.

**Boundary:** Prior AI conclusions are research context, not a new fact source or current user preference.

**Software checks:** [test_knowledge](../../tests/test_knowledge.py), [test_research_memory](../../tests/test_research_memory.py), [test_harness_sdk](../../tests/test_harness_sdk.py)

### N04 — Relevance, MMR and coverage evidence selection

**Status:** Implemented / experimental.

Budget, time, duplicate and linked-update contracts; frozen matched selector comparisons completed.

**Boundary:** Lexical training-free methods; coverage scored below relevance/MMR in the pilot. Context cost is an estimate.

**Software checks:** [test_memory_selection](../../tests/test_memory_selection.py), [test_memory_benchmark](../../tests/test_memory_benchmark.py)

## Research and evidence

### R01 — Manual research questions and reviewed answers

**Status:** Implemented / verified within stated scope.

Real Pro current-data and saved-history answers completed; numeric, unit and source gates tested.

**Boundary:** Separate-context review uses the same model family and remains fallible AI assessment.

**Software checks:** [test_agent](../../tests/test_agent.py), [test_review](../../tests/test_review.py), [test_knowledge](../../tests/test_knowledge.py), [test_advice](../../tests/test_advice.py)

### R02 — Structured reports and publication gates

**Status:** Implemented / verified within stated scope.

Real report publication with numeric and source support checks; failed-review/export drafts retained.

**Boundary:** Attribution is inspectable, not guaranteed truth. Unsupported claims must be revised, removed or qualified.

**Software checks:** [test_agent](../../tests/test_agent.py), [test_review](../../tests/test_review.py), [test_product](../../tests/test_product.py)

### R03 — Claim-to-original evidence reader

**Status:** Implemented / verified within stated scope.

Report and chat advice/history citation mapping checked in desktop/mobile browser; exact-quote contracts tested.

**Boundary:** Unmatched citation context falls back to source view; quote integrity alone does not establish semantic correctness.

**Software checks:** [test_review](../../tests/test_review.py), [test_advice](../../tests/test_advice.py)

### R04 — Registered autonomous Python calculations

**Status:** Implemented / verified within stated scope.

Real terminal calculation rerun, recipe/input hashes, lineage and publication-number checks.

**Boundary:** Unregistered terminal prose is not automatically citable. Reproducibility does not prove a method is suitable.

**Software checks:** [test_harness_sdk](../../tests/test_harness_sdk.py), [test_product](../../tests/test_product.py), [test_replay](../../tests/test_replay.py)

### R05 — Evidence ZIP, integrity verification and offline replay

**Status:** Implemented / verified within stated scope.

Real report package integrity and custom recipe replay passed; tampering, missing-file and traversal cases tested.

**Boundary:** Local exports can contain cited private portfolio data; public source delivery excludes them. Archived code replay executes on the host.

**Software checks:** [test_audit](../../tests/test_audit.py), [test_replay](../../tests/test_replay.py)

### R06 — Saved report history and changes

**Status:** Implemented / verified within stated scope.

Immutable earlier reports and literal claim/source/judgment diffs tested; browser comparison inspected.

**Boundary:** Literal wording differences are not automatically material market events.

**Software checks:** [test_desk](../../tests/test_desk.py)

## Advice

### A01 — Evidence-linked conditional suggestions

**Status:** Implemented / verified within stated scope.

Real current-data advice published; conditions, counterpoints, invalidation, gaps and review triggers validated/reviewed.

**Boundary:** No established accuracy rate, causal price effect or profitable investment edge.

**Software checks:** [test_advice](../../tests/test_advice.py), [test_review](../../tests/test_review.py)

### A02 — Qualified historical-case reasoning

**Status:** Implemented / verified within stated scope.

Real saved-experiment follow-up; case end, receipt time, underlying inputs and transfer limits tested.

**Boundary:** Unavailable analogies remain gaps; retrospective observations are not promoted to point-in-time evidence or invented win rates.

**Software checks:** [test_advice](../../tests/test_advice.py), [test_research_memory](../../tests/test_research_memory.py)

### A03 — Research preferences and paper scenarios

**Status:** Implemented / verified within stated scope.

Unknown versus explicit-zero context, loss scenarios and common-window buy/hold, staged-entry and momentum calculations tested.

**Boundary:** Defaults are labelled assumptions; hypothetical loss is not a probability estimate or actual P&L.

**Software checks:** [test_advice](../../tests/test_advice.py), [test_simulation](../../tests/test_simulation.py), [test_product](../../tests/test_product.py)

## Portfolio

### P01 — Manual USD/ETH/BTC ledger and CSV operations

**Status:** Implemented / verified within stated scope.

Atomic imports, duplicates/conflicts, void/restore and export tests; disposable UI acceptance completed.

**Boundary:** Actual ledger currently empty; no demo holdings inserted. Manual records are not exchange-verified balances.

**Software checks:** [test_investment_ledger](../../tests/test_investment_ledger.py), [test_investment_api](../../tests/test_investment_api.py)

### P02 — FIFO basis, fees and realized/unrealized P&L

**Status:** Implemented / verified within stated scope.

Exact cash/fee accounting, transfers, cutoff and missing-mark/basis cases tested.

**Boundary:** Unknown basis remains unknown. This is not tax accounting and excludes unrecorded outside holdings.

**Software checks:** [test_investment_ledger](../../tests/test_investment_ledger.py)

### P03 — Read-only portfolio context and purchase constraints

**Status:** Implemented / verified within stated scope.

Private snapshot lineage and cash/concentration/depth/conditional-loss preview tested.

**Boundary:** Business analyst has no ledger-write tool. Preview is not a trade or complete suitability assessment.

**Software checks:** [test_investment_api](../../tests/test_investment_api.py), [test_advice](../../tests/test_advice.py)

## Strategy Lab

### S01 — Local logistic/tree training and walk-forward evaluation

**Status:** Implemented / experimental.

Real saved completed-OHLCV experiment and model inspection; temporal labels, purging, refit and future-invariance tests.

**Boundary:** Small inspectable research baselines, not demonstrated professional forecasting or validated profitable models.

**Software checks:** [test_strategy_models](../../tests/test_strategy_models.py), [test_strategy_history](../../tests/test_strategy_history.py)

### S02 — Cost-aware common-window simulations and exports

**Status:** Implemented / verified within stated scope.

Common calendar/costs, delayed execution, baseline comparisons, artifact JSON and deterministic report tested.

**Boundary:** Idealized daily-open fills; no real latency/market impact. Repeated experiments weaken holdout independence.

**Software checks:** [test_strategy_models](../../tests/test_strategy_models.py), [test_simulation](../../tests/test_simulation.py)

## Monitoring

### T01 — Persistent once/daily/weekly briefs and interval monitors

**Status:** Implemented / verified within stated scope.

Real scheduled report completed; timezone, restart, duplicate prevention and bounded retry tests.

**Boundary:** Local computer and service must run. Three saved product schedules are currently disabled; no new schedule was enabled in this review.

**Software checks:** [test_scheduler](../../tests/test_scheduler.py)

### T02 — Changes-only triggers and in-app run status

**Status:** Implemented / verified within stated scope.

Quiet baseline, unchanged-no-model-call, busy deferral and saved run-state tests; status UI inspected.

**Boundary:** Thresholds are user monitoring preferences. External email/SMS/push channels are not implemented.

**Software checks:** [test_scheduler](../../tests/test_scheduler.py), [test_desk](../../tests/test_desk.py)

## Dashboard

### U01 — English Home, Research and Portfolio workspace

**Status:** Implemented / verified within stated scope.

Real desktop/390px checks cover three primary destinations, nested news/reports/updates, embedded chat, optional preferences and More. Saved evidence/history access is preserved.

**Boundary:** ETH is the primary workspace and BTC a comparison asset; not an arbitrary-asset trading terminal.

**Software checks:** [test_desk](../../tests/test_desk.py), [test_investment_api](../../tests/test_investment_api.py)

### U02 — Persistent inbox, comparison point and disconnected recovery

**Status:** Implemented / verified within stated scope.

Seen state, irregular quote-baseline semantics and CSRF tests; real disconnect/reconnect and reload acceptance.

**Boundary:** Saved quote change is not personal profit. Reconnect does not silently restart model research.

**Software checks:** [test_desk](../../tests/test_desk.py), [test_agent](../../tests/test_agent.py)

## Validation

### V01 — Offline application regression and clean-wheel startup

**Status:** Implemented / verified within stated scope.

167 tests across 26 modules, offline harness probe and isolated wheel-state acceptance; no paid model calls in software tests.

**Boundary:** Dependencies reused from locked local runtime; Linux/Python-version CI is configured but not remotely executed here.

**Software checks:** [test_agent](../../tests/test_agent.py), [test_product](../../tests/test_product.py), [test_investment_api](../../tests/test_investment_api.py)

### V02 — Frozen historical and memory evaluations

**Status:** Implemented / verified within stated scope.

Saved 3-case historical gate pilot; 13 official-source snapshots, 12 groups, 39 probes and 77 frozen contract checks.

**Boundary:** Small, AI-authored pilot labels and current captures; not a large independent market benchmark.

**Software checks:** [test_evaluation](../../tests/test_evaluation.py), [test_memory_benchmark](../../tests/test_memory_benchmark.py), [test_memory_selection](../../tests/test_memory_selection.py)

### V03 — Matched paired Pro answers and recorded failures/costs

**Status:** Implemented / experimental.

Four fixed cases by three selectors produced 12 outputs with shared inputs/model and actual token/latency records.

**Boundary:** No same-model truth judge; response-mode agreement is not semantic accuracy. No statistically established improvement.

**Software checks:** [test_memory_answers](../../tests/test_memory_answers.py)

### V04 — Independent human adjudication and richer real revision histories

**Status:** Not implemented.

Explicit gap preserved in acceptance and evaluation guide.

**Boundary:** Labels await independent review; twelve independently observed multiversion real event histories are not available. New tuning needs untouched confirmation data.

**Software checks:** No core test module; see verification scope.

## Optional / future

### X01 — Jev external semantic assessment

**Status:** Adapter connected / experimental.

Adapter validation, abstention, credential boundary and no-key behavior tested.
One authorized real event-status smoke test completed on 2026-10-23, returning
jev-1.14.0 and the expected synthetic planned status (588 input / 63 output tokens,
1.223 seconds). Three-stage integration and task/report quality remain unverified.

**Boundary:** Separate TYPESAFE_API_KEY required. No substitution with Doubao-labelled Jev and no price prediction claim.

**Software checks:** [test_jev](../../tests/test_jev.py)

### X02 — Public-chain audit anchoring

**Status:** Partial.

Contract/client and local EVM tests retained; public Sepolia probe lacked test funds.

**Boundary:** No verified public deployment/transaction. Optional audit dependencies are separate; no mainnet deployment.

**Software checks:** No core test module; see verification scope.

### X03 — Exchange sync, automated orders and public multiuser deployment

**Status:** Not implemented.

No working exchange-order or account-sync integration is delivered; service is loopback-only.

**Boundary:** Current release is a local single-user research application, not a broker, cloud service or production account system.

**Software checks:** No core test module; see verification scope.

## Optional teacher-feedback research extension

See [usage and methods](RESEARCH_EXTENSIONS.md) and
[acceptance evidence](../development/RESEARCH_EXTENSIONS_ACCEPTANCE.md).

| Capability | Status and boundary |
|---|---|
| Read-only RPC resilience | Verified transport/cache/backoff/range tests and low-load real probe; historical state requires separate provider permission |
| Ten explicit signal contracts | Formula, availability, hypotheses, alternative explanations and failure conditions; no presumed predictive value |
| Frozen signal evaluation | 4,960 real ETH/BTC daily bars,18 cases,23 variants,3 costs;18-case saved-source offline replay matched exactly |
| Multi-role strategy workshop | Real two-round Deep Agents/Pro author/reviewer/refinement and sealed final test; experimental generated code requires semantic review |
| Generated-code semantic check | Constant-price invariant detected a wrong63-bar mean in the real selected candidate; candidate visibly blocked for correction, never promoted |
| Advanced integration | Collapsed optional Strategy Lab panel; no primary navigation, ledger, default methods or main tool changes |
| Argo / funding arbitrage | Argo inspected, not integrated; Go/WASM setup and parity validation remain future work. Funding arbitrage deferred |
