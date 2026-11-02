# Verification report — Aether 0.2.0

Reviewed on **2026-10-16, Asia/Shanghai**. See the complete
[feature inventory](../guides/FEATURES.md) and its
[machine-readable evidence map](FEATURE_STATUS.json).

Status update, **2026-10-26**: the current [project review](PROJECT_REVIEW_20261026.md)
supersedes the historical unconfigured-Jev note below. Jev completed one real
adapter connection test on 2026-10-23; its three-stage research workflow remains
unimplemented. Current production code still matches the 235-test team-handoff
artifact. A fresh offline framework/empty-state check passed on 2026-10-26;
the full suite and paid research were not repeated for this documentation review.

## What was checked

The latest UI follow-up simplifies navigation to Home, Research and Portfolio,
embeds the conversation, nests news/reports/updates, moves advanced controls under
More and increases typography. The backend harness, data, accounting and advice
logic are unchanged. Current source, wheel and loaded runtime hashes are compared
before packaging. Earlier paid research is reused; the changed UI receives actual
desktop/mobile checks and offline package regression. Read-only local API checks do not refresh provider
data, start research, change portfolio records or enable schedules.

| Area | Evidence | Scope |
|---|---|---|
| Software regression | 167 offline tests in 26 modules | Contracts, adversarial cases and mocked transports; not 167 independent market events |
| Harness wiring | Offline Deep Agents tool loop/checkpoint probe | Verifies local graph execution without a paid model |
| Packaging | Isolated wheel imports, four skills, empty portfolio/inbox, assets and exported regression suite | Installed locked dependencies reused; not a clean internet dependency download |
| JavaScript | Syntax checks of dashboard, investment and desk scripts | Prior actual desktop/mobile browser acceptance retained |
| Live APIs | Local health/config, market snapshot, news/history, reports, portfolio, strategies, subscriptions and evaluation endpoints | Reads saved state; a stored `ok` receipt does not prove current external freshness |
| Publication | Earlier real Pro market/search/original-page/custom-calculation/report workflow | Source/numeric gates passed; author and separate-context reviewer are still AI |
| Historical advice | Earlier real Pro answer on an actual saved strategy experiment | Case end, source lineage, material differences and limits retained |
| Audit | Earlier report: 82 package files, zero integrity issues, two numeric checks and one custom recipe replay | Integrity/reproducibility, not truth or investment skill |
| Scheduling | Earlier real scheduled report plus timezone/retry/deduplication/restart tests | Three current saved product schedules are disabled |
| Portfolio | FIFO/import/void/restore tests and disposable browser acceptance | Actual ledger remains empty; no test holdings added to it |
| Browser | Earlier real desktop and 390px mobile checks, exact claim passages, timeline/diff and reconnect | No repeated paid research for this UI and documentation release |

The private receipts and browser images are retained locally, outside the public
source export. The local release report records precise command results and
artifact hashes. Public documentation summarizes scope without redistributing
account-specific research records or publisher articles.

## Research measurements retained

- The earlier three-case historical pilot completed its source/cutoff gates and
  abstained from directional forecasts. It does not establish predictive skill.
- The memory pilot uses thirteen official-source snapshots in twelve topic
  groups, 39 retrieval/scope/time probes, six matched selector/ablation variants
  and 77 frozen contract checks. Frozen software/input replay passed.
- Required-passage recall for relevance/MMR was 5/5 development and 7/8 held-out;
  coverage was 3/5 and 5/8. The weaker result is retained and specific queries use
  relevance. All thirteen before-capture targets were excluded by every policy.
- Four fixed questions by three selectors produced twelve real Pro outputs with
  matched candidates/model/context. Each method agreed with the AI-authored
  response-mode rubric on 3/4 cases. This is a schema/label agreement measure,
  not 75% semantic accuracy. Exact eligible quotations do not validate all prose.
- Provider tokens and latency are recorded where returned. Missing usage remains
  unknown; currency charges or fully attributable end-to-end cost are not inferred.

Detailed denominators, ablations, failures and reproduction steps appear in
[memory evaluation](../guides/MEMORY_EVALUATION.md).

## Open limits

Independent human semantic adjudication remains pending. The pilot has current
source snapshots rather than twelve independently observed multiversion event
histories. Its held-out results have been inspected, so subsequent tuning needs
new untouched confirmation data. No statistical advantage, causal price effect
or profitable investment edge is established.

Jev has offline adapter/abstention tests but no configured separate key or real
accepted provider request. Public audit anchoring has local contract/client
tests but no verified public deployment. Broader macro/ETF/liquidation/consensus
coverage is incomplete. There is no exchange synchronization, automated trading,
external notification channel or public multiuser service. No Git push occurred.


Teacher-feedback extension update: see [RESEARCH_EXTENSIONS_ACCEPTANCE.md](RESEARCH_EXTENSIONS_ACCEPTANCE.md) for the optional signal validation/workshop, real data, negative results and candidate review block. Earlier verification remains scoped to its recorded release.
