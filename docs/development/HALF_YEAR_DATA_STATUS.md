# Half-year research library: delivery status

The available-data snapshot has passed the complete offline integrity audit and is frozen. Its required-core acceptance remains **false**, with all source-specific missing fields and original-text gaps retained. The independent separate-folder restore, all tracked hashes and all 50 dataset queries also passed. No data-lane process remains active.

## Fixed scope and artifacts

- Window: `[2026-04-26T00:00:00Z, 2026-10-26T00:00:00Z)`: 183 days / 4,392 closed hours per ETH/BTC asset. ETH primary, BTC reference; Ethereum, Arbitrum, Base and Optimism ecosystem scope.
- Working library: `data/library-halfyear-20260426-20261026/`.
- Frozen snapshot: `data/datasets/aether-halfyear-20260426-20261026-v1/`.
- Frozen `DATASET_MANIFEST.json` SHA-256: `ba0b2f632a687cd5daee5c22ca0e873c09b0831110407657a51ade598a205638`.
- Frozen `research.sqlite` SHA-256: `188357649ad35b3862cd7d45ae208bd291d288a5dcba5bf8dbb4248db7ab0edc`.
- Delivery evidence: `local/halfyear/data/DELIVERY_SUMMARY.json`, frozen `ACCEPTANCE.json`, `COVERAGE.json`, `SOURCES.json`, `DISCOVERY.json`, `GAPS.json` and the backup/dataset manifests.
- No active acquisition writer. All ten configured sources completed their bounded archive/listing discovery. Original bodies are separately assessed; completed discovery is not a claim of full body access or global news recall.

## Acquired and verified

| Data | Actual coverage / scope |
| --- | --- |
| Bitstamp ETH/USD and BTC/USD hourly OHLCV | Each 4,392 formal hours + 720 separately marked warmup hours; no missing formal interval |
| Bitstamp hourly descriptive features | Each 4,392 formal rows, derived only from consecutive saved prefixes |
| Coinbase daily OHLCV | Each 183 formal days + 91 warmup days |
| Coinbase hourly OHLCV | Each 4,387 / 4,392; five exact May 8 hours missing, retained as a source gap |
| Coinbase hourly features | Each 3,667 rows; five absent source hours and 720 subsequent incomplete lookback points remain unknown |
| CoinGecko reference price / cap / volume | Each asset 183 native daily observations |
| Coin Metrics available ETH network / supply fields and exchange flows | 183 daily rows; unavailable fields remain absent rather than zero |
| Four-chain TVL / stablecoins / DEX volume | All twelve series cover 183 daily observations |
| Aave V3 fees / revenue and named stETH / Aave WETH yields | Each 183 native daily/date-covered observations; protocol scopes stay distinct |
| ETH ETF flows | All 126 equity trading dates; Columbus Day is a trading day, July 3 is not |
| NYFed EFFR | All 126 bank business dates; July 3 is observed, Columbus Day is not |
| Official Treasury 2-year / 10-year par-yield references | Each 125 Treasury market dates; independent `macro.treasury.cmt2.daily` / `cmt10.daily`, not a FRED/H15 acquisition claim |
| FOMC calendar | Four official scheduled meetings in the fixed window; no daily zeros |
| OKX ETH-USDT-SWAP OI | 1,400 consecutive available hours, August 28 16:00 through October 25 23:00 UTC; 1,399 exact preceding-hour changes verified, first change unknown |
| OKX funding | 292 actual settlements, July 20 16:00 through October 25 16:00 UTC; earlier history unavailable |
| Etherscan gas / burn and explicit arithmetic fee-per-transaction calculation | Each 183 daily observations |
| Current execution snapshot | One separately timestamped Coinbase top-25 book / execution-cost scenario; not historical depth |

Registered contracts: **50**, of which **38 are complete under their own definitions**. Independent venues/providers are never spliced into the original Coinbase, OKX or FRED datasets. `market.bitstamp.{eth,btc}.hourly` is the complete available hourly price reference; `analysis.bitstamp.{eth,btc}.hourly` is its corresponding feature series.

