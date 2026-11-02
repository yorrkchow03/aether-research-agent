# Teacher-feedback extension acceptance — 2026-10-17

The optional research extension is implemented without replacing the main
Deep Agents/Doubao Pro harness, analyst tool/skill set, ledger, existing
walk-forward algorithm or three primary destinations. It is an experimental
research capability, not a validated profitable strategy.

## Real data and evaluation

- 20 preserved Coinbase response pages; ETH and BTC each have2,480 complete,
  contiguous UTC daily bars,2020-01-01–2026-10-16 exclusive:4,960 bars total.
- Corpus manifest:
  `9465b345e94035072bbb4d9590c6331ce42f7b0499f1a77d1a43350e3bee1d93`.
- 18 prespecified asset/windows:6 development,4 validation,8 confirmation.
  Across them4,228 common asset-target observations; earlier bars are training
  context. These are not4,228 independent observations.
- 23 variants per case: full logistic, shallow forest, constant prior,
  ten single-signal logistic fits and ten drop-one refits. Three matched cost
  scenarios, four baselines and explicit abstention counts. Every case passed;
  failed cases would exclude the whole case for all variants.
- Paired14-day circular block intervals,300 draws, predeclared seed. These are
  descriptive uncertainty intervals, not multiple-comparison-adjusted claims.
- Original hashes, protocol/software/environment lock, fitted JSON models,
  predictions, cutoffs and per-window results retained. Saved-source replay with
  socket connects blocked reproduced18/18 case JSONs exactly. No frozen test
  used network or a paid model.

| Partition | Cases | Common target rows | Constant-prior Brier | Full logistic | Full forest |
|---|---:|---:|---:|---:|---:|
| Development |6|2,190|0.252123|0.255327|0.254573|
| Validation |4|732|0.249539|0.249641|0.249856|
| Confirmation |8|1,306|0.250320|0.251723|0.251271|

Lower is better. Both full models were weaker than the constant prior in these
aggregate partitions. No investment edge follows from this study. Source data
is retrospective and single venue, not authenticated point-in-time releases;
idealized fills/costs, correlated assets/features and adaptive validation reuse
remain limitations. Outcomes were not used to retune the frozen comparison.

## Actual Pro workshop and semantic failure

- First real bounded attempt failed the pure-source gate: authors repeatedly
  used disallowed collection mutation. That attempt used two author rounds and produced no
  eligible strategy or confirmation. Its failures/usage were retained.
- Improved concrete error messages and simpler coding guidance then yielded
  two author and two separate-context reviewer stages, actual source, both
  deterministic development/validation evaluations, source lock and8 final
  confirmation cases. Nine successful-study model requests used actual provider
  ID `doubao-seed-2-1-pro-260915`,150,874 recorded tokens. Monetary cost is unknown;
  no amount was inferred. Only the authorized Plan endpoint was used.
- Round2 was selected by the prespecified validation objective. Final results
  were evaluated after locking, never passed to author/reviewer contexts and
  not reused to select a different candidate. Paper results include positive
  and negative windows; no orders or ledger mutations occurred.
- A subsequent code/formula review found a real defect: the declared63-bar
  moving average sums42 prices but divides by63. A deterministic constant100
  price test yields66.6666666667 instead of100. The candidate is visibly
  **blocked for semantic correction**. Original faulty source and results stay
  intact; no corrected candidate or fresh independent confirmation is claimed.
- This is useful negative validation: role approval and successful backtesting
  do not establish mathematical correctness. All generated code remains a
  review artifact, never a promoted analyst strategy or an execution instruction.

## RPC, software, API and interface

- 23 new adversarial RPC tests exercise exact original-time cache reuse,
  integrity,429/Retry-After and transient retries/deadlines, range adaptation,
  contiguous coverage, missing histories, invalid envelopes and input bounds.
- Low-load mainnet probe obtained chain/finalized/historical headers and a
  32-block filtered log window; a repeated request reused the identical receipt.
  Historical state requires the provider's personal token and remains a gap.
  Provider maximum rate/range was not measured or inferred.
- 220 offline tests passed in the working environment, including existing
  terminal/socket tests and new corpus, signal, pure-code, time/future-data,
  stop/resume, semantic-invariant, extension/API/CSRF and compatibility checks.
  Unix-socket permission was needed for the existing SDK tests. No model judged
  these tests as ground truth. Source/wheel verification is recorded separately.
- Desktop and390×844 mobile checks confirmed the optional panel, saved negative
  results, per-signal uncertainty, review block and preserved Home/Research/
  Portfolio navigation. Mobile document width390, with wide tables contained in
  horizontal scrolling. Body text16px; no browser console errors observed.
- Argo repository tree `5000407e138e3d38bfa98f3318e02dd4fdbcaa17` inspected;
  no local Go toolchain. Argo execution/Go-WASM parity is not implemented.
  Funding arbitrage stays optional/deferred; no framework migration occurred.

## Evidence locations (local, excluded from upload source)

- `reports/research_extensions/`: RPC probe, regression, replay, Argo inspection
  and clean-export receipts.
- `data/validation/signals_v1/`: immutable real corpus, originals and provenance.
- `runtime/research_lab/runs/e964d72ef0ab40d88c3062ad8a495c46/`: formal signal study.
- `runtime/research_lab/runs/b61cb0c7706e440ba3fc8c8843b1bbe6/`: first failed Pro attempt.
- `runtime/research_lab/runs/f557620ec4f84a62b42872530814136d/`: real two-round
  workshop, immutable original result and separate blocked developer review.

Public upload contains code, protocol, tests, docs and scripts; no credentials,
raw captured data, private holdings, generated research or model traces. A clean
wheel test reuses installed locked dependencies, not a fresh dependency download.
