"""Download, normalize, inventory and verify an ETH research data bundle."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

from . import __version__
from .http import Fetcher, SourceError, dumps, utcnow
from .normalize import (CM_MAP, aave_topics, coingecko, coinmetrics, ethereum_events, etf_flows,
                        fed_rates, funding, iso, llama_chart, llama_stable, llama_tvl, number, plain_text, record, timestamp)

ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc


class Preparation:
    def __init__(self, as_of: str, days=365, project_root=ROOT):
        if not 31 <= days <= 365:
            raise ValueError("days must be between 31 and 365 (CoinGecko Demo lookback)")
        self.cutoff = datetime.strptime(as_of, "%Y-%m-%d").replace(tzinfo=UTC)
        if self.cutoff > datetime.now(UTC):
            raise ValueError("The requested cutoff is in the future")
        self.start = self.cutoff - timedelta(days=days)
        self.days = days
        self.root = project_root
        suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        self.run_dir = project_root / "data/runs" / suffix
        self.run_dir.mkdir(parents=True, exist_ok=False)
        (self.run_dir / "datasets").mkdir()
        self.fetcher = Fetcher(self.run_dir)
        self.datasets = {}
        self.job_results = []

    def request(self, name, url, **kwargs):
        return self.fetcher.fetch(name, url, **kwargs)

    def save(self, dataset_id, rows, *, kind="daily", units=None, scope="", semantics="", notes=None, required=False):
        rows = list(rows)
        if kind != "snapshot":
            rows = [r for r in rows if self.start <= timestamp(r["observed_at"]) and timestamp(r.get("period_end") or r["observed_at"]) <= self.cutoff]
        seen = set()
        for r in rows:
            identity = (r["observed_at"], r.get("url")) if kind == "events" else r["observed_at"]
            if identity in seen:
                raise ValueError(f"Duplicate observation time in {dataset_id}: {identity}")
            seen.add(identity)
        rows.sort(key=lambda r: r["observed_at"])
        path = "datasets/" + dataset_id + ".jsonl"
        body = "".join(dumps(r, sort_keys=True) + "\n" for r in rows).encode()
        (self.run_dir / path).write_bytes(body)
        flags = sorted({f for r in rows for f in r["quality_flags"]})
        missing = {key: sum(r["values"].get(key) is None for r in rows) for key in {k for r in rows for k in r["values"]}}
        metadata = {
            "dataset_id": dataset_id, "kind": kind, "scope": scope, "units": units or {},
            "time_semantics": semantics, "path": path, "sha256": hashlib.sha256(body).hexdigest(),
            "record_count": len(rows), "first_observed_at": rows[0]["observed_at"] if rows else None,
            "last_observed_at": rows[-1]["observed_at"] if rows else None,
            "latest_effective_time": max((r.get("period_end") or r["observed_at"] for r in rows), default=None),
            "missing_values": missing, "quality_flags": flags, "notes": notes or [], "required": required,
            "replay_mode": "reconstructed_observations; not strict point-in-time",
        }
        if kind == "snapshot":
            metadata["notes"].append("Only a retrieval-time snapshot; not usable as historical state.")
        elif kind == "daily" and rows:
            lag = (self.cutoff - timestamp(metadata["latest_effective_time"])).total_seconds() / 86400
            metadata["lag_days_at_cutoff"] = lag
            if lag > 3:
                metadata["quality_flags"] = sorted(set(metadata["quality_flags"] + ["stale_or_short_coverage"]))
            dates = {timestamp(r["observed_at"]).date() for r in rows}
            span = (max(dates) - min(dates)).days + 1
            metadata["missing_calendar_days_inside_span"] = span - len(dates)
        self.datasets[dataset_id] = metadata
        return rows

    def market(self, asset, coin_id):
        query = urlencode({"vs_currency": "usd", "days": self.days, "interval": "daily"})
        headers = {}
        if os.environ.get("COINGECKO_DEMO_API_KEY"):
            headers["x-cg-demo-api-key"] = os.environ["COINGECKO_DEMO_API_KEY"]
        snap = self.request(f"coingecko_{asset}", f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?{query}", headers=headers)
        return self.save(f"market.coingecko.{asset}.daily", coingecko(snap, asset), required=True, units={"price_usd": "USD", "market_cap_usd": "USD", "rolling_24h_volume_usd": "USD"},
                         scope=f"{asset.upper()} aggregated market data", semantics="00:00 UTC price observation; volume is a rolling 24h measure, not an additive daily candle",
                         notes=["Live non-midnight tail samples are discarded.", "Unauthenticated access worked in this run; official Demo access documents an API key requirement."])

    def cm(self, asset="eth", metrics=None, dataset_id=None, optional=False):
        metrics = metrics or ["PriceUSD", "AdrActCnt", "TxCnt", "SplyCur", "IssTotNtv", "FeeTotNtv"]
        dataset_id = dataset_id or f"network.coinmetrics.{asset}.daily"
        query = urlencode({"assets": asset, "metrics": ",".join(metrics), "frequency": "1d", "start_time": self.start.date().isoformat(),
                           "end_time": (self.cutoff - timedelta(days=1)).date().isoformat(), "page_size": 1000})
        snap = self.request("cm_" + asset + "_" + "_".join(metrics), "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?" + query, optional=optional)
        units = {CM_MAP[m]: ("count" if m in ("AdrActCnt", "TxCnt", "ValidatorActOngCnt") else "USD" if m == "PriceUSD" else "ETH") for m in metrics}
        return self.save(dataset_id, coinmetrics(snap, dataset_id), units=units, scope=f"Coin Metrics {asset} network metrics", required=(asset == "eth" and not optional),
                         semantics="Provider UTC daily label, conservatively eligible after label+1 day; price convention differs from CoinGecko",
                         notes=["Community data is for non-commercial use with attribution.", "FeeTotNtv includes fees; it is not an ETH burn metric.", "Active addresses are not unique people.", "Exchange-flow metrics depend on the provider's exchange-address labels, can be revised, and do not directly measure investor buying or selling."])

    def tvl(self, chain):
        snap = self.request("llama_tvl_" + chain.lower(), f"https://api.llama.fi/v2/historicalChainTvl/{chain}")
        return self.save(f"ecosystem.tvl.{chain.lower()}.daily", llama_tvl(snap, chain), required=chain == "Ethereum", units={"tvl_usd": "USD"}, scope=f"DeFiLlama chain TVL: {chain}",
                         semantics="Provider dated TVL observations", notes=["USD TVL changes include asset price effects; not net inflows.", "Chain TVL excludes liquid staking and double-counted TVL per endpoint documentation.", "Do not add L1 and L2 TVL as a deduplicated total."])

    def stable(self, chain):
        snap = self.request("llama_stable_" + chain.lower(), f"https://stablecoins.llama.fi/stablecoincharts/{chain}")
        return self.save(f"ecosystem.stablecoins.{chain.lower()}.daily", llama_stable(snap, chain), required=chain == "Ethereum", units={"circulating_value_usd": "USD", "usd_pegged_circulating_value_usd": "USD"},
                         scope=f"Stablecoin circulating value on {chain}", semantics="Provider dated stock observation", notes=["Values use totalCirculatingUSD, not mixed native currencies.", "Supply growth does not prove buying ETH or external net inflows."])

    def dex(self, chain):
        url = f"https://api.llama.fi/overview/dexs/{chain}?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true"
        snap = self.request("llama_dex_" + chain.lower(), url)
        return self.save(f"ecosystem.dex_volume.{chain.lower()}.daily", llama_chart(snap, f"ecosystem.dex_volume.{chain.lower()}.daily", "dex_volume_usd"), required=chain == "Ethereum",
                         units={"dex_volume_usd": "USD/day"}, scope=f"DeFiLlama covered DEX volume on {chain}", semantics="UTC day aggregate; excluded until the day finishes")

    def protocol_fees(self, data_type):
        snap = self.request("llama_aave_v3_" + data_type, f"https://api.llama.fi/summary/fees/aave-v3?dataType={data_type}")
        field = "fees_usd" if data_type == "dailyFees" else "revenue_usd"
        dataset = "protocol.aave_v3." + field + ".daily"
        return self.save(dataset, llama_chart(snap, dataset, field), units={field: "USD/day"}, scope="Aave V3 across all source-covered chains and markets",
                         semantics="UTC day aggregate", notes=["This series is not Ethereum-only, not Ethereum base fees and not ETH token-holder revenue."])

    def funding_history(self):
        all_rows = []
        after = None
        for page in range(4):
            params = {"instId": "ETH-USDT-SWAP", "limit": 400}
            if after: params["after"] = after
            snap = self.request(f"okx_funding_{page}", "https://www.okx.com/api/v5/public/funding-rate-history?" + urlencode(params))
            items = snap.json()["data"]
            if not items: break
            all_rows.extend(funding(snap))
            oldest = min(int(i["fundingTime"]) for i in items)
            if oldest <= int(self.start.timestamp() * 1000): break
            if after == str(oldest): raise ValueError("Funding pagination made no progress")
            after = str(oldest)
        # Deduplicate overlapping page boundaries; never assume an 8-hour settlement interval.
        unique = {r["observed_at"]: r for r in all_rows}
        return self.save("derivatives.okx.eth_usdt.funding", unique.values(), kind="timeseries", units={"funding_rate": "fraction/settlement", "realized_rate": "fraction/settlement"},
                         scope="OKX ETH-USDT-SWAP only", semantics="Actual funding settlement timestamps", notes=["Public endpoint retains about three months; source has no older records in this API.", "Positive funding is not by itself a buy/sell signal."])

    def open_interest(self):
        snap = self.request("okx_open_interest", "https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=ETH-USDT-SWAP")
        item = snap.json()["data"][0]
        row = record("derivatives.okx.eth_usdt.open_interest", Decimal(item["ts"]) / 1000,
                     {"oi_contracts": item["oi"], "oi_eth": item["oiCcy"], "oi_usd": item["oiUsd"]}, snap, "/data/0", available_at=snap.metadata["retrieved_at"], flags=["venue_specific", "snapshot_only"])
        return self.save(row["dataset_id"], [row], kind="snapshot", units={"oi_contracts": "contracts", "oi_eth": "ETH", "oi_usd": "USD"}, scope="OKX ETH-USDT-SWAP", semantics="Live provider snapshot")

    def orderbook(self):
        snap = self.request("coinbase_eth_orderbook", "https://api.exchange.coinbase.com/products/ETH-USD/book?level=2")
        obj = snap.json()
        bids = sorted(((Decimal(p), Decimal(q)) for p, q, *_ in obj["bids"]), reverse=True)
        asks = sorted((Decimal(p), Decimal(q)) for p, q, *_ in obj["asks"])
        if not bids or not asks or bids[0][0] >= asks[0][0]: raise ValueError("Empty or crossed order book")
        mid = (bids[0][0] + asks[0][0]) / 2
        values = {"best_bid_usd": bids[0][0], "best_ask_usd": asks[0][0], "spread_bps": (asks[0][0] - bids[0][0]) / mid * 10000,
                  "bid_depth_1pct_usd": sum((p*q for p,q in bids if p >= mid * Decimal("0.99")), Decimal(0)),
                  "ask_depth_1pct_usd": sum((p*q for p,q in asks if p <= mid * Decimal("1.01")), Decimal(0))}
        row = record("execution.coinbase.eth_usd.orderbook", obj.get("time") or snap.metadata["retrieved_at"], values, snap,
                     {"bids": "/bids", "asks": "/asks", "time": "/time"}, available_at=snap.metadata["retrieved_at"], flags=["snapshot_only", "venue_specific"])
        return self.save(row["dataset_id"], [row], kind="snapshot", units={k: "basis_points" if k == "spread_bps" else "USD" for k in values}, scope="Coinbase Exchange ETH-USD order book", semantics="Live book snapshot",
                         notes=["Depth is specific to this venue and this instant; it is not historical liquidity or a guaranteed execution price.", "Exchange trading fees require the user's venue/tier; not inferred from public book data."])

    def finalized_block(self):
        snap = self.request("ethereum_finalized_block", "https://ethereum-rpc.publicnode.com", payload={"jsonrpc": "2.0", "id": 1, "method": "eth_getBlockByNumber", "params": ["finalized", False]})
        obj = snap.json()["result"]
        values = {"block_number": int(obj["number"], 16), "gas_used": int(obj["gasUsed"], 16), "gas_limit": int(obj["gasLimit"], 16),
                  "base_fee_wei_per_gas": int(obj["baseFeePerGas"], 16), "execution_base_fee_burn_eth": Decimal(int(obj["baseFeePerGas"],16) * int(obj["gasUsed"],16)) / Decimal(10**18)}
        row = record("network.ethereum.finalized_block", int(obj["timestamp"],16), values, snap, "/result", available_at=snap.metadata["retrieved_at"],
                     flags=["snapshot_only"], extra={"block_hash": obj["hash"]})
        return self.save(row["dataset_id"], [row], kind="snapshot", units={"block_number": "block", "gas_used": "gas", "gas_limit": "gas", "base_fee_wei_per_gas": "wei/gas", "execution_base_fee_burn_eth": "ETH/block"},
                         scope="Ethereum finalized execution-layer block", semantics="Block timestamp and hash", notes=["Burn formula: baseFeePerGas * gasUsed / 1e18.", "This is one block's execution base fee burn; excludes blob fees and consensus penalties, and is not daily total burn."])

    def yields(self):
        snap = self.request("llama_yield_pools", "https://yields.llama.fi/pools")
        rows = []
        selected = []
        for i, item in enumerate(snap.json()["data"]):
            if item["chain"] != "Ethereum": continue
            if item["project"] == "lido" and item["symbol"] == "STETH" or item["project"] == "aave-v3" and item["symbol"] == "WETH" and not item.get("poolMeta"):
                selected.append(item)
                # One pool per dataset avoids conflating distinct market scopes.
                dataset = "yields." + item["project"].replace("-", "_") + "." + item["symbol"].lower() + ".snapshot"
                row = record(dataset, snap.metadata["retrieved_at"], {"apy_percent": item["apy"], "apy_base_percent": item.get("apyBase"), "apy_reward_percent": item.get("apyReward"), "tvl_usd": item["tvlUsd"]}, snap,
                             f"/data/{i}", available_at=snap.metadata["retrieved_at"], flags=["snapshot_only", "protocol_specific"], extra={"pool_id": item["pool"], "project": item["project"], "symbol": item["symbol"]})
                self.save(dataset, [row], kind="snapshot", units={"apy_percent":"percent/year", "apy_base_percent":"percent/year", "apy_reward_percent":"percent/year", "tvl_usd":"USD"}, scope=f"{item['project']} {item['symbol']} on Ethereum",
                          notes=["APY is a provider estimate, not a promised return. Lido stETH is not total network staked ETH.", "Provider predictions are deliberately excluded from normalized records."])
        if not selected: raise ValueError("Selected Ethereum ETH yield pools were not found")
        for item in selected:
            history = self.request("llama_yield_history_" + item["pool"], "https://yields.llama.fi/chart/" + item["pool"])
            dataset = "yields." + item["project"].replace("-", "_") + "." + item["symbol"].lower() + ".history"
            rows = [record(dataset, obj["timestamp"], {"apy_percent": obj.get("apy"), "apy_base_percent": obj.get("apyBase"), "tvl_usd": obj.get("tvlUsd")}, history, f"/data/{i}", flags=["availability_unknown", "protocol_specific"])
                    for i, obj in enumerate(history.json()["data"])]
            self.save(dataset, rows, kind="timeseries", units={"apy_percent":"percent/year","apy_base_percent":"percent/year","tvl_usd":"USD"}, scope=f"Historical {item['project']} {item['symbol']} pool {item['pool']}", semantics="Provider snapshot timestamps; not assumed to be daily midnight")
        return selected

    def events(self, provider):
        if provider == "ethereum":
            snap = self.request("ethereum_blog_rss", "https://blog.ethereum.org/feed.xml")
            rows = ethereum_events(snap); dataset = "events.ethereum_blog"
        else:
            snap = self.request("aave_governance_topics", "https://governance.aave.com/latest.json")
            rows = aave_topics(snap); dataset = "events.aave_governance"
        return self.save(dataset, rows, kind="events", scope=provider + " published materials", semantics="Publication timestamps; content version is current retrieval version",
                         notes=["Published time is not event time. A proposal is not proof of approval or execution.", "Current content can have later edits, so these records are not historical point-in-time evidence."])

    def document(self, name, url, title):
        snap = self.request(name, url)
        text = plain_text(snap.text()) if "html" in snap.metadata["response_headers"].get("Content-Type", "") else snap.text()
        row = record("documents." + name, snap.metadata["retrieved_at"], {}, snap, "full_document", available_at=snap.metadata["retrieved_at"], flags=["snapshot_only"], extra={"title": title, "url": url, "text": text, "content_kind": "reference_document"})
        return self.save(row["dataset_id"], [row], kind="snapshot", scope=title, semantics="Retrieval-time document version")

    def etf(self):
        snap = self.request("farside_eth_etf_flows", "https://farside.co.uk/ethereum-etf-flow-all-data/")
        return self.save("flows.farside.eth_etf.daily", etf_flows(snap), units={"*_net_flow_usd":"USD/trading day"}, scope="US spot Ethereum ETF net flows as aggregated by Farside", semantics="US trading date, conservatively eligible after UTC day end",
                         notes=["Original values are US$ million, converted by 1,000,000.", "Incomplete ETF publications retain nulls; they are not treated as zero.", "This is a publisher aggregation, not independently reconciled issuer filings."])

    def macro(self):
        snap = self.request("federalreserve_h15", "https://www.federalreserve.gov/releases/h15/")
        return self.save("macro.federalreserve.h15.daily", fed_rates(snap), units={"effective_fed_funds_percent":"percent/year", "treasury_2_year_percent":"percent/year", "treasury_10_year_percent":"percent/year"},
                         scope="Federal Reserve H.15 recent business days", semantics="Date of rate observation, publication availability unknown", notes=["Recent five observations only; not a complete macro backtest series."])

    def fred(self):
        # A failed public download is retained in the source manifest, not replaced by guesses.
        snap = self.request("fred_macro_history", "https://fred.stlouisfed.org/graph/graph.csv?" + urlencode({"id":"DFF,DGS10,DTWEXBGS", "cosd":self.start.date().isoformat(), "coed":(self.cutoff-timedelta(days=1)).date().isoformat()}), optional=True)
        import csv, io
        items = list(csv.DictReader(io.StringIO(snap.text())))
        if not items or "DGS10" not in items[0]: raise ValueError("FRED did not return the expected CSV")
        rows = [record("macro.fred.daily", obj.get("observation_date") or obj.get("DATE"), {"effective_fed_funds_percent":obj.get("DFF"), "treasury_10_year_percent":obj.get("DGS10"), "broad_dollar_index":obj.get("DTWEXBGS")}, snap, f"CSV row {i+2}", period_end=timestamp(obj.get("observation_date") or obj.get("DATE"))+timedelta(days=1), flags=["availability_unknown","revised_history_possible"]) for i,obj in enumerate(items)]
        return self.save("macro.fred.daily", rows, units={"effective_fed_funds_percent":"percent/year","treasury_10_year_percent":"percent/year","broad_dollar_index":"index, Jan 2006=100"},scope="FRED DFF, DGS10, DTWEXBGS", notes=["Broad trade-weighted dollar index is not DXY.","Latest revised observations, not ALFRED historical vintages."])

    def jobs(self):
        jobs = []
        def add(name, fn, required=False): jobs.append((name, fn, required))
        for asset, coin_id in (("eth","ethereum"),("btc","bitcoin")):
            add("market_"+asset, lambda a=asset,c=coin_id:self.market(a,c), True)
        add("ethereum_network", self.cm, True)
        add("btc_reference_price", lambda:self.cm("btc",["PriceUSD"],"market.coinmetrics.btc.daily"))
        for metric in ("SplyBurntNtv","FlowInExNtv","FlowOutExNtv"):
            add("optional_eth_"+metric, lambda m=metric:self.cm("eth",[m],"network.coinmetrics.eth."+m+".daily",True))
        for metric in ("SplyStkedNtv","ValidatorActOngCnt","StkDepositTotNtv","StkWithdrawTotNtv"):
            add("optional_staking_"+metric, lambda m=metric:self.cm("eth_cl",[m],"staking.coinmetrics."+m+".daily",True))
        for chain in ("Ethereum","Arbitrum","Base","Optimism"):
            add("tvl_"+chain,lambda c=chain:self.tvl(c),chain=="Ethereum")
            add("stablecoins_"+chain,lambda c=chain:self.stable(c),chain=="Ethereum")
            add("dex_"+chain,lambda c=chain:self.dex(c),chain=="Ethereum")
        for typ in ("dailyFees","dailyRevenue"):
            add("aave_"+typ,lambda t=typ:self.protocol_fees(t))
        add("funding_history",self.funding_history)
        add("open_interest",self.open_interest)
        add("orderbook",self.orderbook)
        add("finalized_block",self.finalized_block)
        add("eth_yields",self.yields)
        add("ethereum_events",lambda:self.events("ethereum"))
        add("aave_events",lambda:self.events("aave"))
        add("etf_flows",self.etf)
        add("macro_recent",self.macro)
        add("macro_history",self.fred)
        add("fomc_calendar",lambda:self.document("fomc_calendar","https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm","FOMC official meeting calendar"))
        add("ethereum_supply_reference",lambda:self.document("ethereum_supply_reference","https://ethereum.org/en/developers/docs/intro-to-ether/","Ethereum ETH issuance and burn reference"))
        add("llama_api_reference",lambda:self.document("llama_api_reference","https://api-docs.defillama.com/llms-free.txt","DeFiLlama free API documentation"))
        return jobs

    def run_job(self, job):
        name, fn, required = job
        try:
            fn()
            result = {"name":name,"status":"ok","required":required}
        except Exception as exc:
            result = {"name":name,"status":"unavailable","required":required,"error":f"{type(exc).__name__}: {exc}"}
        print(dumps(result), flush=True)
        return result

    def load_rows(self, dataset):
        return [json.loads(line) for line in (self.run_dir/self.datasets[dataset]["path"]).read_text().splitlines()]

    def derived(self):
        for asset in ("eth","btc"):
            key = f"market.coingecko.{asset}.daily"
            if key not in self.datasets or not self.datasets[key]["record_count"]:
                # Keep distinct conventions visible rather than substituting silently.
                self.job_results.append({"name":"derived_metrics","status":"unavailable","required":True,"error":"Missing CoinGecko daily series; no silent provider substitution"})
                return
        eth = self.load_rows("market.coingecko.eth.daily")
        btc = {r["observed_at"]:r for r in self.load_rows("market.coingecko.btc.daily")}
        prices = []; ratios=[]; output=[]
        for row in eth:
            if row["observed_at"] not in btc: continue
            b = btc[row["observed_at"]]
            p = float(row["values"]["price_usd"]); bp=float(b["values"]["price_usd"])
            prices.append((row,b,p,bp)); ratios.append(p/bp)
            i = len(prices)-1
            values={"eth_btc_price_ratio":str(Decimal(row["values"]["price_usd"])/Decimal(b["values"]["price_usd"]))}
            refs=[row["record_id"],b["record_id"]]
            if i >= 7 and timestamp(row["observed_at"])-timestamp(prices[i-7][0]["observed_at"]) == timedelta(days=7):
                values["eth_return_7d_fraction"] = p/prices[i-7][2]-1
                values["eth_btc_ratio_return_7d_fraction"] = ratios[i]/ratios[i-7]-1
                refs.extend([prices[i-7][0]["record_id"],prices[i-7][1]["record_id"]])
            if i >= 30 and timestamp(row["observed_at"])-timestamp(prices[i-30][0]["observed_at"]) == timedelta(days=30):
                window=prices[i-30:i+1]
                logs=[math.log(window[j][2]/window[j-1][2]) for j in range(1,len(window))]
                values["eth_volatility_30d_annualized_fraction"] = statistics.stdev(logs)*math.sqrt(365)
                values["eth_return_30d_fraction"] = p/window[0][2]-1
                peak=window[0][2];mdd=0
                for w in window:
                    peak=max(peak,w[2]);mdd=min(mdd,w[2]/peak-1)
                values["eth_max_drawdown_30d_fraction"] = mdd
                refs.extend(w[0]["record_id"] for w in window)
            if i >= 90 and timestamp(row["observed_at"])-timestamp(prices[i-90][0]["observed_at"]) == timedelta(days=90):
                window=prices[i-90:i+1]
                er=[math.log(window[j][2]/window[j-1][2]) for j in range(1,len(window))]
                br=[math.log(window[j][3]/window[j-1][3]) for j in range(1,len(window))]
                if statistics.stdev(er) and statistics.stdev(br): values["eth_btc_return_correlation_90d"] = statistics.correlation(er,br)
                refs.extend(r["record_id"] for w in window for r in w[:2])
            rec={"record_id":hashlib.sha256((row["record_id"]+b["record_id"]+"metrics-v1").encode()).hexdigest()[:24],"dataset_id":"analysis.eth_btc.daily",
                 "observed_at":row["observed_at"],"period_end":None,"available_at":None,"retrieved_at":utcnow(),"values":{k:number(v) for k,v in values.items()},
                 "quality_flags":["derived","availability_unknown"],"evidence":row["evidence"]+b["evidence"],"input_record_ids":sorted(set(refs)),"calculation_version":"metrics-v1"}
            output.append(rec)
        self.save("analysis.eth_btc.daily", output, required=True, units={"eth_btc_price_ratio":"BTC/ETH","*_fraction":"fraction","eth_btc_return_correlation_90d":"dimensionless"},scope="ETH and BTC aligned CoinGecko daily observations",semantics="Same timestamps, only contiguous UTC calendar-day windows",
                  notes=["Volatility = sample standard deviation of 30 log returns * sqrt(365).","Correlation uses log returns, not price levels.","Drawdown is the minimum price/running-peak-1 within a 31-price window.","These are descriptive calculations, not investment recommendations."])
        network = "network.coinmetrics.eth.daily"
        if network in self.datasets:
            rows=self.load_rows(network); output=[]
            for prev, cur in zip(rows,rows[1:]):
                if timestamp(cur["observed_at"])-timestamp(prev["observed_at"]) != timedelta(days=1): continue
                a,b=prev["values"].get("supply_eth"),cur["values"].get("supply_eth")
                if a is None or b is None: continue
                r=dict(cur);r["values"]={"supply_change_eth":str(Decimal(b)-Decimal(a))};r["dataset_id"]="analysis.eth_supply_change.daily"
                r["record_id"]=hashlib.sha256((prev["record_id"]+cur["record_id"]+"supply-delta-v1").encode()).hexdigest()[:24]
                r["input_record_ids"]=[prev["record_id"],cur["record_id"]];r["evidence"]=prev["evidence"]+cur["evidence"]
                r["quality_flags"]=["derived","availability_unknown"];r["calculation_version"]="supply-delta-v1";output.append(r)
            self.save("analysis.eth_supply_change.daily",output,units={"supply_change_eth":"ETH/day"},scope="Changes in Coin Metrics ETH supply observations",semantics="Difference of adjacent daily supply observations",notes=["Supply delta is not gross issuance and is not an independently observed burn amount."])

    def build_database(self):
        db=sqlite3.connect(self.run_dir/"research.sqlite")
        db.executescript("CREATE TABLE datasets(dataset_id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL); CREATE TABLE records(record_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, observed_at TEXT NOT NULL, effective_time TEXT NOT NULL, available_at TEXT, payload_json TEXT NOT NULL); CREATE INDEX records_lookup ON records(dataset_id,effective_time); CREATE TABLE sources(source_id TEXT PRIMARY KEY,metadata_json TEXT NOT NULL);")
        for source in self.fetcher.sources:
            db.execute("INSERT INTO sources VALUES(?,?)",(source["source_id"],dumps(source)))
        for dataset, meta in sorted(self.datasets.items()):
            db.execute("INSERT INTO datasets VALUES(?,?)",(dataset,dumps(meta)))
            for row in self.load_rows(dataset):
                db.execute("INSERT INTO records VALUES(?,?,?,?,?,?)",(row["record_id"],dataset,row["observed_at"],row.get("period_end") or row["observed_at"],row["available_at"],dumps(row)))
        db.commit();db.close()

    def run(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.job_results=list(pool.map(self.run_job,self.jobs()))
        self.derived()
        self.build_database()
        required_failures=[j for j in self.job_results if j["required"] and j["status"]!="ok"]
        empty_required=[d for d in self.datasets.values() if d["required"] and d["record_count"]==0]
        manifest={"schema_version":"1.0","collector_version":__version__,"created_at":utcnow(),"run_dir":str(self.run_dir),
                  "cutoff":iso(self.cutoff),"start":iso(self.start),"lookback_days":self.days,"status":"partial" if required_failures or empty_required else "ready_with_limitations",
                  "datasets":sorted(self.datasets.values(),key=lambda d:d["dataset_id"]),"sources":sorted(self.fetcher.sources,key=lambda s:s["name"]),"jobs":self.job_results,
                  "limitations":["Historical observations were downloaded now and may be revised; historical availability is generally unknown.",
                                 "Live snapshots are separate from cutoff-filtered historical series.","No model, trading account or private API is used.","All unavailable optional fields remain unavailable; no guessed values are inserted."]}
        manifest["database_sha256"]=hashlib.sha256((self.run_dir/"research.sqlite").read_bytes()).hexdigest()
        (self.run_dir/"manifest.json").write_text(dumps(manifest,indent=2)+"\n")
        (self.root/"data/latest.json").write_text(dumps({"run_dir":str(self.run_dir),"manifest_path":str(self.run_dir/"manifest.json")},indent=2)+"\n")
        report=["# ETH 研究数据准备结果", "",f"采集目录：`{self.run_dir}`",f"历史截止：{manifest['cutoff']}；回看 {self.days} 天。",f"状态：{manifest['status']}。", "",
                "| 数据集 | 行数 | 最近观察时间 | 类型 |", "|---|---:|---|---|"]
        report.extend(f"| {d['dataset_id']} | {d['record_count']} | {d['last_observed_at']} | {d['kind']} |" for d in manifest["datasets"])
        report += ["", "## 暂未取得的数据", ""]
        report.extend(f"- {j['name']}：{j.get('error')}" for j in self.job_results if j["status"]!="ok")
        report += ["", "## 使用边界", "", "历史序列是今天下载的回溯数据；不能证明在历史预测时刻已经可获得。实时订单簿、持仓量、收益率和当前文档另存为快照。", "",
                   "TVL 变化、稳定币供给变化不是资金净流入；总手续费不是 ETH 销毁量；ETF 未发布的数字保持缺失。Lido 池数据不代表全网质押。", "",
                   "每条记录含证据定位，每个原始响应和数据文件保存 SHA-256。详情见 manifest.json。"]
        (self.run_dir/"REPORT.md").write_text("\n".join(report)+"\n")
        return manifest
