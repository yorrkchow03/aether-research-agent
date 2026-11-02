# Optional signal validation and strategy workshop

Open **More → Strategy Lab → Signal validation & strategy workshop**. The
existing walk-forward experiment remains above it. Home, Research, Portfolio,
the analyst's four skills and12 business tools are unchanged. Nothing here
places orders or changes recorded holdings. Funding arbitrage is deferred.

## Inspect or run

Select a saved **Signal validation** run. Inspect aggregate Brier scores, then
choose an asset/window to view all ten signal ablations with paired uncertainty.
Positive improvement means lower Brier error. Expand matched-cost results for
the full model, individual signals, drop-one refits and baselines. The signal
catalog explains formulas, hypotheses, competing explanations and failure cases.
Sources are Coinbase ETH-USD/BTC-USD completed UTC daily bars, with preserved
original pages and normalized hashes. These are retrospective captures; observed
dates do not establish historical public availability.

**Run offline signal validation** starts an isolated experiment on the frozen
corpus. It does not fetch data or call an LLM. All variants share target rows,
capital, dates and per-side costs. Training labels must be available strictly
before each window's first decision. Execution waits one full daily bar. The
comparison uses fixed per-window fits, distinct from the existing expanding
walk-forward tool. Neither replaces the other.

**Start 2-round Pro workshop** uses the existing authorized Doubao Pro Plan:
author → independent-context model reviewer → deterministic Python backtest →
bounded refinement. It saves actual strategy code, rationale, failures, reviews,
source identity, each attempt and model token usage. Models see only development
and validation context/feedback. The selected source is locked before one final
confirmation evaluation, which is never returned to those roles. Reusing a
confirmation result for later tuning would invalidate it as an independent test.

Workshop completion means the research pipeline finished. It does **not** mean
the strategy is correct or approved for investment. Every candidate still needs
independent formula/code review. The real acceptance study exposed a wrong
63-bar mean despite model approval; its visible developer review blocks adoption
and preserves the original faulty-code result. The constant-price diagnostic is
a narrow invariant, not a universal semantic verifier. No strategy is promoted
automatically into the analyst or any execution system.

Stop an active workshop after its current request, then explicitly resume saved
roles. Service restarts mark active jobs interrupted; they do not issue new paid
calls automatically. Changed code/input locks refuse resume. Failed jobs retain
their evidence. Offline validation completes its bounded run. Start extensions
when main research is idle; use one service/CLI executor for a workspace.

## Capture and reproduce

From the repository root with the configured Python environment:

```sh
python scripts/data/probe_rpc.py
python scripts/evaluation/prepare_signal_corpus.py
python scripts/evaluation/run_research_lab.py signals
python scripts/evaluation/run_research_lab.py workshop --rounds 2
python scripts/evaluation/replay_signal_evaluation.py \
  --run runtime/research_lab/runs/RUN_ID/artifacts \
  --corpus data/validation/signals_v1 --output reports/signal-replay.json
```

Capture is explicit, rate-limited and resumable until frozen. It refuses to
overwrite a frozen corpus. Missing/conflicting bars fail capture, not zero-fill.
The versioned protocol is `examples/evaluation/signal_protocol_v1.json`; a later
study must use a separately named protocol/corpus/output. The default covers
2020-01-01 through2026-10-16 exclusive; do not relabel it as current live data.
Source exports deliberately exclude captured data and paid research artifacts.
New users capture their own corpus before starting these optional jobs.

Saved-source replay verifies original artifact hashes, blocks socket connects,
loads the archived implementation, rechecks the corpus manifest and compares
every case. Library versions are recorded; cross-version numerical equivalence
is not assumed. Memory/news evaluation is separate and retains its earlier limits.

## Plug-in boundaries and engines

`research_agent.research_lab` owns the catalog, corpus loader, evaluator, workshop
and optional API/service. Main quantitative methods and main tools are untouched.
Set `AETHER_RESEARCH_LAB_ENABLED=false` in private local configuration and restart
to hide/disable new jobs. The registration in `server.py`, one script include and
one collapsed panel are the integration points; no schema migration is required.

Generated code has a pure prefix-to-weight contract: no imports, network, file
access, introspection, recursion or orders. It runs in a credential-free child
with CPU/wall-clock bounds. Linux uses an address-space bound; this macOS runtime
uses sampled peak-RSS guarding because DATA/AS limits are rejected. This is an
operational safeguard for reviewed local research code, not a hostile-code sandbox.
The main analyst keeps its authorized general terminal/Python flexibility.

The teacher's [Argo framework](https://github.com/rxtech-lab/argo-trading) and
[macOS app](https://github.com/rxtech-lab/argo-trading-macOS) were inspected.
Argo requires Go/WASM setup; the local Go toolchain is absent. An adapter would
port the pure strategy boundary and reproduce input cutoffs, delayed fills,
allocation caps, cost/exit conventions and common-window results before enabling
it. No Argo run is simulated or reported as accepted. Existing Deep Agents stays
the business harness; alternative agent SDKs are future choices.

RPC now has original-time integrity-checked cache reuse, conservative pacing,
bounded429/transient backoff and adaptive small contiguous log batches. A
low-load probe records observed access separately from unknown provider maxima.
Historical state authorization remains a reported gap; no paid access is bought.

## Method references

- [Coinbase candle contract](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles)
- [Coinbase public REST limits](https://docs.cdp.coinbase.com/exchange/rest-api/rate-limits)
- [Ethereum JSON-RPC](https://ethereum.org/developers/docs/apis/json-rpc/)
- [Block-hash log filtering](https://eips.ethereum.org/EIPS/eip-234)
- [Temporal splitting](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)
- [Model-dependent feature importance cautions](https://scikit-learn.org/stable/modules/permutation_importance.html)
