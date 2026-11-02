# Data and time semantics

Live sources are discovered through `list_data_sources` and `research_agent.live`. The dashboard shows availability per source. Original responses, canonical records, recipes and retrieval times are saved under ignored `data/live/` and `runtime/knowledge/`. Provider failures retain visible gaps or explicitly labelled older values; unknown fields are never zero-filled.

| Data | Typical source | Scope to preserve |
| --- | --- | --- |
| ETH/BTC quotes and completed candles | Coinbase | Single venue, USD, completed UTC bars; rolling ticker volume differs from per-bar volume |
| TVL, stablecoins, DEX/protocol activity | DefiLlama | USD stock versus actual volume/flow, market and chain coverage |
| Funding and OI | OKX | Single venue and contract, explicit native/contracts/USD units |
| Visible book costs | Coinbase | Saved top-25 asks, not a guaranteed fill or historical depth |
| Network and exchange-labelled transfers | CoinMetrics / public RPC | Provider labels and fixed-block scope; transfers are not verified buying/selling |
| News, announcements, events | Publisher feeds, Ark search, original pages | Search excerpt versus independently captured original; publication and event dates differ |
| Strategy history | Paged Coinbase daily OHLCV | Current retrieval of completed history; retrospective, not authenticated historical availability |

Public endpoints can be limited by region, rate limits or provider plans. Some consensus/burn/validation metrics, comprehensive liquidation feeds and full macro/ETF histories may remain unavailable. Use a scoped alternative or record a gap; do not replace a full-market metric with a narrow proxy under the same name.

## Historical archive

A clean checkout contains no downloaded archive. Current tools and Strategy Lab work independently. To prepare an optional immutable historical bundle:

```bash
python scripts/data/prepare_data.py --as-of YYYY-MM-DD --days 365
python scripts/data/check_data.py
python scripts/data/inspect_data.py --help
```

`data/latest.json` points to the selected archive. The cutoff-aware SDK distinguishes observed-time reconstruction from strict eligibility requiring known availability before cutoff. Current retrieval does not prove a historical public release time; most reconstructed public observations must not be described as strict point-in-time evidence. Training/evaluation labels and future rows are explicitly kept outside the feature cutoff.

Portfolio transactions are user records, separate from public evidence. FIFO accounting is documented in [investment components](../architecture/INVESTMENT_COMPONENTS.md). No user's holdings are distributed or inserted as demo data; synthetic examples live in `examples/` and `tests/fixtures/` only.
