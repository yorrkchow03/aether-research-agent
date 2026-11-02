"""Public data adapters, shared TTL cache, and citable retrieval snapshots."""
from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
from research_data.http import dumps, utcnow
from research_data.normalize import number, iso, timestamp

PROVIDERS = {
    "coinbase": "https://api.exchange.coinbase.com",
    "okx": "https://www.okx.com",
    "defillama": "https://api.llama.fi",
    "stablecoins": "https://stablecoins.llama.fi",
    "coingecko": "https://api.coingecko.com",
    "coinmetrics": "https://community-api.coinmetrics.io",
}
CATALOG = {
    "price": "Coinbase ETH/BTC-USD last trade, single exchange, not aggregated fair value",
    "candles": "Coinbase daily OHLCV, completed UTC candles only, up to 90 days",
    "hourly_candles":"Coinbase ETH/BTC-USD completed UTC hourly OHLCV; up to 72 hours",
    "tvl": "DeFiLlama Ethereum USD TVL stock; changes are not net inflows",
    "stablecoins": "DeFiLlama Ethereum circulating USD value, not ETH buying pressure",
    "dex_volume": "DeFiLlama Ethereum DEX aggregate volume, provider rolling 24h",
    "funding": "OKX ETH-USDT swap funding fraction per provider settlement interval",
    "open_interest": "OKX ETH-USDT swap USD open interest, single venue",
    "reference_price":"CoinGecko aggregated USD reference quote with provider update time, not an executable order",
    "orderbook":"Coinbase level2 ETH/BTC-USD bid/ask depth; top25 levels, snapshot only",
    "funding_history":"OKX ETH-USDT funding observations; variable intervals must be checked, not annualized",
    "aave_fees":"DeFiLlama Aave V3 covered-chain daily fees, not ETH token revenue",
    "aave_revenue":"DeFiLlama Aave V3 covered-chain daily protocol revenue, not ETH token revenue",
}


def atomic(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + threading.current_thread().name.replace("/", "_") + ".tmp")
    tmp.write_text(dumps(obj, indent=2)); tmp.replace(path)


