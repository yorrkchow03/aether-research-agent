"""Check dated aggregate ETH ETF flows against Farside's saved Total column."""
from datetime import date
from decimal import Decimal
from fnmatch import fnmatchcase
import re
from unicodedata import normalize
from urllib.parse import urlsplit

from research_data.http import Snapshot
from research_data.normalize import etf_flows, number

from .report_presentation import _saved_raw

_DATASET = 'flows.farside.eth_etf.daily'
_PAGE_PATH = '/ethereum-etf-flow-all-data'
_TICKERS = r'ETHA|ETHB|FETH|ETHW|TETH|ETHV|QETH|EZET|MSSE|ETHE'
_PUBLISHER = re.compile(r'\bFarside\b', re.I)
_ETHER = re.compile(r'\b(?:ETH|Ether|Ethereum)\b|以太坊', re.I)
_OTHER_ASSET = re.compile(r'\b(?:BTC|Bitcoin|SOL|Solana|XRP)\b|比特币', re.I)
_FLOW = re.compile(r'\bETF\b|\bETFs\b', re.I)
_TOTAL = re.compile(r'\b(?:row totals?|total rows?|(?:aggregate|combined) (?:net )?(?:flows?|inflows?|outflows?)|'
                    r'net (?:flows?|inflows?|outflows?)|total (?:net )?(?:flows?|inflows?|outflows?))\b'
                    r'|(?:合计|总计|总额|净流入|净流出|净流量)', re.I)
_ROW_TOTAL = re.compile(
    r'\b(?:row totals?|total (?:ETF[- ]flows? )?rows?)\b|(?:合计|总计|总额)', re.I)
_DAILY = re.compile(r'\b(?:daily|dated|respectively)\b|每日|分别', re.I)
_AGGREGATE_SCOPE = re.compile(
    r'\b(?:aggregate|combined|all (?:US )?(?:spot )?(?:Ethereum|Ether|ETH) ETFs)\b|合计|总计', re.I)
_FUND = re.compile(rf'\b(?:{_TICKERS}|BlackRock|Fidelity|Bitwise|Grayscale|VanEck|Invesco|Franklin|21Shares)\b'
                   r'|贝莱德|富达|灰度', re.I)
_FLOW_WORD = re.compile(r'\b(?:flows?|inflows?|outflows?)\b|流入|流出|流量', re.I)
_PERIOD_TOTAL = re.compile(
    r'\b(?:(?:weekly|monthly|cumulative|multi-day|period|year-to-date) (?:net )?(?:flows?|outflows?|inflows?|totals?)'
    r'|(?:flows?|outflows?|inflows?|totals?) (?:for|over|across) the (?:week|month|period))\b'
    r'|周度|全周|月度|累计', re.I)
_RANGE = re.compile(
    r'\d{1,2}\s*[-–—]\s*\d{1,2}\s+[A-Za-z]{3,9}\b|\d{1,2}\s*[-–—]\s*\d{1,2}日')
_NEGATIVE = re.compile(r'\boutflows?\b|净?流出', re.I)
_POSITIVE = re.compile(r'\binflows?\b|净?流入', re.I)