An optional `analysis.eth.base_fee_ratio_proxy.daily` provides 183 publisher-burn/gas ratios in wei/gas. It preserves the original burn decimal string, integer gas, both input IDs/hashes, implementation object and display-unit precision. On the 30 previously verified monthly native days, all gas counts match exactly and all ratios are compatible with publisher display precision. Whole-window publisher burn scope remains explicitly uncertain. This ratio is **not** the required arithmetic block-mean base fee or a replacement for blob/consensus/supply-burn metrics. See `RATIO_PROXY_VALIDATION.json`.

## Text and saved research

Stored: 8,339 document identities / 8,944 original versions. Effective query view: **8,301 original-text entries**, including forum posts, report pages and attachments; these counts must not be described as 8,301 unique news articles. Revisions, duplicate-text links and original acquisition dates are preserved.

| Source | Effective original-text entries |
| --- | ---: |
| Ethereum Foundation | 178 |
| Bitcoin Core | 5 |
| Aave Governance | 1,177 |
| Lido Research | 952 |
| Ethereum Magicians | 4,353 |
| Coinbase Institutional | 34 |
| CoinDesk | 0; metadata only |
| Decrypt | 1,547 |
| SEC | 3 |
| Federal Reserve | 52 |

Decrypt's official archive was paginated through 21 pages and all 2,045 in-window candidates were processed, without a fixed article cap. SEC's short RSS stopped at September 3; three official descending archive pages reached April 8 and supplied the necessary six-month discovery coverage. Excluded candidates and their selection reasons remain in `DISCOVERY.json`.

The library imports 10 actual saved reports and 7 actual saved answers, with 81 citation links / 64 distinct resolvable evidence IDs, 2,349 imported evidence rows and 9,380 historical aliases. One previously reused workspace ID retains explicit competing saved versions and per-report links. No historical reports or user holdings were invented.

## Residual requirements

Required-core completion is not claimed. The twelve unresolved registered contracts are the two Coinbase hourly series and two corresponding hourly-feature series, OKX OI/funding/funding-statistics history, two FRED/H15 Treasury series, partially entitled Coin Metrics network/supply series, and the native arithmetic daily base-fee series.

- Coinbase omits `2026-05-08T02:00` through `06:00` UTC for both assets. Two bounded overlap checks returned no replacement. Bitstamp is an explicitly independent complete reference, not a fill for those IDs.
- OKX earlier history remains missing. Only fifteen sparse holes inside its actually available OI range were probed and repaired; 2,992 formal early hours were not queried one by one. No extrapolation or zero-fill.
- Coin Metrics `FeeMeanNtv`, signed burn and `eth_cl` staking/validator fields require unavailable entitlement; `GasUsed` was not available from that community source. Separately labelled public gas and arithmetic fee references remain available.
- H15 returned HTTP 200 with an empty body in two bounded parameter probes. This is an acquisition failure, not an observation or a holiday. Official Treasury par-yield alternatives remain separate datasets.
- A half-year raw execution-header sweep was not launched under the agreed storage/resource policy. The required arithmetic block mean remains missing; the optional burn/gas ratio does not close it. Historical feeHistory/staking access was not purchased or requested again.
- CoinDesk retains 771 candidate metadata entries under access-policy review; no article body or snippet is counted as full text.
- Four other text outcomes remain explicit: an Ethereum short/challenge page; a Coinbase report attachment host requiring review; a Coinbase report with unresolved publication date; one Decrypt HTTP 404. These are not all paid-access issues. Exact URLs and reasons are in frozen `DISCOVERY.json` / `GAPS.json`.

## Temporal and integrity boundaries

Opening an existing `ResearchLibrary(..., create=False)` reads that library's immutable `window_start/window_end`; `start/end/days/hours` are instance properties. The old defaults and global monthly constants remain unchanged. The original monthly v4 SQLite still matches its published SHA-256 `13805dfb0e31bdf8ca1a95a1b0655e38a5978d209ba4628687ed9f04b55b9e51`; no redundant native monthly rescan was run. See `MONTHLY_COMPATIBILITY.json`.

