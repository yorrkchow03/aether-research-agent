# Investment components

## Local trained models

Completed Coinbase daily USD OHLCV is fetched in bounded pages, with original bytes/hashes. Normalization rejects conflicting overlaps and missing intervals. Canonical history/experiment records retain replay recipes. Current retrieval is retrospective, not authenticated point-in-time data.

Ten lagged features describe returns, volatility, relative volume, moving-average distance, drawdown and range. The target is delayed daily **open-to-close direction**, not a quoted target price or holding-period profit. A full daily bar separates feature cutoff and execution. Training includes only labels whose availability is strictly before the decision cutoff.

A chronological 60/20/20 split separates development, selection and later evaluation. Boundary labels are purged. Candidates are regularized logistic regression with training-only scaling and a shallow random forest. Earlier validation Brier selects the family; the holdout uses expanding refits. Parameters/trees, folds, decisions, inputs, config, environment and artifact hashes are saved as JSON; no uploaded pickle is loaded.

Scores include Brier, log loss, direction/balanced accuracy, probability bins and a constant-prior baseline. Bins diagnose error; they do not establish calibration. Repeated experiments weaken holdout independence. Long/cash probability bands, volatility cap and maximum exposure are declared research assumptions.

Buy/hold, staged entry, momentum and trained-model simulations share capital, evaluation dates and per-side costs, include terminal liquidation and show differing realized exposure. Daily-open fills are idealized; latency/impact are not modeled. News/funding is not backfilled without reliable historical availability. This is a quantitative baseline system, not demonstrated professional alpha; richer specialist models need evaluation against these baselines on untouched point-in-time data.

## Private ledger and purchase previews

The Decimal ledger supports USD funding and ETH/BTC trades/transfers. Buy fees enter FIFO basis; sell fees reduce proceeds. Missing transferred basis stays unknown. Marks carry source IDs and observation/retrieval times; missing basis/marks yield unknown P&L/valuation.

Imports are atomic, identical external references deduplicate and conflicts fail. Voiding/restoring is reversible and validates the resulting accounting. As-of replay uses economic dates under the **current corrected ledger**, not historical knowledge of when entries were entered. Native-asset fees, staking, derivatives, FX and taxes are outside scope.

Purchase previews use saved visible asks and compare intended notional against recorded cash, concentration, depth and a supplied conditional stress/loss budget. Fees apply to intended notional even if book coverage is partial. External holdings remain unknown. No orders are placed.

The analyst can read a private snapshot but has no ledger-writing tools. Reports citing it include private data in local exports. Jev refuses private portfolio records.

## Optional Jev

The adapter uses TypeSafe's [System One API](https://docs.typesafe.ai/api) with a separate credential and no fallback. One bounded public-source question is evaluated against explicit support/relevance/event-status criteria. Typed choices, probabilities and actual model identity are checked; invalid results fail, low confidence abstains, and redacted receipts retain success/failure.

Jev cannot replace arithmetic, date comparisons or price forecasting. Confidence thresholds are assumptions, not calibrated guarantees. Original evidence and publication review remain necessary. Without `TYPESAFE_API_KEY`, the UI shows unconfigured and no request occurs.

References: [TypeSafe quickstart](https://docs.typesafe.ai/introduction/quickstart), [citation checking](https://docs.typesafe.ai/cookbooks/citation_check), [temporal splits](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html), [logistic regression](https://scikit-learn.org/stable/modules/linear_model.html#logistic-regression).
