"""Bounded final delivery: exact approved facts, or a literal evidence-gap notice."""
import hashlib
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from urllib.parse import urlsplit
from research_data.normalize import timestamp
from research_data.http import dumps, utcnow

NOTICE_TITLE = 'Evidence availability notice'
NOTICE_SUMMARY = 'A source-reviewed factual report could not be completed within this attempt. The requested research conclusion remains unresolved.'
NOTICE_REASON = 'No final factual report passed all required publication checks.'
NOTICE_LIMIT = 'This is an evidence-gap delivery notice, not an approved research conclusion. The original draft and check results are retained.'
MINIMAL_SUMMARY = 'This reduced report contains only the observations listed below. Unresolved conclusions from the full draft have been omitted.'
MINIMAL_LIMIT = 'The requested research scope is only partially answered. Unresolved interpretations, forecasts and recommendations have been omitted.'
MAX_MINIMAL_CLAIMS = 8


def evidence_gap_notice(scope=None):
    from .reports import ReportDraft
    return ReportDraft(title=NOTICE_TITLE, summary=NOTICE_SUMMARY, scope=scope,
        delivery_kind='evidence_gap', observations=[], judgment='insufficient_evidence',
        judgment_reason=NOTICE_REASON, limitations=[NOTICE_LIMIT])


def saved_market_fallback(required_window, assets, *, knowledge, lookup, evidence_as_of, raw_reader=None):
    """Prepare a reviewable brief using only exact completed candles already saved."""
    from .report_presentation import ReportChart, ReportScope, resolve_report_media
    from .reports import ReportDraft

    if knowledge is None or not required_window or len(required_window) != 2:
        return None
    start, end = required_window
    try:
        scope = ReportScope(assets=assets, start=start, end=end)
    except ValueError:
        return None
    start_at, end_at = timestamp(scope.start), timestamp(scope.end)
    if any((clock.hour, clock.minute, clock.second, clock.microsecond) != (0, 0, 0, 0)
           for clock in (start_at, end_at)):
        return None
    days = (end_at-start_at).days
    if days < 2 or end_at > timestamp(evidence_as_of):
        return None
    observations, charts = [], []
    for asset in scope.assets:
        dataset_id = f'live.candles.{asset.lower()}'
        packet = knowledge.market_captures(dataset_id, as_of=evidence_as_of, limit=8)
        for rid in packet['records']:
            try:
                row = lookup(rid)
                if row.get('observed_at') != scope.end:
                    continue
                source = urlsplit((row.get('source') or {}).get('url') or '')
                if (source.scheme, source.netloc, source.path, source.fragment) != (
                        'https', 'api.exchange.coinbase.com', f'/products/{asset}-USD/candles', ''):
                    continue
                chart = ReportChart(evidence_id=rid, metric='close_usd')
                media = SimpleNamespace(scope=scope, charts=[chart], documents=[])
                result = resolve_report_media(media, lambda _: row, evidence_as_of=evidence_as_of,
                                              raw_reader=raw_reader)['charts'][0]
                coverage = result['coverage']
                if (coverage['point_count'] != days or coverage['internal_gap_count']
                        or coverage['starts_after_window'] or coverage['ends_before_window']):
                    continue
                close = str(row['values']['close_usd'])
                if Decimal(close) <= 0 or Decimal(close) != Decimal(result['points'][-1]['value']):
                    continue
            except (ValueError, KeyError, OSError, TypeError, InvalidOperation):
                continue
            charts.append(chart)
            observations.append({
                'statement': f'The completed Coinbase Exchange {asset}-USD daily candle ending at '
                             f'{end_at:%Y-%m-%d %H:%M} UTC closed at {close} USD.',
                'evidence_ids':[chart.evidence_id], 'metric':'close_usd', 'value':close, 'unit':'USD',
            })
            break
    if not observations:
        return None
    return ReportDraft(title='Saved market data brief', summary=MINIMAL_SUMMARY, scope=scope, delivery_kind='minimal',
                       observations=observations, charts=charts, limitations=[MINIMAL_LIMIT])


def validate_notice(draft):
    expected=evidence_gap_notice(draft.scope)
    if draft.model_dump()!=expected.model_dump():
        raise ValueError('Evidence-gap notices accept only the fixed host text and no external factual claims')


def notice_review(draft):
    validate_notice(draft)
    digest=hashlib.sha256(dumps(draft.model_dump(),sort_keys=True).encode()).hexdigest()
    return dict(review_id='notice-'+digest[:24], review_version='support-harness-notice-v1',
        status='notice', reviewer_model='programmatic', reviewed_at=utcnow(),
        draft_sha256=digest, targets=[], evidence_text={}, verdicts=[],
        claims_checked=0, claims_total=0, supported=0, unsupported=0, contradicted=0,
        host_issues=[], revision_required=[], scope='Literal host delivery notice; no source-support certification.')