class LiveData:
    def __init__(self, root: Path,knowledge=None):
        self.root = Path(root); self.lock = threading.RLock(); self.request_locks = {};self.knowledge=knowledge
        for part in ("raw", "records", "cache"):(self.root / part).mkdir(parents=True, exist_ok=True)

    def request(self, provider, path, params=None, ttl=60):
        if provider not in PROVIDERS:raise ValueError("Unknown provider; use list_data_sources")
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or any(x in path for x in ("?", "#", "..", "\\")):
            raise ValueError("Use an absolute API path without a host, query or traversal")
        if provider == "okx" and not path.startswith(("/api/v5/public/", "/api/v5/market/")):
            raise ValueError("Only OKX public market endpoints are enabled")
        params = params or {}
        if len(dumps(params)) > 2500:raise ValueError("Query parameters are too large")
        url = PROVIDERS[provider] + path + ("?" + urlencode(params, doseq=True) if params else "")
        identity = hashlib.sha256(url.encode()).hexdigest()
        cache = self.root / "cache" / (identity + ".json")
        with self.lock:request_lock = self.request_locks.setdefault(identity, threading.Lock())
        with request_lock:
            if cache.exists():
                saved = json.loads(cache.read_text())
                if (timestamp(utcnow()) - timestamp(saved["retrieved_at"])).total_seconds() < ttl:
                    record=saved['record'];raw=Path(record['raw_path']).read_bytes()
                    if hashlib.sha256(raw).hexdigest()!=record['source']['sha256'] or record['source']['url']!=url:
                        raise ValueError('Cached source identity/hash mismatch')
                    record['data']=json.loads(raw,parse_float=Decimal)
                    if self.knowledge:self.knowledge.register(record)
                    return {**saved, "cache_hit": True}
            # Public requests use the user's configured macOS proxy; no credentials are sent.
            with httpx.Client(timeout=20, trust_env=True, follow_redirects=False) as client:
                with client.stream("GET", url, headers={"User-Agent": "LocalETHResearch/0.2", "Accept": "application/json"}) as response:
                    if response.status_code != 200:raise RuntimeError(f"{provider} HTTP {response.status_code}")
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 16_000_000:raise RuntimeError("Public API response exceeds 16 MB")
            obj = json.loads(raw, parse_float=Decimal)
            if isinstance(obj, dict) and (obj.get("error") or (provider == "okx" and str(obj.get("code")) != "0")):
                raise RuntimeError(f"{provider} returned an application error")
            digest = hashlib.sha256(raw).hexdigest(); retrieved = utcnow()
            (self.root / "raw" / (digest + ".bin")).write_bytes(raw)
            rid = "live-" + hashlib.sha256((url + digest + retrieved).encode()).hexdigest()[:24]
            source = {"source_id": rid, "name": provider + path, "url": url, "sha256": digest, "retrieved_at": retrieved}
            record = {"record_id": rid, "dataset_id": "live.api." + provider, "observed_at": retrieved,
                      "retrieved_at": retrieved, "available_at": retrieved, "values": {}, "units": {},
                      "source": source, "raw_path": str(self.root / "raw" / (digest + ".bin")),
                      "quality_flags": ["live_retrieval", "provider_time_and_scope_must_be_checked"], "data": obj}
            atomic(self.root / "records" / (rid + ".json"), record)
            if self.knowledge:self.knowledge.register(record)
            result = {"record": record, "retrieved_at": retrieved, "cache_hit": False}
            atomic(cache, result)
            return result

    def get(self, metric, asset="ETH"):
        asset = asset.upper()
        if asset not in ("ETH", "BTC"):raise ValueError("Supported assets: ETH, BTC")
        if metric not in CATALOG:raise ValueError("Unknown metric; use list_data_sources")
        if asset != "ETH" and metric not in ("price", "candles","hourly_candles","reference_price","orderbook"):raise ValueError("This adapter is scoped to ETH/Ethereum")
        specs = {
            "price": ("coinbase", f"/products/{asset}-USD/ticker", {}, 60),
            "candles": ("coinbase", f"/products/{asset}-USD/candles", {"granularity": 86400,
                        "start": iso(timestamp(utcnow()) - timedelta(days=91)),
                        "end": datetime.now(timezone.utc).date().isoformat()}, 3600),
            "tvl": ("defillama", "/v2/historicalChainTvl/Ethereum", {}, 3600),
            "hourly_candles":('coinbase',f'/products/{asset}-USD/candles',{'granularity':3600,
                              'start':iso(datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)-timedelta(days=3)),
                              'end':iso(datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0))},300),
            "stablecoins": ("stablecoins", "/stablecoincharts/Ethereum", {}, 3600),
            "dex_volume": ("defillama", "/overview/dexs/Ethereum", {"excludeTotalDataChart": "true", "excludeTotalDataChartBreakdown": "true"}, 3600),
            "funding": ("okx", "/api/v5/public/funding-rate", {"instId": "ETH-USDT-SWAP"}, 300),
            "open_interest": ("okx", "/api/v5/public/open-interest", {"instType": "SWAP", "instId": "ETH-USDT-SWAP"}, 300),
            "reference_price":("coingecko","/api/v3/simple/price",{"ids":"ethereum" if asset=="ETH" else "bitcoin","vs_currencies":"usd","include_last_updated_at":"true","include_market_cap":"true","include_24hr_vol":"true","include_24hr_change":"true"},120),
            "orderbook":("coinbase",f"/products/{asset}-USD/book",{"level":2},60),
            "funding_history":("okx","/api/v5/public/funding-rate-history",{"instId":"ETH-USDT-SWAP","limit":100},3600),
            "aave_fees":("defillama","/summary/fees/aave-v3",{"dataType":"dailyFees"},3600),
            "aave_revenue":("defillama","/summary/fees/aave-v3",{"dataType":"dailyRevenue"},3600),
        }
        provider, path, params, ttl = specs[metric]
        # Use a stable midnight start so the daily candle cache survives polling.
        if metric == "candles":params["start"] = (datetime.now(timezone.utc).date() - timedelta(days=91)).isoformat()
        fetched = self.request(provider, path, params, ttl)
        raw_record = fetched["record"]; data = raw_record["data"]; values = {}; units = {}; series = []
        observed = raw_record["retrieved_at"]; flags = ["live_retrieval"]
        if metric == "price":
            values = {"price_usd": number(data["price"]), "bid_usd": number(data["bid"]), "ask_usd": number(data["ask"]), "volume_24h_asset": number(data["volume"])}
            units = {"price_usd": "USD", "bid_usd": "USD", "ask_usd": "USD", "volume_24h_asset": asset}
            observed = data.get("time") or observed; flags.append("single_exchange_last_trade")
        elif metric=='reference_price':
            row=data['ethereum' if asset=='ETH' else 'bitcoin'];values={"reference_price_usd":number(row['usd']),"market_cap_usd":number(row.get('usd_market_cap')),"rolling_24h_volume_usd":number(row.get('usd_24h_vol')),"change_24h_fraction":number(Decimal(str(row['usd_24h_change']))/100) if row.get('usd_24h_change') is not None else None}
            units={k:'fraction' if k.endswith('fraction') else 'USD' for k in values};observed=iso(timestamp(row.get('last_updated_at') or timestamp(observed).timestamp()));flags.append('aggregated_reference_not_executable')
        elif metric=='orderbook':
            bids=data['bids'][:25];asks=data['asks'][:25]
            if not bids or not asks:raise ValueError('Orderbook has no usable bid/ask levels')
            bid=Decimal(bids[0][0]);ask=Decimal(asks[0][0]);mid=(bid+ask)/2
            values={'best_bid_usd':str(bid),'best_ask_usd':str(ask),'spread_usd':str(ask-bid),'spread_fraction':str((ask-bid)/mid),
                    'top25_bid_depth_usd':str(sum(Decimal(p)*Decimal(q) for p,q,*_ in bids)),
                    'top25_ask_depth_usd':str(sum(Decimal(p)*Decimal(q) for p,q,*_ in asks))}
            units={k:'fraction' if k.endswith('fraction') else 'USD' for k in values};flags.extend(['single_exchange','depth_is_snapshot_not_guaranteed_execution'])
        elif metric=='funding_history':
            series=[{'observed_at':iso(timestamp(Decimal(r['fundingTime'])/1000)),'funding_rate_fraction':number(r.get('realizedRate') or r['fundingRate'])} for r in sorted(data['data'],key=lambda x:int(x['fundingTime']))]
            if not series:raise ValueError('No settled funding history observations')
            values={'latest_settled_funding_rate_fraction':series[-1]['funding_rate_fraction'],'observation_count':str(len(series))}
            units={'latest_settled_funding_rate_fraction':'fraction','observation_count':'count'};observed=series[-1]['observed_at'];flags.extend(['single_exchange','settled_history','interval_not_assumed_constant'])
        elif metric in ('aave_fees','aave_revenue'):
            field='fees_usd' if metric=='aave_fees' else 'revenue_usd'
            now=timestamp(utcnow()).timestamp()
            series=[{'observed_at':iso(timestamp(t)),'period_end':iso(timestamp(int(t)+86400)),field:number(v)} for t,v in data['totalDataChart'] if int(t)+86400<=now][-90:]
            if not series:raise ValueError('No completed fee/revenue daily observations')
            values={field:series[-1][field]};units={field:'USD/day'};observed=series[-1]['period_end'];flags.append('aave_all_covered_chains_not_ethereum_only')
        elif metric in ('candles','hourly_candles'):
            interval=86400 if metric=='candles' else 3600
            now = timestamp(utcnow()).timestamp()
            series = [{"observed_at": iso(timestamp(row[0])), "period_end": iso(timestamp(row[0] + interval)),
                       "close_usd": number(row[4]), "open_usd": number(row[3]), "high_usd": number(row[2]),
                       "low_usd": number(row[1]), "volume_asset": number(row[5])}
                      for row in sorted(data) if row[0] + interval <= now][-(90 if interval==86400 else 72):]
            if not series:raise ValueError("No completed candles")
            values = {"close_usd": series[-1]["close_usd"]}; units = {"close_usd": "USD"}; observed = series[-1]["period_end"]
            flags.extend(['completed_daily_candles' if interval==86400 else 'completed_hourly_candles', "single_exchange", "volume_in_asset_units"])
        elif metric in ("tvl", "stablecoins"):
            series = [{"observed_at": iso(timestamp(row["date"])), "value_usd": number(row["tvl"] if metric == "tvl" else sum(Decimal(str(v)) for v in row["totalCirculatingUSD"].values()))}
                      for row in sorted(data, key=lambda x: int(x["date"]))][-90:]
            if not series:raise ValueError("No chain observations")
            key = "tvl_usd" if metric == "tvl" else "circulating_value_usd"
            values = {key: series[-1]["value_usd"]}; units = {key: "USD"}; observed = series[-1]["observed_at"]
            flags.append("stock_not_net_inflow")
        elif metric == "dex_volume":
            values = {"volume_24h_usd": number(data.get("total24h")), "volume_7d_usd": number(data.get("total7d"))}
            units = {key: "USD" for key in values}; flags.append("provider_rolling_window")
        elif metric == "funding":
            row = data["data"][0]; values = {"funding_rate_fraction": number(row["fundingRate"])}; units = {"funding_rate_fraction": "fraction"}
            observed = iso(timestamp(Decimal(row.get("ts") or int(timestamp(observed).timestamp() * 1000)) / 1000))
            flags.append("single_exchange_rate_not_annualized")
        elif metric == "open_interest":
            row = data["data"][0]; values = {"open_interest_usd": number(row["oiUsd"])}; units = {"open_interest_usd": "USD"}
            observed = iso(timestamp(Decimal(row["ts"]) / 1000)); flags.append("single_exchange")
        if any(v is None for v in values.values()):flags.append("missing_value")
        rid = "live-" + hashlib.sha256((raw_record["record_id"] + metric + asset + 'market-record-v1').encode()).hexdigest()[:24]
        record = {k: v for k, v in raw_record.items() if k != "data"}
        record.update(record_id=rid, dataset_id="live." + metric + "." + asset.lower(), values=values, units=units,
                      observed_at=observed, series=series, quality_flags=flags, scope=CATALOG[metric], asset=asset, metric=metric)
        record.update(schema_version='market-record-v1',provider=provider,
                      instrument=f'{asset}-USD' if provider=='coinbase' else 'ETH-USDT-SWAP' if provider=='okx' else asset,
                      quote_currency='USDT' if provider=='okx' else 'USD',
                      first_seen_at=raw_record['retrieved_at'],published_at=None,
                      availability_basis='First local retrieval; not proof of historical publication time',
                      sampling='Completed UTC daily intervals' if metric=='candles' else 'Completed UTC hourly intervals' if metric=='hourly_candles' else 'Provider settlement intervals' if metric=='funding_history' else 'Provider snapshot or rolling window')
        atomic(self.root / "records" / (rid + ".json"), record)
        if self.knowledge:self.knowledge.register(record)
        return {"status": "ok", "record": record, "cache_hit": fetched["cache_hit"]}

    def lookup(self, rid):
        if not rid.startswith("live-") or len(rid) != 29 or not all(c in "0123456789abcdef" for c in rid[5:]):raise ValueError("Invalid live record ID")
        if self.knowledge and self.knowledge.contains(rid):return self.knowledge.get(rid)
        row = json.loads((self.root / "records" / (rid + ".json")).read_text())
        if row["record_id"] != rid:raise ValueError("Record identity mismatch")
        if row.get("raw_path") and hashlib.sha256(Path(row["raw_path"]).read_bytes()).hexdigest() != row["source"]["sha256"]:
            raise ValueError("Live raw response hash mismatch")
        return row


