# Monthly library acceptance — 2026-10-27

The current immutable available-data delivery is **v4**:
`data/datasets/aether-month-20260926-20261026-v4/`.
Its fixed formal window is **[2026-09-26 00:00, 2026-10-26 00:00) UTC**.
ETH is the research asset and BTC the reference. The original v1/v2/v3 folders
remain dated snapshots; they must not be relabelled with v4 counts.

**Available-data integrity and restoration pass. Full required-core acceptance
does not pass.** The manifest state is
`available_data_with_explicit_unresolved_requirements`, with `core_complete=false`.
The user elected to retain unavailable paid/credentialled sources as explicit
gaps. No service was purchased and no missing value was filled with zero.

## Numerical coverage

There are **46 registered dataset contracts:44 complete and2 partial**. These
counts describe source contracts, not46 independent investment signals.

| Group | Verified available coverage |
|---|---|
| Prices | ETH/BTC each720 closed Coinbase USD hourly candles, plus30 preceding days; daily source references preserve venue/aggregate scope and91 preceding days |
| Descriptive features |1,500 recomputed hourly/daily feature rows;30 ETH/BTC ratio/correlation rows; returns, volatility, drawdown, range, volume and moving-average deviations retain formula/input versions |
| Derivatives |720 formal hourly OKX ETH-USDT-SWAP OI observations,90 funding settlements,720 OI changes and90 interval-aware funding-statistic rows |
| Activity and supply |30 daily available active-address/transaction/fee/supply/issuance/change observations; source-specific unavailable fields remain null |
| Original Ethereum headers |215,232 actual mainnet execution headers in3,363 input batches; full fixed range and boundary parents checked;30 daily base-fee/gas/block-count/execution-burn aggregates |
| Fee burn |30 separate daily execution-plus-blob fee-burn observations from original headers and pinned official mainnet fork parameters; not net supply or staking |
| Flows |Source-scoped exchange flows and20 ETH ETF US trading dates |
| DeFi |30 daily TVL, stablecoin scale and DEX-volume observations for Ethereum,Arbitrum,Base,Optimism; source-scoped Aave fees/revenue and named stETH/Aave WETH yields |
| Macro |Fed/NYFed rate series each19 business dates, two-/ten-year Treasury yields and FOMC calendar; release dates/calendar exclusions retained |
| Execution snapshot |Latest separately timed Coinbase top25 levels per side at2026-10-27T11:33:00.954344Z; labelled USD1,000 scenario before fees, limited-book coverage |

Warmup is excluded from default formal-window queries. Native frequencies,
trading calendars, currencies, contracts, source coverage and stock/flow meanings
remain distinct. No scheduled FOMC meeting overlaps the formal window; adjacent
meetings and in-window published minutes are retained without daily zero rows.

An independent raw-header audit verified heights, parents, timestamps, chain,
UTC boundaries and exact sums. Gas agrees exactly with the independent Etherscan
daily CSV on30/30 days. Execution burn agrees within the publisher-displayed
precision on30/30 days, with0 unexplained differences. Both original values
remain unchanged. One original transaction receipt independently checks the
integer blob-price calculation; it is not a second full-month receipt audit.

## Original text and saved research

| Fixed source | Active original documents | Scope and remaining limit |
|---|---:|---|
| Ethereum Foundation |4|Publisher originals |
| Bitcoin Core |0|No matching item on the enumerated publisher blog; no claim that no Bitcoin event occurred |
| Aave Governance |161|Three empty nontext system-action posts are legitimate exclusions |
| Lido Research |162|Publisher forum posts |
| Ethereum Magicians |774|Publisher forum posts |
| Coinbase Institutional |5|Four HTML documents, including a labelled landing page, plus one distinct public37-page PDF; one publication date remains unknown |
| CoinDesk |0|392 dated URLs retained for access-policy review; metadata is not full text |
| Decrypt |264|Relevant originals; exclusions and archive-pagination outcomes retained |
| SEC |1|Relevant original public material |
| Federal Reserve |13|Relevant releases, minutes, speeches and testimony |
| **Total** |**1,384**|Current captured versions, not authenticated historical possession |

All10 discovery jobs completed their declared lists/pagination. This establishes
traceable discovery outcomes, not global news recall or licensed body coverage.
Seventeen formerly undated Coinbase candidates were resolved from the matching
publisher primary publicationDate to outside-window exclusions. CMS creation
clocks and related articles were not used as publication dates.

Actual corpus review repaired Coinbase navigation bleed and a Federal Reserve
minutes body truncated to its introductory paragraph. Re-extraction used the
same captured originals, versioned extractors and preserved earlier evidence IDs.
The Coinbase primary public PDF is separately hashed and linked to its landing
page. Digital-text extraction covers37 pages; no OCR or chart interpretation is
certified. A recovered PDF object-pointer warning and the independent Poppler
page/cover check are recorded. Landing summaries are not counted as full PDFs.

Actual earlier9 reports and7 answers are imported, with112 explicit claims,
125 claim/evidence links and70 output-level citation links. Old ambiguous context
aliases preserve distinct versions and report-specific links. Earlier AI reasoning
is identified as such, not promoted to independent market evidence. Original
2,314 evidence bodies,9 report bodies and7 answer bodies remain unchanged.

## Storage, retrieval and time

