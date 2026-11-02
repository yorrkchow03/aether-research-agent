# Terminal research SDK

## Selected historical research library

When the project has a built library, inspect its catalog and coverage before
assuming that a historical metric or original article exists. The selected fixed
window, provider units, missing states and source acquisition times are explicit.
The catalog reports its actual selected interval; retained older libraries still
resolve earlier evidence IDs. These are SDK operations, not extra business tools:

```python
from research_agent.research_sdk import ResearchClient
client = ResearchClient()
client.call('library-datasets')
client.call('library-coverage')
packet = client.library_series('market.coinbase.eth.hourly')
articles = client.library_search('Ethereum upgrade', asset='ETH')
client.call('library-evidence', record_id=articles['results'][0]['record_id'])
```

Series/search accept start/end, mode='observed' or 'strict', known_by and
pagination. Observed mode is retrospective reconstruction. Strict mode excludes
versions acquired after known_by; it does not authenticate historical publisher
availability. Current market snapshots remain separate from the selected window.
Read original evidence IDs before using facts and register custom calculations
through the existing publication/source chain. General recall_memory also
includes library news acquired by the requested cutoff, using the existing
selection algorithm and correction constraints.

The same operations are available directly from the terminal CLI:

```bash
python -m research_agent.research_sdk library-datasets
python -m research_agent.research_sdk library-series --args '{"dataset_id":"market.bitstamp.eth.hourly","limit":10000}'
```

Library operation names belong to this local SDK, not a public provider URL.

`library-series` also saves the returned page under `data_file` and supplies a
`collection_record_id`. Use that series- ID as an input to register_calculation
when a recipe needs more than a few observation rows. Its JSON has `records`.
Pagination and `query_result_fully_materialized` describe the query result, not
whether the provider has every expected market observation. Check coverage and
continuity. Never fill one venue's gaps with another venue while keeping its ID.

News memory returns exact source excerpts and explicit context gaps. Large task
outputs remain in working-memory artifacts; summaries and past process corrections
are guidance, not source facts. Reopen cited originals before publication. Keep
personal amounts,holdings and risk inputs out of semantic source queries; use
generic public asset/event terms. Jev classification/source assessments are
derived,may abstain,and do not replace numeric/date checks or publication review.

Use this reference when choosing APIs, historical inputs or application services.
The active job attaches a local socket automatically. No provider key is passed
to the terminal. Calls use the same source storage as ordinary tools and appear
in the task trace. The connection ends with the job.

```python
import json
from pathlib import Path
from research_agent.research_sdk import ResearchClient

client = ResearchClient()
data = client.fetch("candles", "ETH")
print(json.dumps(data))
# Returned data_file uses virtual /data/...; in the shell use ./data/...
record = json.loads(Path("." + data["data_file"]).read_text())
print(record.keys())
```

Inspect returned structures before assuming fields. Source SDK calls provide IDs,
observation/retrieval times, units and archived response files. Raw public API
responses may need your own parsing; the SDK does not decide which analysis to do.

CLI equivalent (each command prints one JSON result):

```bash
python -m research_agent.research_sdk sources
python -m research_agent.research_sdk fetch --args '{"metric":"candles","asset":"ETH"}'
python -m research_agent.research_sdk public-api --args '{"provider":"okx","path":"/api/v5/market/history-candles","params":{"instId":"ETH-USDT","bar":"1D","limit":90}}'
```

Available operations and arguments:

| Operation | Arguments / purpose |
|---|---|
| sources | Discover adapter names and provider bases; same as list_data_sources |
| fetch | metric, asset="ETH"; normalized live adapter, choose names from sources |
| public-api | provider, path, params; generic credential-free public GET |
| archive-market | asset="ETH", days=30; fixed-cutoff archived prices, never current |
| archive-chain | metric, chain="Ethereum", days=30; bundle-only chain data |
| archive-events | query="", days=90, max_results=5; archived official posts |
| indicators | name="eth_30d"; optional saved descriptive indicator; empty name lists |
| eth-rpc | method, params; read-only Ethereum RPC, explicit bounded log ranges |
| protocol-activity | blocks=1000, end_block=null; Aave V3 Core WETH only |
| context | Supplied research parameters and clearly labelled scenario defaults |
| memory-news | query="", asset="ETH", cutoff=null, budget=2400, limit=8, policy=null, original_only=false, related_queries=null (up to2 local entity queries), previous_report=null (saved ID); select saved sources with bounded event/version/update context |
| memory-research | query="", limit=8; recall saved reports and source-reviewed answer summaries, with original evidence IDs |
| simulate | price_record_id, profile=null, lookback_days=7; saved baseline comparison |
| strategy-start | config; asynchronous persisted local model experiment |
| strategy-status | run_id=""; list runs or inspect one; do not busy-poll |
| position-preview | asset, notional, fee_bps=10, max_asset_fraction=.5, stress_decline_fraction=.3, max_loss_usd=null |

For operations without a convenience method use client.call(operation, **arguments).
Historical archives may be absent on a clean install. Current captures cannot
prove what was publicly available at a past cutoff. eth-rpc never sends orders.

