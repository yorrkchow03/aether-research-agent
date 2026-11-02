# Monthly research library

The library adds a source-preserving historical data layer to Aether. It uses
SQLite/FTS5 and content-addressed files, with no additional database service.
The analyst still uses local Deep Agents/LangGraph, the configured Doubao Pro
Agent Plan endpoint, its existing skills, terminal and publication review.
No per-indicator LLM tools, trading operations or background schedules were added.

## Current delivery

The frozen monthly dataset is **v4**, with46 dataset contracts(44 complete),
ETH/BTC720 formal hours each,215,232 original Ethereum headers and1,384 original
text documents. Available-data integrity,300 regressions,real SDK/HTTP/terminal
integration and actual separate-folder restoration pass. Full required-core
acceptance remains false because source-specific numerical and publisher-access
gaps remain. See [acceptance](../development/MONTHLY_LIBRARY_ACCEPTANCE.md).
This data task made0 LLM calls and does not re-evaluate report-generation quality.

A separately selected [half-year library and memory upgrade](HALF_YEAR_MEMORY.md)
extends the interval without modifying this monthly snapshot. Its available-data
acceptance and source-specific gaps are tracked separately.

The latest current orderbook snapshot is dated2026-10-27T11:33:00.954344Z; it is
not a historical hour in the fixed month. Coinbase HTML article/landing pages
and the separate public37-page Charting Crypto PDF retain their distinct kinds,
publisher clocks,raw hashes and parent relation. PDF text extraction is not OCR
or verified chart interpretation. Today-acquired sources remain retrospective.

## Fixed scope and meanings

The formal window is **[2026-09-26 00:00, 2026-10-26 00:00) UTC**. ETH is the main
asset and BTC the reference. Coinbase USD candles are single-venue observations;
CoinGecko references are aggregate estimates. They must not be merged as though
they were the same price. OKX observations identify ETH-USDT-SWAP explicitly.
Four-chain DeFi series retain their chain, currency and protocol coverage.
Aave fees/revenue cover all chains represented by the source, not ETH holders.

Hourly prices include 30 preceding days; daily prices include 91 preceding days.
These support the earliest formal-window features, including 30-day returns and
90-return ETH/BTC correlation. Warmup records remain separately identified and
are excluded from the default formal-window query. Funding statistics group
actual settlement intervals and use only prefixes through the observation.
Returns and volatility are descriptive calculations, not investment forecasts.

Native frequencies are preserved. Stock metrics, daily flows, settlement rates,
provider snapshot times, US trading dates and calendar events remain distinct.
US ETF trading dates include Columbus Day; Fed/Treasury rate dates do not.
The FOMC calendar has no meeting overlapping the formal window; its source and
adjacent meeting dates are retained, without manufacturing daily zero rows.
Minutes are indexed by release date rather than the meeting date in their URL.
The orderbook is a separate current top-25 snapshot with a labelled USD1,000
execution scenario before fees. Its depth is a lower bound on the full book.

The optional `execution_headers` collector scans explicitly numbered Ethereum
mainnet execution headers through the public `eth_getBlockByNumber` method.
It uses batches of at most64 headers, paced requests, bounded retries and an
atomic per-batch checkpoint. After the entire fixed range and its boundary
parents have been checked, it publishes daily arithmetic mean base fee
(`network.ethereum.base_fee.daily`) and separate daily gas/block-count/execution
base-fee burn (`network.ethereum.execution.daily`). Execution burn is
`sum(baseFeePerGas * gasUsed) / 1e18`; it excludes blob burn, consensus issuance
and slashing. It cannot establish total staked ETH or silently replace a
source-specific signed supply-burn series. Original responses include transaction
hashes and require several GB; the collector checks disk space before starting.
Partial-month input batches remain marked incomplete and publish no daily values.

After that scan, `fee_burn` optionally derives a separate
`network.ethereum.fee_burn.daily` series: execution burn, blob fee burn, their
sum and the consumed blob gas. It reads the same original headers and an
archived mainnet chain specification pinned to an official client commit.
EIP-4844 integer pricing uses the actual BPO2 schedule for this fixed window;
unrecognized active forks or missing blob fields stop calculation rather than
becoming zero. This total is **fee burn**, not supply change, staking balance,
consensus penalties or issuance. Existing execution-only values retain their
original contract and identities.

## Storage