def verify_notice_review(review,draft):
    from .reports import ReportDraft
    draft=draft if isinstance(draft,ReportDraft) else ReportDraft.model_validate(draft)
    validate_notice(draft)
    expected=notice_review(draft)
    for key in ('status','review_version','review_id','draft_sha256','targets','evidence_text','verdicts','claims_checked','claims_total'):
        if review.get(key)!=expected[key]:raise ValueError('Invalid evidence-gap notice receipt: '+key)


def minimal_report(draft, review, maximum=MAX_MINIMAL_CLAIMS, *, lookup=None,
                   evidence_as_of=None, raw_reader=None, claim_filter=None):
    """Select exact claims supported in the previous review for a final audit."""
    from .review import exact_source_quote

    targets = {target['claim_id']: target for target in review.get('targets', [])}
    verdicts = {verdict['claim_id']: verdict for verdict in review.get('verdicts', [])}
    blocked = {item.get('claim_id') for key in ('revision_required', 'host_issues', 'unassessed_targets')
               for item in review.get(key, [])}
    texts = review.get('evidence_text')

    def previously_supported(cid, claim):
        verdict = verdicts.get(cid, {})
        target = targets.get(cid, {})
        if (cid in blocked or verdict.get('status') != 'supported'
                or verdict.get('scope_alignment') != 'aligned'):
            return False
        if target.get('kind') != 'observation' or any(
                target.get(key) != value for key, value in claim.model_dump().items()):
            return False
        return any(
            quote.get('record_id') in claim.evidence_ids
            and quote.get('purpose', 'support') == 'support'
            and isinstance(quote.get('text'), str) and quote['text'].strip()
            and (texts is None or (
                isinstance(texts.get(quote['record_id']), str)
                and exact_source_quote(quote['text'], texts[quote['record_id']])))
            for quote in verdict.get('quotes', []))

    return _factual_minimal(draft, maximum, lookup=lookup, evidence_as_of=evidence_as_of,
        raw_reader=raw_reader, claim_filter=claim_filter, previously_supported=previously_supported)


def omit_single_reviewed_event(draft, review):
    """Build a reduced draft only when a complete full review isolated one event."""
    from .review import prepare_review_targets, review_targets
    from .reports import ReportDraft

    if draft.delivery_kind != 'full' or draft.scope is None or not draft.observations or len(draft.key_changes) < 2:
        return None
    if review.get('status') != 'needs_revision' or review.get('unassessed_targets') or review.get('unassessed_claim_ids'):
        return None
    if review.get('reviewer_provider') == 'jev':
        if not review.get('batches') or any(batch['status'] != 'validated' for batch in review['batches']):
            return None
    elif review.get('review_stage') != 'full_review' or not review.get('final_prose_bound'):
        return None
    issues = review.get('revision_required', []) + review.get('host_issues', [])
    failed_ids = {issue['claim_id'] for issue in issues}
    if len(failed_ids) != 1:
        return None
    cid = next(iter(failed_ids))
    match = re.fullmatch(r'K([1-9]\d*)', cid)
    if not match:
        return None
    position = int(match[1]) - 1
    if position >= len(draft.key_changes):
        return None

    if review.get('reviewer_provider') == 'jev':
        from .jev_review import TARGET_VERSION
        expected = prepare_review_targets(review_targets(draft, review_version=TARGET_VERSION), review_version=TARGET_VERSION)
    else:
        expected = review_targets(draft, review_version=review.get('review_version'))
    submitted = review.get('targets', [])
    if len(expected) != len(submitted):
        return None
    for target, saved in zip(expected, submitted):
        ids = saved.get('evidence_ids', [])
        if target['kind'] in ('summary', 'judgment'):
            ids = [rid for rid in ids if rid in target['evidence_ids'] or not rid.startswith('context-')]
        if {**saved, 'evidence_ids': ids} != target:
            return None
    verdicts = review.get('verdicts', [])
    by_id = {v['claim_id']: v for v in verdicts}
    if len(verdicts) != len(expected) or by_id.keys() != {t['claim_id'] for t in expected}:
        return None
    if any(v['status'] not in ('supported', 'reasonable_inference', 'conditional') for v in verdicts if v['claim_id'] != cid):
        return None

    # Use a scope-derived heading; arbitrary factual headlines are not separate
    # targets in the normal source-review contract.
    start, end = timestamp(draft.scope.start), timestamp(draft.scope.end)
    interval = 'Weekly ' if end-start == timedelta(days=7) else ''
    if start.time() == end.time() and start.hour == start.minute == start.second == start.microsecond == 0:
        last = end - timedelta(days=1)
        prefix = str(start.day) if (start.year, start.month) == (last.year, last.month) else start.strftime('%d %B %Y')
        span = f'{prefix}–{last.day} {last.strftime("%B %Y")} UTC' if last != start else start.strftime('%d %B %Y UTC')
    else:
        span = f'{draft.scope.start} to {draft.scope.end} (exclusive end)'
    title = f'{", ".join(draft.scope.assets)} {interval}Research Review — {span}'
    body = draft.model_dump()
    body.update(title=title, delivery_kind='minimal')
    del body['key_changes'][position]
    rejected_sources = set(draft.key_changes[position].evidence_ids)
    dependent_items = []
    for field, prefix in (('documents', 'DOC'), ('questions_to_watch', 'M')):
        kept = []
        for index, item in enumerate(body[field]):
            item_id = f'{prefix}{index + 1}'
            witnesses = {q['record_id'] for q in by_id[item_id].get('quotes', [])
                         if q.get('purpose', 'support') == 'support' and q.get('record_id')}
            card_source = {item['evidence_id']} if field == 'documents' else set()
            if (witnesses and witnesses <= rejected_sources) or card_source & rejected_sources:
                dependent_items.append({'claim_id': item_id, 'path': f'/{field}/{index}',
                    'reason': f'The cited source or all supporting witnesses belong to omitted event {cid}.',
                    'evidence_ids': sorted((witnesses | card_source) & rejected_sources)})
            else:
                kept.append(item)
        body[field] = kept
    removed_ids = {cid, *(item['claim_id'] for item in dependent_items)}
    candidate = ReportDraft.model_validate(body)
    return candidate, {'claim_id': cid, 'path': f'/key_changes/{position}', 'reason': issues[0]['reason'],
                       'dependent_items': dependent_items,
                       'retained_original_review_target_ids': [t['claim_id'] for t in expected if t['claim_id'] not in removed_ids],
                       'source_review_id': review['review_id'], 'candidate_kind': 'event_omission',
                       'scope': draft.scope.model_dump(), 'title_from_scope': title}