News recall uses local character TF-IDF and relevance selection for queries,
coverage selection for broad empty-query scans, with explicit
same-origin/repeated-text weighting and saved event revision dependencies. It is
lexical evidence packing, not a neural semantic model or proof of corroboration.
The budget measures an estimated serialized-text cost, not provider tokens.
Inspect selection gaps and candidate_pool_at_limit; try other terms or query the
original source when recall is incomplete. Policies relevance, mmr and recent
are available for comparisons; they share the same cutoff and candidate pool.
Read selected original records before using them in an answer. A review receipt
does not transform old plans into confirmed outcomes or establish price effects.
Research recall also retains source-reviewed answer summaries across sessions.
Those summaries are previous AI reasoning, not new market facts. Follow their
source_evidence_ids to the original source or experiment; do not cite the summary
as independent evidence or reuse old personal context as a current preference.

The reusable calculations are ordinary Python modules: research_agent.simulation
(market_statistics, compare_strategies, loss_scenarios), research_agent.metrics
(descriptive market/depth helpers), research_agent.investment.strategies
(StrategyConfig, run_experiment). Inspect their signatures/source before custom
use; they are examples of tested logic, not mandatory methods.

Publication, source verification and ledger transaction writes are not SDK
operations. Cite actual IDs, register custom recipes, and submit through the
normal source-checked answer/report tools.

## Recipe paths and saved inputs

The terminal runs at the real session workspace. In `execute`, create a recipe
as `./notes/name.py`; `/notes/name.py` would address the host filesystem root.
File tools such as `write_file` instead use the virtual `/notes/name.py` path.
`register_calculation` accepts `/notes/name.py`, `./notes/name.py`, or an actual
absolute path under this session's notes directory. Do not pass a host absolute
path to `write_file`: it is a virtual-path tool. Check that a write actually
succeeded; a trailing successful `echo` does not prove an earlier write succeeded.

For library series, every returned row ID represents that observation only.
A sum over a month needs all contributing row IDs, directly or through the saved
collection_record_id, not just the first and last.
Read schema and `total`/pagination before aggregating. Monthly indicators already
stored with formula/input provenance can be cited directly; custom calculations
are optional. Publication and recipe registration remain ordinary model tools,
not methods on `ResearchClient`.

## Structured working memory and exact source cards

These are Python/CLI operations behind the existing terminal, not extra business
LLM tools. `memory-state` takes no arguments and returns the current task state.
`memory-note` accepts `statement`, `evidence_ids` and `kind` (`derived_claim`,
`question`, `next_check`). Derived claims require actual source IDs and remain
unverified. Neither operation can change the host task ID or certify a fact.

`memory-cards` accepts `query`, `asset`, `cutoff`, `limit`, and optional explicit
`entity_key`/`event_key`. To reopen one card, pass `card_id` and optional `cutoff`.
Cards retain source spans, hashes, acquisition time and dirty-summary status.
They are pointers into originals, not a replacement evidence namespace. New
indexes are partial and built incrementally. A historical strict cutoff excludes
later-acquired cards and links; a fresh index cannot pretend to have existed in
an earlier system snapshot.

`memory-research` now also accepts `cutoff`. It returns saved outputs and
attributed correction/update notices actually known by that point. The earlier
report is reasoning context, not a new market fact or current user profile.

For large inputs, redirect CLI JSON to a notes file or access ResearchClient from
Python and print only metadata/selected fields. Preserve the returned collection
ID as a recipe input; never substitute only endpoint IDs for a full series.

## Conditional historical cases

`memory-cases` reads reviewed prior hypotheses and all their source pointers.
It is a terminal service behind the existing memory capability, not an extra
model tool. Current facts still need original evidence. For example:

```python
packet = client.research_cases(
    "Ethereum governance proposal",
    context={"asset": "ETH", "horizon_days": 30,
             "conditions": {"event_status": "proposed"}},
    limit=4, budget_chars=16000,
)
for item in packet["results"]:
    print(item["case_id"], item["case"]["hypothesis"], item["match"])
# Reopen an entire case by identity, then read its original evidence IDs:
# client.call("memory-cases", case_id=the_returned_id)
```

Keys in `conditions` are labels actually saved in a reviewed explanation; choose
them from returned cases or the current source. Unknown values use `None`.
The score exposes keyword coverage, exact condition matches, asset and horizon
similarity. It is a navigation score, not a financial probability. Mismatches,
unknowns and negative outcomes are retained. Complete cases that do not fit the
budget are listed in `budget_skipped`; increase the budget or read one case.

Default `mode="strict"` requires both the case and original sources to have been
acquired by `cutoff`. Explicit `mode="retrospective"` is a reconstruction and
does not prove historical system knowledge; archived strict jobs cannot override
their host cutoff. Later outcomes have separate clocks and never affect ranking.
The host creates cases only after actual successful source review. This SDK
cannot create cases, write outcomes, change acquisition clocks or store private
portfolio/user-context lineage as reusable public research.
