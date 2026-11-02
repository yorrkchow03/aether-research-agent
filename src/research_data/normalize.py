"""Provider-specific normalization with explicit time and unit semantics."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from xml.etree import ElementTree

from .http import dumps

UTC = timezone.utc


def timestamp(value):
    if isinstance(value, (int, float, Decimal)) or str(value).isdigit():
        return datetime.fromtimestamp(float(value), UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def number(value):
    if value is None or str(value).strip() in ("", "-", "–", "n.a.", "N/A", "."):
        return None
    text = str(value).strip().replace(",", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        d = Decimal(text)
    except InvalidOperation:
        return None
    return str(d) if d.is_finite() else None


def record(dataset_id, observed, values, snapshot, locator, *, period_end=None, available_at=None, flags=(), extra=None):
    normalized = {k: number(v) for k, v in values.items()}
    row = {
        "dataset_id": dataset_id, "observed_at": iso(timestamp(observed)),
        "period_end": iso(timestamp(period_end)) if period_end else None,
        "available_at": iso(timestamp(available_at)) if available_at else None,
        "retrieved_at": snapshot.metadata["retrieved_at"], "values": normalized,
        "quality_flags": sorted(set(flags)),
        "evidence": [{"source_id": snapshot.source_id, "locator": locator}],
    }
    if any(v is None for v in normalized.values()):
        row["quality_flags"].append("missing_value")
    row.update(extra or {})
    identity = dumps({k: row[k] for k in ("dataset_id", "observed_at", "values", "evidence")}, sort_keys=True)
    row["record_id"] = hashlib.sha256(identity.encode()).hexdigest()[:24]
    return row


def coingecko(snapshot, asset):
    data = snapshot.json()
    if not all(key in data for key in ("prices", "market_caps", "total_volumes")):
        raise ValueError("CoinGecko response is missing chart fields")
    points = {}
    for source_key, target_key in (("prices", "price_usd"), ("market_caps", "market_cap_usd"), ("total_volumes", "rolling_24h_volume_usd")):
        for i, (ms, value) in enumerate(data[source_key]):
            point = points.setdefault(int(ms), {"values": {}, "locators": {}})
            point["values"][target_key] = value
            point["locators"][target_key] = f"/{source_key}/{i}"
    rows = []
    for ms, point in sorted(points.items()):
        dt = timestamp(Decimal(ms) / 1000)
        # The final live sample is not a daily 00:00 UTC observation.
        if (dt.hour, dt.minute, dt.second, dt.microsecond) != (0, 0, 0, 0):
            continue
        rows.append(record(f"market.coingecko.{asset}.daily", dt, point["values"], snapshot, point["locators"], flags=["availability_unknown"]))
    return rows


CM_MAP = {
    "PriceUSD": "price_usd", "ReferenceRateUSD": "reference_rate_usd", "TxCnt": "transaction_count",
    "AdrActCnt": "active_address_count", "SplyCur": "supply_eth", "IssTotNtv": "gross_issuance_eth",
    "FeeTotNtv": "total_fees_eth", "SplyBurntNtv": "signed_burn_eth", "SplyStkedNtv": "staked_eth",
    "ValidatorActOngCnt": "active_validator_count", "StkDepositTotNtv": "staking_deposits_eth",
    "StkWithdrawTotNtv": "staking_withdrawals_eth", "FlowInExNtv": "exchange_inflow_eth",
    "FlowOutExNtv": "exchange_outflow_eth", "AssetCompletionTime": "completion_epoch_seconds",
}


def coinmetrics(snapshot, dataset_id):
    data = snapshot.json()
    if "next_page_url" in data:
        raise ValueError("Coin Metrics response is paginated; refusing silent truncation")
    rows = []
    for i, item in enumerate(data["data"]):
        start = timestamp(item["time"])
        # Conservatively treat a daily metric as the full UTC day it labels.
        values = {target: item[key] for key, target in CM_MAP.items() if key in item}
        if not values:
            continue
        completion = item.get("AssetCompletionTime")
        available = timestamp(Decimal(completion)) if completion else None
        rows.append(record(dataset_id, start, values, snapshot, f"/data/{i}", period_end=start + timedelta(days=1), available_at=available,
                           flags=[] if available else ["availability_unknown"], extra={"provider_time_label": item["time"]}))
    return rows


def llama_tvl(snapshot, chain):
    return [record(f"ecosystem.tvl.{chain.lower()}.daily", item["date"], {"tvl_usd": item["tvl"]}, snapshot, f"/{i}", flags=["availability_unknown", "usd_valuation_not_net_flow"])
            for i, item in enumerate(snapshot.json())]


def llama_stable(snapshot, chain):
    rows = []
    for i, item in enumerate(snapshot.json()):
        # Native units of EUR, JPY, etc. must not be added as if they were USD.
        usd_values = item.get("totalCirculatingUSD")
        if not isinstance(usd_values, dict):
            raise ValueError("Missing USD-converted stablecoin values")
        total = sum((Decimal(str(v)) for v in usd_values.values()), Decimal(0))
        rows.append(record(f"ecosystem.stablecoins.{chain.lower()}.daily", item["date"],
                           {"circulating_value_usd": total, "usd_pegged_circulating_value_usd": usd_values.get("peggedUSD")}, snapshot, f"/{i}/totalCirculatingUSD",
                           flags=["availability_unknown", "supply_change_not_net_capital_inflow"]))
    return rows


def llama_chart(snapshot, dataset_id, metric):
    data = snapshot.json()
    chart = data.get("totalDataChart")
    if not isinstance(chart, list):
        raise ValueError("Missing historical chart")
    return [record(dataset_id, t, {metric: value}, snapshot, f"/totalDataChart/{i}", period_end=timestamp(t) + timedelta(days=1), flags=["availability_unknown"])
            for i, (t, value) in enumerate(chart)]


def funding(snapshot):
    rows = []
    for i, item in enumerate(snapshot.json()["data"]):
        ms = Decimal(item["fundingTime"])
        rows.append(record("derivatives.okx.eth_usdt.funding", ms / 1000,
                           {"funding_rate": item.get("fundingRate"), "realized_rate": item.get("realizedRate")}, snapshot, f"/data/{i}",
                           flags=["availability_unknown", "venue_specific"], extra={"instrument": item["instId"]}))
    return rows


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("p", "div", "br", "h1", "h2", "h3", "li"):
            self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(html):
    parser = TextParser(); parser.feed(html)
    return "\n".join(line for line in (" ".join(s.split()) for s in "".join(parser.parts).splitlines()) if line)


class Tables(HTMLParser):
    def __init__(self):
        super().__init__(); self.tables = []; self.depth = 0; self.rows = []; self.row = []; self.cell = None
    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
            if self.depth == 1: self.rows = []
        if self.depth and tag == "tr": self.row = []
        if self.depth and tag in ("th", "td"): self.cell = []
    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)
    def handle_endtag(self, tag):
        if self.depth and tag in ("th", "td") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split())); self.cell = None
        if self.depth and tag == "tr" and self.row: self.rows.append(self.row)
        if tag == "table":
            if self.depth == 1: self.tables.append(self.rows)
            self.depth = max(0, self.depth - 1)


def etf_flows(snapshot):
    parser = Tables(); parser.feed(snapshot.text())
    rows = []
    for ti, table in enumerate(parser.tables):
        headers = next((r for r in table if "ETHA" in r and "FETH" in r), None)
        if not headers: continue
        total_header = next((r for r in table if r and r[-1].lower() == "total"), None)
        if not total_header or len(headers) != len(total_header):
            raise ValueError("ETF header layout changed")
        for ri, cells in enumerate(table):
            try: day = datetime.strptime(cells[0], "%d %b %Y").replace(tzinfo=UTC)
            except (ValueError, IndexError): continue
            if len(cells) != len(headers):
                raise ValueError("ETF row column count does not match its header")
            vals = {headers[i].lower() + "_net_flow_usd": None if number(cells[i]) is None else Decimal(number(cells[i])) * 1_000_000
                    for i in range(1, len(headers) - 1)}
            total = number(cells[-1])
            vals["total_net_flow_usd"] = Decimal(total) * 1_000_000 if total is not None else None
            flags = ["availability_unknown", "publisher_aggregated", "trading_day_series"]
            if any(v is None for v in vals.values()): flags.append("incomplete_publication")
            # Source figures are rounded to 0.1 million; compare with a matching tolerance.
            if all(v is not None for v in vals.values()):
                residual = sum(v for k, v in vals.items() if k != "total_net_flow_usd") - vals["total_net_flow_usd"]
                if abs(residual) > Decimal(len(headers)) * 50_000: flags.append("total_mismatch")
            rows.append(record("flows.farside.eth_etf.daily", day, vals, snapshot, f"table[{ti}]/row[{ri}]", period_end=day + timedelta(days=1), flags=flags))
    if not rows: raise ValueError("No dated ETF rows found; HTML may be a challenge page")
    return rows


def fed_rates(snapshot):
    parser = Tables(); parser.feed(snapshot.text())
    table = next((t for t in parser.tables if t and t[0][0] == "Instruments"), None)
    if not table: raise ValueError("H.15 instrument table not found")
    days = [datetime.strptime(re.sub(r"\s", "", x), "%Y%b%d").replace(tzinfo=UTC) for x in table[0][1:]]
    fields = {}; nominal = False
    for ri, cells in enumerate(table[1:], 1):
        name = cells[0]
        if name.startswith("Federal funds (effective)"): fields["effective_fed_funds_percent"] = (ri, cells[1:])
        if name.startswith("Nominal"): nominal = True
        if name.startswith("Inflation indexed"): nominal = False
        if nominal and name in ("2-year", "10-year"):
            fields[f"treasury_{name.replace('-', '_')}_percent"] = (ri, cells[1:])
    if len(fields) < 3: raise ValueError("H.15 nominal maturity layout changed")
    return [record("macro.federalreserve.h15.daily", day, {k: v[1][i] for k, v in fields.items()}, snapshot,
                   {k: f"table[0]/row[{v[0]}]/column[{i+1}]" for k, v in fields.items()}, period_end=day + timedelta(days=1),
                   flags=["availability_unknown", "business_day_series", "short_history"])
            for i, day in enumerate(days)]


def ethereum_events(snapshot):
    root = ElementTree.fromstring(snapshot.body)
    rows = []
    for i, item in enumerate(root.findall("./channel/item")):
        pub = parsedate_to_datetime(item.findtext("pubDate")).astimezone(UTC)
        text = item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or item.findtext("description") or ""
        rows.append(record("events.ethereum_blog", pub, {}, snapshot, f"/rss/channel/item[{i}]", available_at=None,
                           flags=["text_retrieved_after_publication", "availability_unknown"], extra={
                               "title": item.findtext("title"), "url": item.findtext("link"), "published_at": iso(pub),
                               "text": plain_text(text), "content_kind": "official_blog_post", "event_date": None}))
    return rows


def aave_topics(snapshot):
    return [record("events.aave_governance", item["created_at"], {}, snapshot, f"/topic_list/topics/{i}",
                   flags=["availability_unknown", "forum_topic_not_final_decision"], extra={
                       "title": item["title"], "url": f"https://governance.aave.com/t/{item['slug']}/{item['id']}",
                       "published_at": item["created_at"], "last_activity_at": item.get("last_posted_at"),
                       "text": item.get("excerpt", ""), "content_kind": "governance_topic", "event_date": None})
            for i, item in enumerate(snapshot.json()["topic_list"]["topics"])]
