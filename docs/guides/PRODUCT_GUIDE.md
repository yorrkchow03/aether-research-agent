# Aether — personal crypto research desk

Open **http://127.0.0.1:8765/** while the local service is running. The project lives at `/path/to/aether`.

## Start

Double-click `Start_Aether.command`, or run:

```bash
cd /path/to/aether
.venv/bin/python scripts/start_agent.py
```

Keep the service running. Computer sleep, shutdown, or stopping the server prevents scheduled research. No cloud deployment is required.

## Three everyday destinations

The main navigation has **Home**, **Research** and **Portfolio**. **More** contains
Strategy Lab and Settings. Body and conversation text use a larger 16px scale,
with readable controls and a single-column mobile layout.

- **Home:** the Overview section combines ETH/BTC saved prices, completed-candle
  returns/risk, the ETH chart, latest research and recent publisher material.
  Switch 24H/7D/30D/90D or candles/line. Refresh requests market data; inspect the
  saved observation/retrieval time. News & events is a section within Home.
  Its search field searches saved material; Search live news explicitly starts
  an analyst research job. Click a source to inspect it. Earlier event versions
  remain in the timeline. More market indicators, activity and workspace status
  are expandable so the first screen stays focused.
- **Research:** Ask opens the actual conversation in the page. Enter a question,
  select an existing conversation or start New chat. Create a report starts an
  evidence-reviewed investment brief. Reports contains saved reports and their
  source links/comparison/evidence exports. Subscribe to updates opens scheduling;
  the Updates section manages once/daily/weekly briefs and interval monitors.
  Local computer/service uptime is required. Unchanged monitors do not call the
  model; notifications remain in the app.
- **Portfolio:** manually record USD funding, ETH/BTC trades/transfers, fees and
  known transferred cost basis. Import/export CSV, void/restore corrections,
  refresh marks and inspect FIFO realized/unrealized P&L. Before you buy checks
  intended notional against recorded cash, visible depth, concentration and a
  supplied conditional loss limit. It does not place an order or verify outside
  accounts. Unknown basis stays unknown.

Within Research, **Research preferences** is optional and collapsed by default.
Supply horizon, budget, existing exposure, loss tolerance and preferences when
useful, then Save context. Blank amounts stay unknown. Fees/slippage are labelled
scenario assumptions. Historical scenarios is another optional section for
same-window buy/hold, staged entry and momentum paper comparisons. It is separate
from actual portfolio profit and from a trained Strategy Lab experiment.

**More → Strategy Lab** trains local logistic/tree research candidates on real
completed OHLCV, with chronological holdout/common-cost baseline comparisons and
saved inspectable artifacts. Optional Jev needs its separate TypeSafe key and
otherwise stays unconfigured. **More → Settings** shows model/harness, source
connections and saved historical/memory evaluation results.

The analyst chooses tools, API parameters, original pages, terminal/Python and
calculations. Important current answers use submit_research_answer; reports use
publish_report. Failed work retains sources/drafts and can be explicitly continued.
Reloading or reconnecting does not restart model research. Stop requests local
cancellation; an in-flight network request may finish first.

Click a report or answer's numbered source to read the claim beside saved review
passages, support limits and source data. Technical provenance is expandable.
Report comparison preserves literal statement/source/judgment changes. An earlier
report or saved event classification is context, not fresh ground truth.

Conditional advice links evidence, applicability, counterpoints, invalidation and
review triggers. Historical comparisons require actual source/experiment records,
case end, differences and transfer limits. Retrospective capture is labelled;
missing evidence remains a gap. No profitable investment edge is established.

Home's badge counts unseen saved activity. The News badge counts returned saved
news rows (currently capped at30), not unread articles. There is no separate
Bitcoin workspace; BTC is a market comparison. Legacy today/overview/news/report
and watchlist hashes resolve into the corresponding new workspace sections.

A disconnected service keeps the saved view with a banner and Reconnect action.
Keys remain private on the local server. New analyst research is English by
default; original publisher language and historical reports are preserved.

## Harness and tools

The business loop is local **Deep Agents 0.7.23 / LangGraph**, backed by **`doubao-seed-2.1-pro`** at the authorized Ark **`/api/plan/v3`** endpoint. Codex develops this application but does not perform its business tool loop. The service never falls back to Lite or the extra-billing `/api/v3` endpoint.

The research skill guides source selection, scope, dates, calculations, contrary evidence, and uncertainty without requiring a fixed analysis checklist. Convenience adapters are optional. `request_public_api`, `search_web`, `fetch_webpage`, `read_evidence`, real `execute`/Python, and `register_calculation` allow custom research. Calculations are saved with inputs, units, method, and recipe hashes. Facts are checked mechanically, then by a separate-context Pro reviewer before publication. Exact source excerpts are inserted by code, not freely generated by the author.

## Memory

LangGraph SQLite checkpoints retain each conversation. Shared `runtime/knowledge/knowledge.sqlite` stores canonical evidence receipts, versioned documents, FTS news retrieval, source-linked event revisions, and report history. Original responses and recipes are saved under shared `raw/` and `recipes/`. Reports and snippets are context; fresh factual claims need actual underlying evidence.

URL/content deduplication handles repeated captures. The agent associates multiple sources and corrections using an event ID; ambiguous event grouping remains an analyst judgment. Capture time and declared publication time are separate. As-of recall returns the locally available version; it does not establish when a publisher first made the article public. Historical evaluation uses a separate frozen harness with current network, terminal, and shared user memory unavailable.

User research preferences live separately from shared market/news evidence. No embedding service is required for this initial SQLite/FTS approach. Dense retrieval can be evaluated later against a labelled recall set.

Current queried news recall ranks local character TF-IDF relevance; broad scans
may use coverage selection. MMR/coverage and context budget are available through
the terminal SDK for comparison. Explicit saved event updates are packed with
older evidence; repeated URL/text demand is weighted. This is lexical retrieval,
not proof of independence, contradiction or truth. Its context cost is estimated.
Reviewed answer summaries also persist as research context with underlying source
IDs; they are not new factual evidence or current personal preferences.

## Local permissions and boundaries

The real terminal runs on the host in the session workspace; it is not an OS sandbox. Credentials are excluded from the child environment and logs/exports are redacted. The server listens only on loopback and checks Host, Origin, and local mutation tokens. This personal architecture is not a multiuser production isolation boundary; an actor with host write access can change local files or databases.

No real trading or exchange-account synchronization is included. Portfolio P&L uses manually recorded transactions; local trained strategy models are research baselines, not demonstrated professional forecasting models. Sparse source coverage and unknown publication times remain visible. Source support is inspectable AI judgment, not independent truth. Replaying code proves reproducibility, not a profitable strategy.

See [data](DATA_GUIDE.md), [validation](VALIDATION_AND_AUDIT.md), [investment methods](../architecture/INVESTMENT_COMPONENTS.md) and [testing](../development/TESTING.md).


Teacher-feedback extension update: see [RESEARCH_EXTENSIONS.md](RESEARCH_EXTENSIONS.md) for the optional signal validation/workshop, real data, negative results and candidate review block. Earlier verification remains scoped to its recorded release.


## Report review and corrections

See [Reading reports](READING_REPORTS.md) for recent changes, folded numeric
fields, source passages, dated review notices and the simple concern form.