Publication dates and first local acquisition stay separate. Normal observed mode is retrospective reconstruction. Strict Ethereum queries through the historical October 26 cutoff return zero because these originals were actually acquired October 27. Raw provider responses can contain wider history; historical evaluation must use clipped normalized evidence packs, not unrestricted raw-file access. Publisher factual truth and publication/version history are not independently authenticated.

A later valid page can currently remove a document's previous quarantine relation during reconciliation. The library preserves active exclusion effective times, but does not provide a complete journal of deleted quarantine states. Current historical text cutoff zero-counts are unaffected; do not claim full replay of every former quarantine/restoration decision.

## Verification and operational receipts

- Final freeze audit: zero errors; 45,114 observations, 6,412 raw objects, 10,762 Coinbase candles, 16,484 feature recomputations and 8,944 text versions checked. `integrity_passed=true`, `text_discovery_complete=true`, `core_complete=false`, `all_acceptance_passed=false`.
- Source/query integration: actual full hourly references, first/middle/last independently recomputed 24-hour returns per asset, original bytes/hash and Ethereum text retrieval, and all 64 distinct saved-output citation IDs. `QUERY_INTEGRATION.json`.
- Cached real-source replay with network forbidden: four Bitstamp/Treasury datasets add no captures, raw objects or observations. Counts remain captures5,584 / objects6,412 / observations45,114 / observation-capture links46,599. `CACHED_REPLAY.json`.
- Fourteen half-year/HTTP/source tests and 59 existing library regressions passed at the data-lane checkpoint. Parent owns later combined application/integration results.
- The independent separate-folder restore passed: all 6,419 tracked files retain their hashes; all 50 dataset queries, coverage, text query and saved-research query are equal; restored integrity and portable legacy raw/recipe paths passed. `RESTORE.json`. The actual price→text→raw-source→calculation→citation integration also passed against the restored library, `RESTORED_QUERY_INTEGRATION.json`. No acquisition or verification process remains active.

The Decrypt performance repair reused HTTP clients only inside `TextCollector.run`, preserving the host lock, one request in flight, 1.1-second request-start pacing, public headers, 45-second timeout and original retry bounds. Offline tests cover reuse, close and continued processing after another source fails. The authorized single-writer handover preserved/rehashed all 8,162 then-existing versions and 5,340 objects and passed SQLite integrity; old PID58118 exited before new PID60381 resumed. The resumed process skipped all 784 already successful URLs and later completed normally. No SIGKILL, second writer, shortened source scope, model call, service restart, purchase or schedule was used. Receipts: `CRAWLER_DIAGNOSIS.json`, `HTTP_HANDOVER.json`, `TEXT_PROGRESS_AFTER_REUSE.json`.

## Manual query and update

```bash
.venv/bin/python -m research_data.library coverage \
  --root data/library-halfyear-20260426-20261026

.venv/bin/python -m research_data.library series \
  --root data/library-halfyear-20260426-20261026 \
  --dataset market.bitstamp.eth.hourly --limit 10000

.venv/bin/python -m research_data.library search \
  --root data/library-halfyear-20260426-20261026 \
  --query Ethereum --mode strict --known-by 2026-10-26T00:00:00Z

# Explicit cached/resumable updates. Never launch a second same-source writer.
.venv/bin/python -m research_data.library collect \
  --root data/library-halfyear-20260426-20261026 \
  --groups bitstamp,treasury,derive
.venv/bin/python -m research_data.library texts \
  --root data/library-halfyear-20260426-20261026
```

```python
from research_data.library.store import ResearchLibrary
lib = ResearchLibrary("data/library-halfyear-20260426-20261026", create=False)
prices = lib.series("market.bitstamp.eth.hourly", limit=10000)
news = lib.search_documents("Ethereum", source="ethereum", limit=10)
original = lib.evidence(news["results"][0]["record_id"])
raw = lib.raw(original["library_capture_id"])
```

A frozen snapshot must remain unchanged. Incremental updates target the working library, and a new freeze uses a new destination. Existing default monthly libraries, immutable evaluations, private state and old evidence IDs remain available; no production application configuration or public upload was performed by this data lane.