```text
data/library/
  research.sqlite             # typed series, FTS, provenance, jobs, versions
  objects/<sha-prefix>/<sha>.bin

data/datasets/aether-month-20260926-20261026-v4/
  research.sqlite
  objects/
  BACKUP_MANIFEST.json
  ACCEPTANCE.json
  COVERAGE.json
  DATASET_MANIFEST.json
  SOURCES.json
  DISCOVERY.json
  GAPS.json

local/data-library/month-20260926-20261026/
  before-library/              # original knowledge/raw/recipe backup
  backup-manifest.json
  ...                         # preflight, failure and verification receipts
```

The v4 folder already exists; freeze commands below deliberately use a new
unique destination. Never overwrite an accepted snapshot. The working library
can be updated manually. A frozen dataset is a separate
copy. Neither is included in the public source ZIP. Redistribution of publisher
material requires its own review of source terms. Existing private state,
original IDs and frozen evaluation corpora are retained.

Tables separate sources/dataset contracts, objects/capture receipts, observation
versions, document/version/FTS records, discoveries, collection jobs, original
research outputs/citations and relations. Identical bytes share one object.
Repeated requests retain acquisition receipts; equal observations do not create
new facts. Changed values or original pages create versions. Removed or invalid
items are quarantined by relation, not silently overwritten or erased.

Old report dependencies are imported from their actual saved files. An older
context ID with divergent saved versions is an explicit alias with variants and
report-specific `citation_version:<ID>` relations. It is not rebound to an
arbitrarily selected preference record. Old preferences cannot prove a current
budget or holding.

## Query examples

From the project directory, using its virtual environment:

```sh
.venv/bin/python -m research_data.library coverage
.venv/bin/python -m research_data.library series --dataset market.coinbase.eth.hourly --limit 3
.venv/bin/python -m research_data.library search --query Ethereum --source ethereum --limit 5
.venv/bin/python -m research_data.library series --dataset market.coinbase.eth.hourly --mode strict --known-by 2026-10-26T00:00:00Z
```

`observed` reconstructs historical observations from what was captured now.
`strict` returns only versions acquired by `known_by`. Today's capture is never
backdated to an article's publication date. Consequently this new backfill is
absent from a strict query ending at the old window cutoff. It supports
retrospective research; it does not establish what the system knew at that time.

Keyword queries use SQLite FTS5 BM25, weighting titles4 and body text1. The
temporary in-memory search corpus contains only the latest eligible versions
after asset/source/kind/publication/acquisition filters. Thus later acquisitions
and superseded text cannot influence historical corpus statistics. No-query
browsing remains chronological. This prevents newer generic posts from
crowding an older relevant original out of the memory candidate pool; the
existing context-budget selector runs afterwards. These are lexical relevance
scores, not factual-confidence scores. Strict host tasks also clamp SDK/tool
news-memory cutoffs and reject report recall without a point-in-time filter.

The agent's existing terminal can use its job-scoped ResearchClient:

```python
from research_agent.research_sdk import ResearchClient
c = ResearchClient()
c.call("library-datasets")
c.call("library-coverage")
# Current snapshots are outside the historical default window:
c.library_series("execution.coinbase.eth.snapshot", end="2026-10-28T00:00:00Z")
rows = c.library_series("market.coinbase.eth.hourly", limit=24)
news = c.library_search("Ethereum", source="ethereum", limit=5)
source = c.call("library-evidence", record_id=news["results"][0]["record_id"])
```

These five operations extend the SDK, not the LLM business-tool list. The
library is attached on application startup only if its database exists. New
queries do not capture data implicitly. Current research can read beyond the
old archive cutoff; historical/strict tasks retain their cutoff constraints.
Existing memory selection can retrieve library documents while respecting its
acquisition cutoff and evidence budget. It does not insert all raw pages into
the context. Evidence can be materialized into the existing source store on use.

Read-only local HTTP routes are `/api/library/datasets`, `/api/library/coverage`,
`/api/library/series` and `/api/library/documents`. Saved AI research can be searched with
`/api/library/research` and inspected with `/api/library/research/{output_id}`,
including original claims, citation links and legacy version relations. Existing
`/api/evidence/{record_id}` resolves old and library IDs. Series support
`dataset_id,start,end,mode,known_by,limit,offset`; document queries also support
`query,asset,source,kind`. Only `127.0.0.1`/`localhost` are served. Start normally
with `.venv/bin/python scripts/start_agent.py` if the application is not running.