_MONTHS = 'January February March April May June July August September October November December'.split()
_MONTH_NAMES = '|'.join(f'{month}|{month[:3]}' for month in _MONTHS)
_DATE = re.compile(
    rf'(?<![A-Za-z0-9/])(?:(?P<iso>\d{{4}}[-/]\d{{1,2}}[-/]\d{{1,2}})'
    rf'|(?P<china_year>\d{{4}}年\s*)?(?P<china_month>\d{{1,2}})月\s*(?P<china_day>\d{{1,2}})日'
    rf'|(?P<before_day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<after_month>{_MONTH_NAMES})\.?(?:\s*,?\s+(?P<after_year>\d{{4}}))?'
    rf'|(?P<before_month>{_MONTH_NAMES})\.?\s+(?P<after_day>\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,?\s+(?P<comma_year>\d{{4}}))?'
    rf'|(?P<ambiguous>\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?))(?![A-Za-z0-9/])', re.I,
)
_SCALE = r'trillions?|billions?|millions?|thousands?|tn|bn|mm|mn|[tbmk]|亿|万'
_MULTIPLIERS = {
    't': 10**12, 'tn': 10**12, 'trillion': 10**12,
    'b': 10**9, 'bn': 10**9, 'billion': 10**9, '亿': 10**8,
    'm': 10**6, 'mm': 10**6, 'mn': 10**6, 'million': 10**6, '万': 10**4,
    'k': 10**3, 'thousand': 10**3,
}
_CURRENCY = r'US\s*\$|USD|US dollars?|(?:CA?|AU?|NZ|HK|SG)?\$|EUR|GBP|CNY|JPY|CAD|AUD|HKD|[€£¥]|美元'
_AMOUNT = re.compile(
    rf'(?<![A-Za-z0-9.,])(?P<open>\()?\s*(?P<sign>[+-])?\s*(?P<currency>{_CURRENCY})?'
    rf'\s*(?P<inner_sign>[+-])?\s*(?P<value>\d+(?:,\d{{3}})*(?:\.\d+)?)'
    rf'(?:\s*(?P<scale>{_SCALE})(?![A-Za-z]))?'
    rf'(?:\s*(?P<after_currency>{_CURRENCY})(?:\s*(?P<after_scale>{_SCALE}))?(?![A-Za-z]))?'
    rf'(?(open)\s*\))(?![A-Za-z0-9]|[.,]\d|\s*%)', re.I,
)
_SHARED_SCALE = re.compile(
    rf'(?:\b(?:in|units?:)\s+(?:{_CURRENCY})\s*(?P<after>{_SCALE})(?![A-Za-z])'
    rf'|\bin\s+(?P<before>{_SCALE})(?![A-Za-z])\s+(?:of\s+)?(?:{_CURRENCY})'
    rf'|(?P<currency>{_CURRENCY})\s*(?P<compact>mm|mn|m|bn)(?![A-Za-z0-9]))', re.I,
)
_BARE_TOTAL = re.compile(
    r'(?:row total|total row|(?:net )?(?:flow|outflow|inflow)s?)\s*(?:of|was|were|:|=)?\s*$'
    r'|(?:\b(?:was|were|equaled|reached|stood at)|为|[:=])\s*$',
    re.I)
_PAIR_SEPARATOR = re.compile(r',|\b(?:and|while|versus|vs\.?|followed by)\b|、|，|而|及|和', re.I)
_SENTENCE = re.compile(r'[。！？\n]+|(?<=[a-z0-9)])\.(?=\s+[A-Z])')
_CLAUSE = re.compile(r'[;；]+')
_TIME = re.compile(r'\b\d{1,2}:\d{2}(?::\d{2})?\b')


class DatedETFValidationError(ValueError):
    """A claimed daily total failed deterministic validation."""


def _day(match):
    if match['iso']:
        return date(*(int(part) for part in re.split('[-/]', match['iso'])))
    if match['china_year']:
        return date(int(match['china_year'].split('年')[0]), int(match['china_month']), int(match['china_day']))
    month = match['before_month'] or match['after_month']
    year = match['after_year'] or match['comma_year']
    if month and year:
        months = [name[:3].lower() for name in _MONTHS]
        return date(int(year), months.index(month[:3].lower()) + 1, int(match['before_day'] or match['after_day']))
    raise ValueError('a complete, unambiguous trading date is required')


def _source_kind(row):
    if row.get('dataset_id') == _DATASET:
        return 'row'
    if row.get('dataset_id') != 'live.webpage':
        return None
    url = urlsplit(row.get('source', {}).get('url', ''))
    if url.scheme == 'https' and url.hostname in ('farside.co.uk', 'www.farside.co.uk') and url.path.rstrip('/') == _PAGE_PATH:
        return 'page'
    return None