def prepare_factual_minimal(draft, *, lookup, evidence_as_of, claim_filter, raw_reader=None):
    """Select locally valid facts for a fresh audit when this draft has no review."""
    if not callable(lookup) or not callable(claim_filter):
        raise TypeError('An unaudited candidate requires saved evidence and a dated-claim validator')
    return _factual_minimal(draft, MAX_MINIMAL_CLAIMS, lookup=lookup, evidence_as_of=evidence_as_of,
        raw_reader=raw_reader, claim_filter=claim_filter)


def _factual_minimal(draft, maximum, *, lookup, evidence_as_of, raw_reader, claim_filter,
                     previously_supported=None):
    from .claims import validate_claims
    from .report_presentation import resolve_report_media
    from .reports import ReportDraft

    saved_records = {}
    section_limit = min(maximum, MAX_MINIMAL_CLAIMS)

    def saved_lookup(record_id):
        if record_id not in saved_records:
            row = lookup(record_id)
            if not isinstance(row, dict) or row.get('record_id') != record_id:
                raise ValueError('Retained evidence ID does not match the saved record')
            saved_records[record_id] = row
        return saved_records[record_id]

    def eligible_claims(prefix, claims):
        kept = []
        for index, claim in enumerate(claims, 1):
            if len(kept) >= section_limit:
                break
            cid = prefix + str(index)
            if previously_supported is not None and not previously_supported(cid, claim):
                continue
            try:
                if lookup is not None:
                    validate_claims([claim], saved_lookup)
                if claim_filter is not None and not claim_filter(claim):
                    continue
            except (ValueError, KeyError, OSError, TypeError, IndexError, OverflowError):
                continue
            kept.append((cid, claim.model_copy(deep=True)))
        return kept

    observations = eligible_claims('O', draft.observations)
    events = eligible_claims('K', draft.key_changes)
    # Reserve slots for both sections, then let either section use empty slots.
    half = MAX_MINIMAL_CLAIMS // 2
    kept_observations = observations[:max(half, MAX_MINIMAL_CLAIMS - len(events))]
    kept_events = events[:max(half, MAX_MINIMAL_CLAIMS - len(observations))]
    if not (kept_observations or kept_events):
        return None, []

    reduced = ReportDraft(title='Limited evidence report', summary=MINIMAL_SUMMARY,
        scope=draft.scope, delivery_kind='minimal', observations=[claim for _, claim in kept_observations],
        key_changes=[claim for _, claim in kept_events], limitations=[MINIMAL_LIMIT])
    if lookup is not None and evidence_as_of is not None and reduced.scope is not None:
        chart_keys = set()
        for chart in draft.charts:
            key = (chart.evidence_id, chart.metric)
            if key in chart_keys:
                continue
            try:
                trial = reduced.model_copy(update={'charts': [chart]})
                resolve_report_media(trial, saved_lookup, evidence_as_of=evidence_as_of, raw_reader=raw_reader)
            except (ValueError, KeyError, OSError, TypeError, IndexError, OverflowError):
                continue
            reduced.charts.append(chart.model_copy(deep=True))
            chart_keys.add(key)
    return reduced, [cid for cid, _ in (*kept_observations, *kept_events)]
