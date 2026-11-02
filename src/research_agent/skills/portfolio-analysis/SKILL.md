---
name: portfolio-analysis
description: Analyze the user's manually recorded portfolio, P&L, costs, concentration and hypothetical position sizing. Use for holdings-related research, purchase scenarios and loss-budget questions.
---
# Portfolio and purchase scenarios

Read get_research_context for supplied horizon/budget/risk/cost parameters and
get_private_portfolio for actual user-entered ledger records and source-timed
marks. An empty ledger means no imported records; external wealth is unknown.
Never infer a portfolio, budget or risk tolerance from missing fields.

The ledger preserves FIFO and fee accounting. Reuse its saved fields as the
accounting input. Unknown basis stays unknown. No exchange synchronization or
order execution is implied by a ledger balance.

Use terminal Python for custom P&L comparisons, concentration, sizing constraints
or stress assumptions. Inspect supported fields rather than guessing them. Read
[the SDK reference](../research/references/sdk.md) to use
ResearchClient().preview_position(asset, notional, **assumptions) for the existing
visible-book/fee/concentration/stress estimate. You can choose another method
using the original portfolio and orderbook evidence and register_calculation.

Show assumptions, units and gaps. The preview uses the full intended purchase
amount for fees/stress even when displayed depth cannot cover it. Thresholds and
stress declines are supplied scenarios, not empirically justified trading rules.
Do not confuse orderbook estimates with an actual fill or execution guarantee.

For a citable custom result, save a /notes/*.py recipe, accept saved record JSONs
via sys.argv[1:], emit values/units, then register_calculation on the actual IDs.
The SDK preview's orderbook inputs can support its cost fields; register any
additional portfolio-derived calculation needed by publication.

Disclose that a report citing a portfolio record includes private user summary
data in its local export. Use facts, contrary evidence and conditional decision
criteria; never submit orders or modify transactions through research code.