def _row_total(row):
    values = row.get('values', {})
    metric = 'total_net_flow_usd' if 'total_net_flow_usd' in values else 'net_flow_usd'
    if not any(unit in ('USD', 'USD/trading day') and fnmatchcase(metric, pattern)
               for pattern, unit in row.get('units', {}).items()):
        return {}
    amount = number(values.get(metric))
    if amount is None:
        return {}
    return {date.fromisoformat(row['observed_at'][:10]): {Decimal(amount)}}


def _page_totals(row, raw_reader):
    source = row['source']
    raw = _saved_raw(row, source, raw_reader, 'Farside dated ETF flow table')
    rows = etf_flows(Snapshot(source, raw))
    totals = {}
    for source_row in rows:
        amount = source_row['values']['total_net_flow_usd']
        if amount is not None:
            day = date.fromisoformat(source_row['observed_at'][:10])
            totals.setdefault(day, set()).add(Decimal(amount))
    return totals


def _amount_tokens(clause, dates, row_units):
    dates_and_times = [*dates, *_TIME.finditer(clause)]
    table_units = row_units or _SHARED_SCALE.search(clause)
    for match in _AMOUNT.finditer(clause):
        if any(match.start('value') < span.end() and span.start() < match.end('value') for span in dates_and_times):
            continue
        if (match['open'] or match['currency'] or match['after_currency'] or match['scale'] or match['sign']
            or match['inner_sign']
            or _BARE_TOTAL.search(clause[:match.start()].rstrip())
            or (table_units and '.' in match['value'])):
            yield match


def _dollars(match, clause, support, row_units):
    currencies = {match['currency'], match['after_currency']} - {None}
    if any(re.sub(r'\s+', '', item).casefold() not in {'usd', 'us$', '$', 'usdollar', 'usdollars', '美元'}
           for item in currencies):
        raise ValueError('the figure requires a US dollar unit')
    if match['scale'] and match['after_scale']:
        raise ValueError('the stated unit is ambiguous')
    scale = match['scale'] or match['after_scale']
    if not scale and not currencies:
        shared = list(_SHARED_SCALE.finditer(clause))
        scales = {item['before'] or item['after'] or item['compact'] for item in shared}
        if len(scales) > 1:
            raise ValueError('the stated unit is ambiguous')
        scale = next(iter(scales), None)
    if not scale and not currencies and row_units:
        scale = 'million'
    if not scale and not currencies:
        raise ValueError('the figure requires an explicit US dollar unit')
    multiplier = _MULTIPLIERS[scale.lower().removesuffix('s')] if scale else 1
    magnitude = Decimal(match['value'].replace(',', '')) * multiplier
    negative = _NEGATIVE.search(support)
    positive = _POSITIVE.search(support)
    signs = {match['sign'], match['inner_sign']} - {None}
    if len(signs) > 1 or (match['open'] and '+' in signs):
        raise ValueError('the stated sign is ambiguous')
    if match['open'] or '-' in signs:
        if positive and not negative:
            raise ValueError('the amount sign conflicts with the stated inflow')
        return -magnitude
    if '+' in signs and negative and not positive:
        raise ValueError('the amount sign conflicts with the stated outflow')
    if negative and positive and not signs:
        raise ValueError('the direction of the flow is ambiguous')
    return -magnitude if negative and not positive else magnitude


def _claimed_pairs(clause, dates, amounts, row_units):
    tokens = sorted([(token.start(), kind, token) for kind, items in (('date', dates), ('amount', amounts))
                     for token in items])
    if len(dates) != len(amounts):
        raise ValueError('pair each figure with its own trading date')
    for i in range(0, len(tokens), 2):
        _, first_kind, first = tokens[i]
        _, second_kind, second = tokens[i + 1]
        if first_kind == second_kind:
            raise ValueError('pair each figure directly with its trading date')
        span_start = 0 if i == 0 else tokens[i - 1][2].end()
        span_end = len(clause) if i + 2 == len(tokens) else tokens[i + 2][2].start()
        if i and not _PAIR_SEPARATOR.search(clause[span_start:first.start()]):
            raise ValueError('separate distinct date and amount pairs')
        leading = clause[span_start:first.start()]
        trailing = clause[second.end():span_end]
        cut = _PAIR_SEPARATOR.search(trailing)
        support = leading + clause[first.end():second.start()] + (trailing if not cut else trailing[:cut.start()])
        if not _NEGATIVE.search(support) and not _POSITIVE.search(support):
            support = clause
        day, amount = (first, second) if first_kind == 'date' else (second, first)
        yield _day(day), _dollars(amount, clause, support, row_units)


