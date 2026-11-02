---
name: quantitative-analysis
description: Analyze saved OHLCV, returns, volatility, drawdown, liquidity or cross-asset relationships using Python. Use for custom indicators, numeric comparisons and reproducible calculations.
---
# Flexible quantitative analysis

Choose the method from the question. Use existing Python libraries or write a
small script; each possible indicator does not need its own tool.

1. Read [the data SDK reference](../research/references/sdk.md) if you need inputs.
   Inspect the returned record and its file before writing analysis. Live candles
   have series with close_usd/observed_at/period_end; archive collections have
   records with values. A latest ID is one row, not the full historical series.
2. State the asset, units, sample frequency, completed interval boundaries and
   window. N close-to-close returns need N+1 closes. Check gaps, duplicates and
   asset alignment; do not silently treat irregular rows as consecutive days.
3. Use execute and Python. Put exploratory code under ./notes. You can import
   research_agent.simulation or research_agent.metrics, inspect their source,
   adjust parameters or choose another appropriate method. Do not install packages
   or change the host system for routine research.
4. For citable output, write /notes/*.py accepting saved input JSON paths through
   sys.argv[1:]. Print exactly one JSON object with numeric values and matching
   units; optional meta describes assumptions, missingness and method details.
   Use register_calculation to rerun on actual evidence IDs and retain lineage.
   Registration checks the self-contained recipe in a fresh working directory.
   Read its declared sys.argv input snapshots; exploratory notes and a live SDK
   connection are not implicit inputs. Capture new data before registration.
5. A reference recipe is available at scripts/market_statistics.py. Copy it to
   notes before registration. It accepts one saved live candle record; inspect it
   before use. Choose your own window/method where appropriate.

Do not multiply a fraction by 100 in a report's numeric value field. Format it as
a percentage only in prose. Do not replace missing data with zero. A snapshot
cannot establish a historical extreme. Explain formulas, costs, windows and
limitations; successful replay does not establish prediction quality.
