"""Report media derived from verified, saved evidence."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from fnmatch import fnmatchcase
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from research_data.normalize import iso, timestamp
from .claims import Claim


CANDLE_FIELDS = {"low_usd": 1, "high_usd": 2, "open_usd": 3, "close_usd": 4, "volume_asset": 5}
RAW_CANDLE_FIELDS = {"low": "low_usd", "high": "high_usd", "open": "open_usd",
                     "close": "close_usd", "volume": "volume_asset"}
FIXED_FREQUENCIES = {"1h": timedelta(hours=1), "1d": timedelta(days=1)}
METRIC_LABELS = {"low_usd": "Low price", "high_usd": "High price", "open_usd": "Opening price",
                 "close_usd": "Closing price", "volume_asset": "Trading volume"}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTH = r"(?P<month>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_DAY = r"(?P<day>[0-9]{1,2})"
_RANGE = r"(?:\s*[-–—]\s*[0-9]{1,2})?"
_EVENT_DATES = (
    re.compile(r"^(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})(?![0-9])"),
    re.compile(rf"^(?P<label>{_DAY}{_RANGE}\s+{_MONTH}(?:\s+(?P<year>[0-9]{{4}}))?)\b", re.I),
    re.compile(rf"^(?P<label>{_MONTH}\.?\s+{_DAY}{_RANGE}(?:,?\s+(?P<year>[0-9]{{4}}))?)\b", re.I),
    re.compile(r"^(?P<label>(?:(?P<year>[0-9]{4})年\s*)?(?P<month>[0-9]{1,2})月\s*(?P<day>[0-9]{1,2})[日号])"),
)


def _body(draft):
    return draft if isinstance(draft, dict) else draft.model_dump()


def report_display_title(draft):
    """Present saved reports under a neutral title without altering their identity."""
    body = _body(draft)
    title = body.get("title") or "Research report"
    if title.casefold() not in {"limited evidence report", "limited research report", "saved market data brief"}:
        return title
    scope = body.get("scope") or {}
    assets = ", ".join(scope.get("assets") or ())
    heading = " ".join(filter(None, (assets, "Research report")))
    try:
        begin = timestamp(scope["start"]).date()
        end = (timestamp(scope["end"]) - timedelta(microseconds=1)).date()
    except (KeyError, ValueError, TypeError):
        return heading
    window = end.strftime("%d %b %Y") if begin == end else f"{begin:%d %b %Y} – {end:%d %b %Y}"
    return f"{heading} · {window} UTC"


def report_display_summary(draft):
    from .minimal_delivery import MINIMAL_SUMMARY

    body = _body(draft)
    if body.get("summary") != MINIMAL_SUMMARY:
        return body.get("summary") or ""
    facts = [*body.get("observations", []), *body.get("key_changes", [])]
    return " ".join(item["statement"] for item in facts[:2])


def report_display_limits(draft):
    from .minimal_delivery import MINIMAL_LIMIT

    return ["The saved sources leave some developments in this period unresolved."
            if item == MINIMAL_LIMIT else item for item in _body(draft).get("limitations", [])]


def report_watch_items(items):
    """Respect author priority and show at most three distinct follow-ups."""
    selected = {}
    for item in items or ():
        key = " ".join(item.strip().rstrip(".?？。").casefold().split())
        if key and key not in selected:
            selected[key] = item
        if len(selected) == 3:
            break
    return list(selected.values())


def _event_date(statement, scope):
    lead = re.match(r"^(?:on\s+|as of\s+)?", statement, re.I).end()
    for pattern in _EVENT_DATES:
        match = pattern.match(statement[lead:])
        if not match:
            continue
        fields = match.groupdict()
        try:
            if fields.get("date"):
                result = date.fromisoformat(fields["date"])
            else:
                month = fields["month"]
                month = int(month) if month.isdecimal() else _MONTHS.index(month[:3].title()) + 1
                day = int(fields["day"])
                if fields.get("year"):
                    result = date(int(fields["year"]), month, day)
                else:
                    if not scope:
                        return None
                    first = timestamp(scope["start"]).date()
                    last = (timestamp(scope["end"]) - timedelta(microseconds=1)).date()
                    candidates = []
                    for year in range(last.year, first.year - 1, -1):
                        try:
                            candidate = date(year, month, day)
                        except ValueError:
                            continue
                        if first <= candidate <= last:
                            candidates.append(candidate)
                    if len(candidates) != 1:
                        return None
                    result = candidates[0]
        except (ValueError, KeyError, TypeError):
            return None
        label = fields.get("label") or fields["date"]
        # The full audited sentence stays intact in the report packet.
        rest = statement[lead + match.end():]
        delimiter = re.match(r"^\s*[:：—–]\s*", rest)
        display = rest[delimiter.end():] if delimiter else statement
        return result.isoformat(), label, display
    return None


def report_timeline(draft, presentation=None, lookup=None):
    """Place audited evidence on event cards without using publication as event time."""
    body = _body(draft)
    documents = (presentation or {}).get("documents", [])
    scope = body.get("scope")
    published_days = {item["evidence_id"]: item.get("published_at") for item in documents}
    cards = []

    def publication_day(ids):
        days = set()
        for rid in ids:
            if not rid.startswith(("page-", "web-")):
                continue
            if rid not in published_days:
                try:
                    record = lookup(rid) if lookup else {}
                    published_days[rid] = record.get("published_at")
                except (KeyError, ValueError, OSError):
                    published_days[rid] = None
            if published_days[rid]:
                try:
                    days.add(timestamp(published_days[rid]).date().isoformat())
                except (ValueError, TypeError):
                    pass
        return next(iter(days)) if len(days) == 1 else None

    def make_card(statement, ids, *, authored_date=True):
        authored = _event_date(statement, scope) if authored_date else None
        if authored:
            day, label, display = authored
            kind = "event"
        else:
            day = label = publication_day(ids)
            display = statement
            kind = "publication" if day else None
        return {"statement": statement, "display_statement": display, "evidence_ids": list(ids),
                "date": day, "date_label": label, "date_kind": kind, "measurement": None,
                "facts": [], "documents": []}

    def matching_card(ids):
        supplied = set(ids)
        best = 0
        match = None
        for card in cards:
            sources = set(card["evidence_ids"])
            sources.update(item["evidence_id"] for item in card["documents"])
            sources.update(rid for fact in card["facts"] for rid in fact["evidence_ids"])
            overlap = len(sources & supplied)
            if overlap > best:
                best, match = overlap, card
        return match

    def same_statement(left, right):
        return " ".join(left.casefold().rstrip(" .。").split()) == " ".join(right.casefold().rstrip(" .。").split())

    for event in body.get("key_changes", []):
        card = make_card(event["statement"], event["evidence_ids"])
        if event.get("metric"):
            card["measurement"] = event
        if not any(same_statement(item["statement"],card["statement"])
                   and set(card["evidence_ids"]) <= set(item["evidence_ids"]) for item in cards):
            cards.append(card)
    for document in documents:
        ids = [document["evidence_id"]]
        card = matching_card(ids)
        if card is None:
            card = make_card("", ids, authored_date=False)
            cards.append(card)
        card["documents"].append(document)
    for fact in body.get("observations", []):
        if fact.get("metric"):
            continue
        card = matching_card(fact["evidence_ids"])
        if card is None:
            cards.append(make_card(fact["statement"], fact["evidence_ids"]))
        elif not (set(fact["evidence_ids"]) <= set(card["evidence_ids"])
                  and any(same_statement(fact["statement"], text)
                          for text in (card["statement"],card["display_statement"]))):
            card["facts"].append(fact)
    return sorted(cards,key=lambda card: (card["date"] is None, card["date"] or ""))


def _clock(value):
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("A report clock must be an ISO timestamp with a time zone")
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if at.utcoffset() is None:
        raise ValueError("A report clock must include a time zone")
    return at


def _coinbase_request_clock(value):
    # Date-only bounds in a captured candles query denote UTC day boundaries.
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        value += "T00:00:00Z"
    return _clock(value)


class ReportScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assets: list[Literal["ETH", "BTC"]] = Field(min_length=1, max_length=2)
    start: str = Field(description="Start of the research window as an inclusive ISO timestamp with a timezone.")
    end: str = Field(description="Exclusive end of the research window as an ISO timestamp with a timezone.")

    @field_validator("start", "end")
    @classmethod
    def utc_clock(cls, value):
        return iso(_clock(value))

    @model_validator(mode="after")
    def ordered_window(self):
        if timestamp(self.start) >= timestamp(self.end):
            raise ValueError("Report start must precede its exclusive end")
        if len(set(self.assets)) != len(self.assets):
            raise ValueError("Report assets must be distinct")
        return self


class ReportChart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(pattern=r"^(series|live)-[a-f0-9]{24}$",
        description="Saved library collection or a saved Coinbase Exchange ETH-USD/BTC-USD hourly or daily candle capture. The host supplies completed points.")
    metric: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,79}$",
        description="Exact case-sensitive values key from the saved series, e.g. close_usd. Raw Coinbase Exchange candles also accept their documented fields, e.g. close.")


class FeaturedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(pattern=r"^page-[a-f0-9]{24}$", description="Saved original document record.")
    quote: str = Field(min_length=8, max_length=600, description="One short contiguous exact substring from the saved original body, including whitespace. Do not join sentences across line breaks or normalize the source text.")
    relevance: Claim = Field(description="One statement explaining the source's relevance, citing this document alone.")
    role: Literal["primary", "background", "correction", "counterevidence", "later_update"] = Field(default="primary",
        description="Report role, not source authority. primary means main in-window evidence; a captured media article is not necessarily an issuer's first-hand statement.")

    @model_validator(mode="after")
    def bind_relevance(self):
        if self.relevance.evidence_ids != [self.evidence_id]:
            raise ValueError("Document relevance must cite its exact saved original only")
        if len(self.quote.strip()) < 8:
            raise ValueError("A featured document needs a meaningful exact quote")
        return self


def presentation_evidence_ids(draft):
    return list(dict.fromkeys(item.evidence_id for item in [*draft.charts, *draft.documents]))


def document_review_targets(draft):
    return [{"claim_id": f"DOC{index}", "kind": "observation", **item.relevance.model_dump()}
            for index, item in enumerate(draft.documents, start=1)]


def _source(row):
    source = row.get("source") or {}
    url = source.get("url")
    parsed = urlsplit(url or "")
    if (parsed.scheme not in ("https", "http") or not parsed.hostname
            or parsed.username or parsed.password
            or any(char.isspace() or char in '<>"`' for char in url or "")):
        raise ValueError("Report media needs a valid saved source URL")
    if not source.get("name") or not re.fullmatch(r"[a-f0-9]{64}", source.get("sha256") or ""):
        raise ValueError("Report media needs a saved publisher and source hash")
    return {"publisher": source["name"], "url": url, "sha256": source["sha256"]}


def _capture_time(row, as_of):
    seen = row.get("retrieved_at")
    if not seen:
        raise ValueError("Report media needs a saved retrieval time")
    for value in (seen, row.get("available_at"), row.get("first_seen_at")):
        if value and as_of is not None and timestamp(value) > as_of:
            raise ValueError("Report media was acquired after the evidence cutoff")
    return iso(timestamp(seen))


def _numeric(value):
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Chart source has a missing or invalid value") from None
    if not decimal.is_finite() or not math.isfinite(float(decimal)):
        raise ValueError("Chart source has a non-finite value")
    return decimal


def _unit(row, metric):
    units = {unit for field, unit in row.get("units", {}).items() if fnmatchcase(metric, field) and unit}
    if len(units) != 1:
        available = {field: unit for field, unit in list(row.get('units', {}).items())[:30] if unit}
        raise ValueError("Chart metric needs one exact saved unit: " + metric
            + ". Metric keys are case-sensitive. Saved unit keys: " + json.dumps(available, ensure_ascii=False)
            + ". Read the source data contract and change /charts/<index>/metric using publish_saved_report; no alias is inferred.")
    return next(iter(units))


def _saved_raw(row, source, raw_reader, description):
    try:
        raw = raw_reader(row) if raw_reader else Path(row["raw_path"]).read_bytes()
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError(description + " needs its original saved response") from exc
    if not isinstance(raw, bytes) or not raw or sha256(raw).hexdigest() != source["sha256"]:
        raise ValueError(description + " raw capture hash mismatch")
    return raw


def _raw_coinbase_values(row, source, metric, raw_reader):
    raw = _saved_raw(row, source, raw_reader, "Chart source")
    try:
        raw_rows = json.loads(raw, parse_float=Decimal)
        field = CANDLE_FIELDS[metric]
        values = {}
        for item in raw_rows:
            at = timestamp(item[0])
            if at in values:
                raise ValueError("Chart source contains duplicate raw candle times")
            values[at] = item[field]
    except (OSError, KeyError, IndexError, TypeError) as exc:
        raise ValueError("Chart source needs the original saved Coinbase candle response") from exc
    return values


def _public_coinbase_candles(row, spec, raw_reader):
    """Reconstruct chart points from a host-retained Coinbase Exchange GET."""
    source = _source(row)
    url = urlsplit(source["url"])
    product = re.fullmatch(r"/products/(ETH|BTC)-USD/candles", url.path)
    received = row.get("retrieved_at")
    identity = (f"live-{sha256((source['url'] + source['sha256'] + received).encode()).hexdigest()[:24]}"
                if isinstance(received, str) else None)
    original = row["source"]
    if (url.scheme != "https" or url.netloc != "api.exchange.coinbase.com" or not product or url.fragment
            or identity != row["record_id"] or original.get("source_id") != identity
            or original.get("name") != f"coinbase{url.path}"
            or original.get("retrieved_at") != received
            or row.get("observed_at") != received or row.get("available_at") != received):
        raise ValueError("Chart needs a bound Coinbase Exchange ETH-USD or BTC-USD candle response")

    params = parse_qs(url.query, keep_blank_values=True, strict_parsing=True, max_num_fields=3)
    granularity = params.get("granularity")
    if (granularity not in (["3600"], ["86400"])
            or set(params) not in ({"granularity"}, {"granularity", "start", "end"})
            or any(len(values) != 1 for values in params.values())):
        raise ValueError("Coinbase chart needs explicit hourly or daily granularity and an optional paired date range")
    asset = product.group(1)
    seconds = int(granularity[0])
    frequency = "1d" if seconds == 86400 else "1h"
    interval = FIXED_FREQUENCIES[frequency]
    capture = _clock(received)
    beginning = _coinbase_request_clock(params["start"][0]) if "start" in params else None
    ceiling = _coinbase_request_clock(params["end"][0]) if "end" in params else capture
    if beginning is not None and beginning >= ceiling:
        raise ValueError("Coinbase candle request start must precede its end")

    metric = RAW_CANDLE_FIELDS.get(spec.metric, spec.metric)
    if metric not in CANDLE_FIELDS:
        raise ValueError("Chart metric must match a saved Coinbase candle field, such as close_usd or close")
    unit = asset if metric == "volume_asset" else "USD"
    raw = _saved_raw(row, source, raw_reader, "Chart source")
    rows = json.loads(raw, parse_float=Decimal)
    # The unbounded recent response has been observed with 350 entries. Each
    # entry must still satisfy the captured endpoint's documented OHLCV layout.
    if not isinstance(rows, list) or not 2 <= len(rows) <= 350:
        raise ValueError("Expected a bounded raw array of Coinbase candle rows")
    seen = set()
    points = {}
    for item in rows:
        if (not isinstance(item, list) or len(item) != 6 or type(item[0]) is not int
                or not 0 < item[0] <= int(capture.timestamp()) or item[0] % seconds
                or any(type(value) not in (int, Decimal) for value in item[1:])):
            raise ValueError("Invalid raw Coinbase candle fields or bucket time")
        observed = timestamp(item[0])
        if observed in seen:
            raise ValueError("Chart source contains duplicate raw candle times")
        seen.add(observed)
        low, high, opening, closing, volume = (_numeric(value) for value in item[1:])
        if low <= 0 or not (low <= opening <= high and low <= closing <= high) or volume < 0:
            raise ValueError("Invalid raw Coinbase candle price range or volume")
        close = observed + interval
        if close <= min(capture, ceiling) and (beginning is None or observed >= beginning):
            points[observed] = item[CANDLE_FIELDS[metric]]

    records = [
        {"observed_at": iso(observed), "period_end": iso(observed + interval),
         "values": {spec.metric: value}, "units": {spec.metric: unit},
         "record_id": row["record_id"], "source": original, "retrieved_at": received,
         "available_at": row["available_at"]}
        for observed, value in sorted(points.items())
    ]
    return records, asset, frequency, "coinbase", unit, points


def _chart_input(collection, spec, raw_reader):
    if collection.get("collection_kind") == "library_series":
        query = collection.get("query") or {}
        contract = query.get("dataset") or {}
        records = collection.get("records") or []
        if not query.get("query_result_fully_materialized"):
            raise ValueError("Chart needs a fully materialized library query, not a partial page")
        if (collection.get("input_record_ids") != [row.get("record_id") for row in records]
                or len({row.get("record_id") for row in records}) != len(records)
                or contract.get("dataset_id") != collection.get("dataset_id")):
            raise ValueError("Chart collection lineage differs from its saved records")
        return (records, contract.get("asset"), contract.get("frequency"), contract.get("provider"),
                _unit(contract, spec.metric), None)

    if collection.get("dataset_id") == "live.api.coinbase":
        return _public_coinbase_candles(collection, spec, raw_reader)

    kind = collection.get("metric")
    asset = collection.get("asset")
    if (
        collection.get("schema_version") != "market-record-v1" or collection.get("provider") != "coinbase"
        or collection.get("instrument") != f"{asset}-USD" or collection.get("quote_currency") != "USD"
        or kind not in ("candles", "hourly_candles") or spec.metric not in CANDLE_FIELDS
        or collection.get("dataset_id") != f"live.{kind}.{str(asset).lower()}"
    ):
        raise ValueError("Chart needs a saved library series or a completed Coinbase candle capture")
    source = _source(collection)
    raw = _raw_coinbase_values(collection, source, spec.metric, raw_reader)
    if _unit(collection, "close_usd") != "USD":
        raise ValueError("Coinbase USD chart source has an incompatible price unit")
    unit = "USD" if spec.metric != "volume_asset" else asset
    records = [
        {"observed_at": point["observed_at"], "period_end": point["period_end"],
         "values": {spec.metric: point.get(spec.metric)}, "units": {spec.metric: unit},
         "record_id": collection["record_id"], "source": collection["source"],
         "retrieved_at": collection["retrieved_at"], "available_at": collection.get("available_at")}
        for point in collection.get("series") or []
    ]
    return records, asset, "1d" if kind == "candles" else "1h", "coinbase", unit, raw


def _chart(spec, scope, lookup, as_of, raw_reader):
    collection = lookup(spec.evidence_id)
    if collection.get("record_id") != spec.evidence_id:
        raise ValueError("Chart evidence ID does not match the saved record")
    records, asset, frequency, provider, unit, raw = _chart_input(collection, spec, raw_reader)
    if asset not in scope.assets or not frequency or not provider:
        raise ValueError("Chart source must name an in-scope asset, frequency and provider")
    if not 2 <= len(records) <= 2400:
        raise ValueError("Chart needs 2 to 2400 saved observations")
    start, end = timestamp(scope.start), timestamp(scope.end)
    interval = FIXED_FREQUENCIES.get(frequency)
    sources, points = {}, []
    previous, first_observed, final_interval_end = None, None, None
    for row in records:
        observed = timestamp(row["observed_at"])
        if raw is None and (
            row.get("asset") != asset or row.get("dataset_id") != collection["dataset_id"]
            or row.get("sampling") != frequency or row.get("provider") != provider
        ):
            raise ValueError("Chart observation does not match its dataset contract")
        close = timestamp(row["period_end"]) if row.get("period_end") else None
        if close and (close < observed or (raw is not None and close - observed != interval)):
            raise ValueError("Chart source has an invalid observation interval")
        if not (start <= observed < end) or (close and close > end):
            continue
        if as_of is not None and (close or observed) > as_of:
            raise ValueError("Chart source observation is later than the evidence cutoff")
        received = _capture_time(row, as_of)
        receipt = _source(row)
        source = sources.setdefault((receipt["publisher"], receipt["url"], receipt["sha256"]), receipt)
        value = row.get("values", {}).get(spec.metric)
        decimal = _numeric(value)
        if raw is not None and (observed not in raw or _numeric(raw[observed]) != decimal):
            raise ValueError("Chart point differs from the saved raw Coinbase candle")
        row_unit = _unit(row, spec.metric)
        if row_unit != unit:
            raise ValueError("Chart observation unit does not match its dataset contract")
        position = close or observed
        if previous is not None and position <= previous:
            raise ValueError("Chart points must have distinct chronological timestamps")
        gap = previous is not None and interval is not None and position - previous > interval
        points.append({"at": iso(position), "observed_at": iso(observed), "value": str(value),
                       "evidence_id": row["record_id"], "gap_before": gap})
        previous = position
        final_interval_end = close
        first_observed = observed if first_observed is None else first_observed
        if "source_retrieved_through" not in source or timestamp(received) > timestamp(source["source_retrieved_through"]):
            source["source_retrieved_through"] = received
    if len(points) < 2:
        raise ValueError("Chart needs at least two completed points within the report window")
    internal_gaps = sum(point["gap_before"] for point in points)
    coverage = {"observed_from": iso(first_observed), "plotted_through": points[-1]["at"],
                "internal_gap_count": internal_gaps, "point_count": len(points)}
    if interval:
        coverage["starts_after_window"] = first_observed > start
        coverage["ends_before_window"] = final_interval_end < end if final_interval_end else end - previous > interval
    label_key = RAW_CANDLE_FIELDS.get(spec.metric, spec.metric) if collection.get("dataset_id") == "live.api.coinbase" else spec.metric
    return {"evidence_id": spec.evidence_id, "dataset_id": collection["dataset_id"], "metric": spec.metric,
            "title": METRIC_LABELS.get(label_key, label_key.replace("_", " ").capitalize()),
            "asset": asset, "frequency": frequency, "provider": provider, "unit": unit,
            "start": scope.start, "end": scope.end, "coverage": coverage,
            "sources": list(sources.values()), "points": points}


def _document(item, scope, lookup, as_of, raw_reader):
    row = lookup(item.evidence_id)
    flags = set(row.get("quality_flags") or [])
    if (
        row.get("record_id") != item.evidence_id or row.get("source_kind") in ("feed_item", "search_excerpt")
        or not flags.intersection(("page_fetched", "captured_original_page"))
        or flags.intersection(("provider_excerpt", "article_not_independently_fetched"))
    ):
        raise ValueError("Featured document must be a captured original, not an excerpt or derived item")
    if not isinstance(row.get("text"), str) or item.quote not in row["text"]:
        raise ValueError("Featured document quote is not present in the saved original body: "+item.evidence_id+
            ". Copy one short contiguous source substring including its whitespace; do not join sentences across paragraph breaks. Other saved evidence remains reusable.")
    source = _source(row)
    _saved_raw(row, source, raw_reader, "Featured document")
    if row.get("url") and row["url"] != source["url"]:
        raise ValueError("Featured document URL differs from its source receipt")
    if not row.get("title") or not row.get("published_at"):
        raise ValueError("Featured document needs a saved title and publication time")
    captured = _capture_time(row, as_of)
    published = timestamp(row["published_at"])
    start, end = timestamp(scope.start), timestamp(scope.end)
    if as_of is not None and published > as_of:
        raise ValueError("Featured document was published after the evidence cutoff")
    if item.role == "background":
        valid = published < start
    elif item.role == "later_update":
        valid = published >= end
    else:
        valid = start <= published < end
    if not valid:
        raise ValueError("Featured document publication time does not match its report role")
    return {"evidence_id": item.evidence_id, "title": row["title"], **source,
            "published_at": iso(published), "retrieved_at": captured, "role": item.role,
            "quote": item.quote, "relevance": item.relevance.statement}


def resolve_report_media(draft, lookup, *, evidence_as_of=None, raw_reader=None):
    """Check eligibility, then reconstruct charts and original document cards from evidence."""
    if draft.scope is None:
        if draft.charts or draft.documents:
            raise ValueError("Report media requires a declared research scope")
        return {"charts": [], "documents": []}
    if len({(chart.evidence_id, chart.metric) for chart in draft.charts}) != len(draft.charts):
        raise ValueError("Duplicate chart for the same evidence and metric")
    if len({item.evidence_id for item in draft.documents}) != len(draft.documents):
        raise ValueError("Duplicate featured document")
    as_of = _clock(evidence_as_of) if evidence_as_of is not None else None
    if as_of is not None and timestamp(draft.scope.end) > as_of:
        raise ValueError("Report window ends after its evidence cutoff")
    return {
        "charts": [_chart(chart, draft.scope, lookup, as_of, raw_reader) for chart in draft.charts],
        "documents": [_document(item, draft.scope, lookup, as_of, raw_reader) for item in draft.documents],
    }


def build_report_presentation(draft, lookup, *, user_prompt, created_at, evidence_as_of, raw_reader=None):
    """Build the single report projection consumed by the owner UI and offline replay."""
    media = resolve_report_media(draft, lookup, evidence_as_of=evidence_as_of, raw_reader=raw_reader)
    if draft.scope is None:
        return None
    if not isinstance(user_prompt, str) or not user_prompt.strip() or len(user_prompt) > 20000:
        raise ValueError("A structured report needs its original task question")
    created, cutoff = _clock(created_at), _clock(evidence_as_of)
    if cutoff > created:
        raise ValueError("Evidence cutoff is later than report creation")
    return {"version": "report-presentation-v1",
            "overview": {**draft.scope.model_dump(), "user_prompt": user_prompt,
                         "created_at": iso(created), "evidence_as_of": iso(cutoff), "summary": draft.summary,
                         "document_ids": [item["evidence_id"] for item in media["documents"]]},
            **media}
