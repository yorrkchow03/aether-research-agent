---
name: strategy-research
description: Design retrospective strategy tests, run saved baseline simulations or local statistical model experiments, and compare chronological holdout results with costs. Use for strategy hypotheses and model validation.
---
# Strategy experiments and historical validation

Use this skill when a strategy comparison or predictive hypothesis is actually
requested. A market/news summary does not require a model experiment.

Read [the SDK reference](../research/references/sdk.md) for data capture and
persisted experiment operations. Reuse the tested Python modules instead of
rewriting their numerical implementation blindly. You may choose other methods.

## Baseline simulation

Fetch completed candles and inspect the saved series. For the existing baseline
comparison use ResearchClient().simulate(price_record_id, profile, lookback_days)
or the CLI simulate operation. It saves a citable result. Inspect costs and
execution timing: prior-data signals, next-open execution, terminal liquidation.
Use research_agent.simulation.compare_strategies directly for exploratory work;
register a saved-input recipe when you need to cite custom results.

## Statistical model experiment

ResearchClient().start_strategy(config) starts a saved experiment visible in
Strategy Lab; read it later with client.strategy(run_id). It runs the existing
local logistic/tree candidates on captured real OHLCV. Do not repeatedly poll or
launch duplicates. Inspect an earlier saved experiment before starting another.
Use the persisted evidence_record_id and original inputs when citing results.

Choose asset, history_days, budget_usd, fee_bps, slippage_bps and allocation_cap
only from the supported StrategyConfig fields; inspect the
schema with StrategyConfig.model_json_schema() instead of guessing names. These
are scenario assumptions. You can also import run_experiment for custom local
research and register a recipe linking its actual input records.

## Evaluate honestly

Define the hypothesis and splits before inspecting the holdout. Prevent future
features/labels from entering earlier decisions, respect available labels and
delayed execution, account for fees/slippage, and compare common-window baselines.
Use calibration/Brier as well as directional accuracy and realized simulated
returns/drawdowns. Repeated tuning weakens holdout independence. Forecast
probabilities are estimates, not facts. An inferior or inconclusive result should
be reported plainly. No experiment establishes future profitability by itself.
