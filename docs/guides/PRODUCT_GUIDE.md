# Product workflow

Aether is a personal local application. A fresh checkout begins with empty local sources, reports, chats and portfolio records.

## Home and Analyst

Home displays saved ETH and BTC data and source times. Refresh explicitly collects missing public material and may invoke the configured model. Ask ordinary questions in Analyst, or select Create report to save a cited report. Give an asset, start and end dates, a time convention and focus topics. Use New chat for independent research.

## Library and reports

Library searches saved source text and numeric series. Date ranges are UTC, start inclusive and end exclusive. The known-by cutoff excludes sources acquired later. Reports provides the saved result, source details, PDF and portable audit package. Source support checks do not certify a publisher's truth or an investment outcome.

## Portfolio and Strategy Lab

The Portfolio example is editable hypothetical arithmetic. My local ledger contains only transactions explicitly recorded or imported. Price marks are saved by default; refreshing them is explicit. No order is placed. Strategy Lab trains or reviews research only after explicit actions. Review its dates, held-out Brier score, baseline and fees before interpreting returns.

Optional signal experiments need a frozen corpus. Use `python scripts/data/prepare_signal_corpus.py --help` and inspect the protocol in `examples/evaluation/signal_protocol_v1.json` before collecting data. This operation fetches public market history. Saved experiments are not bundled with the application.

## Local state

Your `.env`, `data/` and `runtime/` remain local and are ignored by Git. Keep the process running for enabled local schedules. Closing it stops scheduling. See [Local setup](LOCAL_SETUP.md) for configuration and maintenance.