class Dashboard:
    def __init__(self, live):
        self.live = live; self.lock = threading.RLock(); self.refreshing = False
        self.path = live.root / "dashboard.json"

    def snapshot(self):
        with self.lock:
            obj = json.loads(self.path.read_text()) if self.path.exists() else {"items": {}, "updated_at": None}
            if self.live.knowledge:
                for group in ('items','indicators'):
                    for item in obj.get(group,{}).values():
                        if item.get('record'):
                            try:item['record']=self.live.lookup(item['record']['record_id'])
                            except (ValueError,KeyError,OSError) as exc:item.update(status='unavailable',error=str(exc)[:200])
            return {**obj, "refreshing": self.refreshing, "now": utcnow()}

    def start_refresh(self):
        with self.lock:
            if self.refreshing:return {"state": "already_running"}
            self.refreshing = True
        threading.Thread(target=self._refresh, daemon=True).start()
        return {"state": "started"}

    def _refresh(self):
        previous = self.snapshot().get("items", {})
        jobs = [("eth_price", "price", "ETH"), ("btc_price", "price", "BTC"), ("eth_candles", "candles", "ETH"), ("btc_candles","candles","BTC"),('eth_hourly','hourly_candles','ETH')]
        jobs += [(metric, metric, "ETH") for metric in ("tvl", "stablecoins", "dex_volume", "funding", "open_interest","reference_price","orderbook","funding_history","aave_fees","aave_revenue")]
        def fetch(job):
            key, metric, asset = job
            try:return key, self.live.get(metric, asset)
            except Exception as exc:
                return key, {**previous.get(key, {}), "status": "unavailable", "error": str(exc)[:300], "failed_at": utcnow(), "retained_previous": key in previous}
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:items = dict(pool.map(fetch, jobs))
            from .metrics import derive_dashboard
            indicators=derive_dashboard(items,self.live.root,self.live.knowledge)
            with self.lock:atomic(self.path, {"items": items, "indicators":indicators,"updated_at": utcnow()})
        finally:
            with self.lock:self.refreshing = False
