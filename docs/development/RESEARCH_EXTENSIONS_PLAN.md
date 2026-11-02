# Optional research extensions — teacher feedback

Status: delivered as an experimental optional extension, reviewed 2026-10-17.
The generated strategy remains blocked for semantic correction; Argo and funding
arbitrage are deferred. See RESEARCH_EXTENSIONS_ACCEPTANCE.md for actual evidence
and limits. Started 2026-10-16. This follows the user's request to apply
the teacher's feedback as plug-in changes, emphasizing more data and stronger
validation. Existing Home/Research/Portfolio, Deep Agents/Doubao Pro, publication
gates, four main skills, twelve business tools, ledger and walk-forward method
remain the compatibility baseline. No trading, purchases, public deployment,
framework migration or new scheduled development tasks are authorized here.

## Deliverables and acceptance

### RE1 — RPC resilience

- An injectable, separately tested read-only RPC transport: bounded request rate,
  exact-request cache, raw receipt integrity, original capture times, bounded
  Retry-After/exponential backoff and explicit permanent/throttle/range/history
  errors. Never send the Ark credential to a public provider.
- Small log batches with adaptive subdivision on provider range/result limits,
  bounded attempts, exact contiguous coverage and preserved input receipts.
- A low-load capability probe for observed block/log/history access. Record a
  conservative client policy separately from a provider's documented/observed
  limits; do not infer a maximum rate from one successful call or load-test a
  public endpoint. Historical failure remains a gap, never a zero metric.
- Adversarial offline tests plus bounded real endpoint receipts and cache reuse.

### RE2 — Explicit signal contracts and evaluation

- A versioned catalog for each existing OHLCV signal: exact formula/unit,
  availability/lookback, research rationale, competing explanations and failure
  conditions. Hypotheses are not claims of profitable or causal effects.
- A separate optional evaluator, without changing strategies.run_experiment:
  per-signal models, full model, leave-one-signal-out ablations, common target
  rows, chronological/availability-purged training and matched cost baselines.
- Prespecified out-of-sample windows, cost stress and time-block uncertainty
  summaries. Report errors, abstention, denominators and weaker results. Feature
  importance is model-dependent; independent signal validity is not assumed.

### RE3 — Larger frozen validation corpus

- Capture multiple years of actual completed ETH and BTC OHLCV with bounded
  provider paging, immutable originals/normalized hashes, provenance and explicit
  gaps. Reuse existing data primitives; never copy private holdings or fabricate
  future observations.
- Freeze source/config/software manifests before evaluation. Development and
  validation are distinct from a final confirmation period; agent tuning cannot
  see final outcomes/metrics. All frozen evaluation runs offline without refresh.
- Compare fixed baselines and ablations on identical rows/windows and record
  source dates, runtime, failures and limitations. Retrospective capture does not
  establish historical public availability. Add adversarial strategy/code/time
  contracts beyond happy-path market examples.

### RE4 — Bounded multi-role strategy workshop

- Optional Deep Agents/Doubao Pro author and reviewer roles, deterministic code
  validation/backtesting and a bounded refinement loop. Reuse the configured
  authorized Plan endpoint; do not substitute Codex or use a new paid provider.
- Produce real inspectable strategy source, rationale, declared inputs and
  limitations. Keep every attempt, review and evaluation, including failures.
- Author/reviewer see development/validation context only. Select/lock code by a
  prespecified validation objective, then evaluate the sealed final period once
  after model refinement ends. Do not feed final results back into tuning.
- Generated strategy execution has a narrow pure-function contract, static
  restrictions, prefix-only data, credential-free child execution and resource
  limits. It is research code review/backtesting, not an order tool or a claim of
  a hostile-code security sandbox. The existing general research terminal stays
  flexible and unchanged.
- Offline unsafe/future/nonfinite/invalid-output cases, failure/stop/restart
  behavior and one bounded real Pro refinement run must be evidenced.

### RE5 — Optional engines and scope

- Inspect the teacher's Argo repositories/docs and actual local toolchain before
  deciding an adapter boundary. Preserve the working Python evaluator. Expose
  unavailable external engines honestly; do not simulate an Argo integration or
  rewrite the existing product to adopt it.
- Funding-arbitrage research remains explicitly optional/deferred. Current
  funding observations are descriptive and are not an implemented arbitrage.
- Vercel/Codex/Claude alternatives are suggestions, not a requirement to replace
  the authorized local Deep Agents business harness.

### RE6 — Product integration and delivery

- Lazy optional extension service/API and advanced Strategy Lab panels. No new
  primary navigation, mandatory research chain, ledger writes, enabled schedules
  or new default analysis method. Document how to disable/unplug the extension.
- Preserve source-linked outputs; deterministic experiment summaries are distinct
  from an analyst-reviewed report. No model self-grade is used as ground truth.
- Run meaningful extension and compatibility regression, real corpus/RPC/Pro
  acceptance and desktop/mobile checks. Refresh source/wheel/ZIP and checksum
  artifacts; keep secrets, captured data and private studies out of upload source.
- Update execution state after each material result/failure. Goal completion
  requires inspecting evidence for every deliverable above; unavailable optional
  engines and scientific limits are disclosed, not relabelled as accepted live
  integrations or profitable performance.

## References to inspect

- Ethereum JSON-RPC: https://ethereum.org/developers/docs/apis/json-rpc/
- Block-hash log identity: https://eips.ethereum.org/EIPS/eip-234
- Coinbase candle/time/rate contracts: https://docs.cdp.coinbase.com/exchange/
- Temporal evaluation: https://scikit-learn.org/stable/modules/cross_validation.html
- Model-dependent feature evaluation: https://scikit-learn.org/stable/modules/permutation_importance.html
- Argo framework: https://github.com/rxtech-lab/argo-trading
- Argo macOS application: https://github.com/rxtech-lab/argo-trading-macOS