`/api/library/relationships?record_id=...` exposes saved revision and provenance
relations; use `known_by` to restrict document-version edges to both endpoints
known at that time. These links distinguish changed original content from
metadata revisions and conservative exact normalized-text matches. They do not
infer publisher corrections, copying direction or semantic agreement. A later
quarantine does not retroactively erase a document that was available before
the recorded exclusion. Evidence reads remain available for source audits;
quarantined evidence is rejected when entering new agent research.

## Manual collection and continuation

```sh
.venv/bin/python -m research_data.library init
.venv/bin/python -m research_data.library collect --groups candles,references,network,derivatives,defi,etf,macro,derive
.venv/bin/python -m research_data.library texts --groups ethereum,bitcoin_core,aave,lido,magicians,coinbase_institutional,coindesk,decrypt,sec,federal_reserve
.venv/bin/python -m research_data.library import-legacy
.venv/bin/python -m research_data.library collect --groups snapshot
# Explicit optional backfill; do not launch if this group is already running:
.venv/bin/python -m research_data.library collect --groups execution_headers
# Only after the complete header scan has passed its source audit:
.venv/bin/python -m research_data.library collect --groups fee_burn
```

The time window is fixed in the library contract. To change it, create a separate
versioned implementation/library rather than changing this database silently.
Reruns reuse successful raw captures and rescan the listing/pagination from its
start, skipping identical facts and completed original texts. The saved job
cursor is progress evidence; continuation also relies on immutable response
caches. Interrupted runs do not require downloading completed pages again.
`--refresh` explicitly requests new source responses and preserves prior
versions. Request pacing, bounded retries and per-source failures allow the
remaining groups to proceed. Use one writer per collection group. No daemon
or new scheduler was installed.

The execution-header job resumes from its saved cursor and original raw cache.
Do not start a duplicate writer, delete its checkpoint or treat quiet output as
a stopped process. Check `ethereum-header-backfill` and the corresponding data
jobs in coverage. The generic collection command can preserve a group failure
and still exit normally; a zero exit code alone is not proof of complete coverage.

## Verification, freezing and restoration

```sh
.venv/bin/python -m research_data.library verify --output local/library-audit.json
.venv/bin/python scripts/dev/verify_monthly_library.py
.venv/bin/python -m research_data.library freeze --output data/datasets/aether-month-20260926-20261026-next
.venv/bin/python -m research_data.library backup --output local/library-backup-unique-name
```

Freeze refuses active collection jobs or integrity failures and refuses to
replace an existing destination. Incomplete source coverage produces a snapshot
labelled `available_data_with_explicit_unresolved_requirements`; this is not a
claim of full core acceptance. Coverage distinguishes acquired observations,
legitimate non-observation dates, pending publication and unresolved missing
requirements. Source-specific fields with public alternatives remain separate.
The existing `memory-research` SDK operation also recalls imported reports and
answers; their conclusions are earlier AI reasoning, not independent facts.

To restore, first verify every path/hash in `DATASET_MANIFEST.json` (or
`BACKUP_MANIFEST.json` for a plain backup), copy the folder to a new location,
and open it with `ResearchLibrary(path, create=False)`. Run verification and
compare coverage/query results before replacing a working library. Paths for
restored raw objects and recipes resolve inside the restored folder. Restart
Aether to attach a deliberately replaced library. Never restore over an active
writer or the old knowledge database.

The actual v4 restore verification can be inspected in
`local/data-library/month-20260926-20261026/RESTORE_V4.json`. To reproduce it,
choose an unused restore folder and receipt path:

```sh
.venv/bin/python scripts/dev/verify_library_snapshot.py --snapshot data/datasets/aether-month-20260926-20261026-v4 --restore local/library-restore-new --receipt local/library-restore-new.json
```

This verifies original/restored hashes,all dataset queries,text/saved-research
queries,portable legacy raw/recipe paths and independent restored integrity.
No download,paid call or model inference is needed. Keep the dataset separate
from the source-only ZIP; copy it only where source terms and private state allow.

Automated audit checks raw hashes, exact publisher numeric values across data
groups, interval/calendar coverage, feature recomputation and original text
extraction. Source-integrity checks do not prove publisher truth or market
profitability. Unavailable article bodies are never counted as full text.
For execution headers, the audit independently rereads original RPC receipts,
checks chain identity, requested heights, parent links and UTC boundaries, and
recalculates the daily values without trusting the collector's saved sums.
See the dataset's acceptance, discovery and gap files for actual current status.