def _render_amount(amount):
    return f'{(amount / 1_000_000).normalize():,f} USD million'


def _etf_flow_context(text):
    return (bool(_FLOW.search(text) or _PUBLISHER.search(text))
            and bool(_TOTAL.search(text) or _FLOW_WORD.search(text) or re.search(r'\bFarside\s*:', text, re.I))
            and not (_OTHER_ASSET.search(text) and not _ETHER.search(text)))


def check_etf_flow_statement(statement, evidence_ids, lookup, *, path=None, raw_reader=None):
    """Verify recognizable dated ETH ETF totals, or raise a focused repair error.

    A return value of zero leaves the statement to ordinary source review.
    """
    text = normalize('NFKC', statement).replace('−', '-')
    if not _DATE.search(text) or not _etf_flow_context(text):
        return 0

    portions = []
    for sentence in _SENTENCE.split(text):
        if not _etf_flow_context(sentence):
            continue
        row_units = bool(_ROW_TOTAL.search(sentence))
        for clause in _CLAUSE.split(sentence):
            dates = list(_DATE.finditer(clause))
            if not dates or (_PERIOD_TOTAL.search(clause) and not _ROW_TOTAL.search(clause)):
                continue
            if _RANGE.search(clause) and not (_DAILY.search(clause) or _ROW_TOTAL.search(clause)):
                continue
            if _FUND.search(clause) and not _AGGREGATE_SCOPE.search(clause):
                continue
            amounts = list(_amount_tokens(clause, dates, row_units))
            if amounts:
                portions.append((clause, dates, amounts, row_units))
    if not portions:
        return 0

    rows = [(rid, lookup(rid)) for rid in dict.fromkeys(evidence_ids)]
    sources = [(rid, row, kind) for rid, row in rows if (kind := _source_kind(row))]
    if not sources and not (_PUBLISHER.search(text) and _ETHER.search(text)):
        return 0
    location = f' at {path}' if path else ''
    try:
        if any(_RANGE.search(clause) for clause, _, _, _ in portions):
            raise ValueError('write each trading date in full beside its own daily total')
        pairs = [pair for clause, dates, amounts, row_units in portions
                 for pair in _claimed_pairs(clause, dates, amounts, row_units)]
        if not sources:
            raise ValueError('cite the saved Farside flow table or a saved dated Farside ETH ETF row')
        source_rows = [(rid, _page_totals(row, raw_reader) if kind == 'page' else _row_total(row))
                       for rid, row, kind in sources]
        errors = []
        for day, amount in pairs:
            totals = {value for _, totals_by_day in source_rows for value in totals_by_day.get(day, ())}
            if not totals:
                errors.append(f'no published Total was found for {day} in the cited Farside capture')
            elif len(totals) != 1:
                errors.append(f'cited Farside captures disagree for {day}')
            elif amount not in totals:
                expected = next(iter(totals))
                ids = [rid for rid, totals_by_day in source_rows if day in totals_by_day]
                errors.append(f'{day} has a published Total of {_render_amount(expected)} in {", ".join(ids[:3])}, '
                              f'but the statement gives {_render_amount(amount)}')
        if errors:
            raise ValueError('. '.join(errors[:4]))
    except (ValueError, KeyError) as exc:
        raise DatedETFValidationError(
            f'Farside dated ETH ETF flow validation failed{location}. {exc}. '
            'Bind each daily total to the exact published trading date and amount in USD, '
            'or remove the unverified numeric assertion.'
        ) from None
    return len(pairs)