The working library uses raw content-addressed objects plus SQLite/FTS5;
observations, captures, article versions, discovery outcomes, computations,
claims and citations remain separate. The existing12 business tools and4 skills
remain unchanged;5 library operations extend the21-operation ResearchClient SDK.
No per-indicator LLM tool, database server, trade or new schedule was introduced.

The v4 audit checks7,727 raw-object hashes,11,460 numerical versions,
2,027 document versions,605 document relationships and preserved exclusions.
`SOURCES.json`, `DISCOVERY.json`, `COVERAGE.json` and `GAPS.json` document scope.

A real-corpus test exposed date-first truncation hiding an older exact EF notice.
Query ranking now uses title-weighted SQLite BM25 over only latest eligible
versions, filtering time/source/asset before corpus statistics. Later captures
cannot affect strict historical rankings. No-query browsing stays chronological.
Existing memory selection then limits context size. Ranking is lexical relevance,
not proof of factual truth or semantic agreement.

`observed` supports retrospective reconstruction. `strict` admits only versions
the system actually acquired by the host cutoff; caller overrides are clamped.
New backfill is therefore absent at the old window cutoff. Unfiltered saved-report
recall is disabled in strict mode. Revision/attachment edges require eligible
endpoints; later quarantines do not retroactively erase previously known versions.

## Verification and delivery evidence

- **300 offline regression tests pass**, including source/time/units, derivatives,
  native-header/fee math, eligible-corpus ranking and HTML/PDF extraction.
- Actual captured-data SDK, local Unix socket, HTTP, terminal calculation,
  input/evidence/raw-source resolution and strict-memory integration pass with
  **0 LLM calls**. This verifies data integration, not new report/model quality.
- The first v4 integration failed at subprocess JSON parsing despite exit0.
  Its traceback is retained. Direct reproduction and one bounded instrumented
  retry passed; the first cause is unconfirmed and no fix is claimed. The verifier
  now retains redacted terminal output for diagnosis.
- Actual separate-folder restore verifies **all7,734 tracked file hashes**,46
  dataset queries,coverage,text and saved-research queries,portable raw/recipe
  paths and restored integrity. Snapshot bytes remain unchanged.
- Exact configured-private-value scan passes **7,735 files**, including the
  manifest. No provider key is included in the data or source export.
- Complete header/fee cached reruns and publisher cached reprocessing grow
 0 objects/captures/observations/document versions. No network request is needed
  for completed cached work. Boundary, interrupted and versioned cases have
  dedicated regression coverage.
- Original private evidence/reports/answers and71 files across the three earlier
  frozen evaluation corpora retain their original data hashes. Historical code
  pins in old manifests are historical metadata, not claims about current code.
- Actual local production HTTP passes on the final98-file startup fingerprint,
  including720 candles,exact original-news recall,PDF raw hash and strict cutoff0.
  Model remains local Deep Agents0.7.23 + Doubao Pro at authorized `/api/plan/v3`.
  No active research was cancelled during the guarded idle refresh.

Private receipts reside in `local/data-library/month-20260926-20261026/`:
`RESTORE_V4.json`, `CREDENTIAL_SCAN_V4.json`,
`integration-v4-retry/RECEIPT.json`, `integration-v4-retry/TERMINAL_RESULT.json`,
`LIVE_SERVICE_V4.json`, `ORIGINAL_AND_FROZEN_FINAL.json`,
`regression-publisher-pdf.log`, `NATIVE_PROVIDER_REVIEW.json`,
`HEADER_FEE_IDEMPOTENCY.json`, `PUBLISHER_IDEMPOTENCY.json` and
`PDF_DEPENDENCY_RECEIPT.json`. Original failed receipts/logs are preserved.

## Unresolved required coverage

1. Coin Metrics network contract:community GasUsed is unsupported and FeeMeanNtv
   returns403. Gas exists separately from raw Ethereum headers/Etherscan;
   FeeTotNtv/TxCnt is an explicit mean-fee calculation. Alternatives do not rewrite
   the unavailable source-specific contract.
2. Coin Metrics supply contract:SplyBurntNtv returns403. Correct consensus asset
   **eth_cl** requests for SplyStkedNtv and ValidatorActOngCnt also return403.
   Network-wide staking/active-validator history is unavailable with current
   rights. stETH yield/TVL cannot establish total network stake. Native
   execution-plus-blob fee burn is a separate measure, not signed supply burn.
3. CoinDesk original bodies remain pending authorized access. One Coinbase
   annual-survey publication date remains unknown, so window eligibility is not
   invented. Discovery records preserve these outcomes.

The historical eth_feeHistory route remains permission-restricted, but the
allowed eth_getBlockByNumber alternative has already delivered the full native
month; a new RPC purchase is not required to use that available dataset. No
verified commercial quote exists and no paid service was activated. These core
coverage gaps prevent full acceptance even though available-data integrity passes.

## Handoff and reproduction

The separate dataset is v4 as named above; it is not bundled with public source.
The current source-only handoff is
`dist/aether-source-monthly-library-20261027-v4/` and its ZIP, with file hashes
and delivery receipts. It excludes private runtime state,credentials,databases,
publisher originals,local receipts and the upstream checkout. No GitHub push or
public publication was performed. Existing v1/v2/v3 and older ZIPs remain dated
artifacts. See the [library guide](../guides/RESEARCH_LIBRARY.md) for queries,
manual incremental collection,unique freeze destinations and restoration.

Hashes establish captured-byte integrity and provenance, not publisher truth,
causation,profitable trading or possession of today's backfill in the past.
