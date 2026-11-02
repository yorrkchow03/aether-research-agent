"""Bounded event/source-update context for source-linked research reports.

This service reads existing public evidence only. It does not call a model,
fetch the internet, mutate sources, or infer event equivalence from keywords.
Reviewed event history, explicit relations and same-source versions are kept
distinct from possible related documents found by local search.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from research_data.http import utcnow
from research_data.library.time import timestamp


VERSION = 'report-event-context-v1'
OFFICIAL_SOURCE_NAMES = frozenset({'ethereum', 'bitcoin_core', 'sec', 'federal_reserve'})
STRUCTURAL_RELATIONS = frozenset({'content_revision_of', 'metadata_revision_of',
    'report_attachment_of', 'supersedes', 'corrects', 'contradicts', 'unresolved_conflict'})
SCOPE = ('Derived context over existing captured evidence. A source version, '
    'analyst event classification or keyword-search candidate does not prove '
    'real-world event status, contradiction, cancellation, causation or publisher truth.')


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), default=str).encode()).hexdigest()[:24]


def _public(row):
    return not (row.get('private') or row.get('profile') is not None or
        str(row.get('record_id', '')).startswith('context-') or
        str(row.get('dataset_id', '')).startswith(('private.', 'local.user_', 'local.portfolio')))


def _source_url(row):
    value = row.get('url') or (row.get('source') or {}).get('url')
    if not value:
        return None
    try:
        parsed = urlsplit(str(value))
    except ValueError:
        return None
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        return None
    # Fragments can identify distinct forum posts; never erase them to dedupe.
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or '/', query, parsed.fragment))


def _times(row):
    known = [row.get(k) for k in ('available_at', 'first_seen_at', 'retrieved_at')]
    known.append((row.get('source') or {}).get('retrieved_at'))
    known = [value for value in known if value]
    acquired = max(known, key=timestamp) if known else None
    return row.get('published_at'), acquired


def _eligible(row, before, mode):
    if not _public(row):
        return False, 'private_context_excluded'
    try:
        published, acquired = _times(row)
        if published and timestamp(published) > before:
            return False, 'publication_after_as_of'
        if mode == 'strict' and (not acquired or timestamp(acquired) > before):
            return False, 'not_acquired_by_as_of'
        return True, None
    except (ValueError, TypeError, OverflowError):
        return False, 'invalid_source_time'


def _card(row, before):
    published, acquired = _times(row)
    source = row.get('source') or {}
    result = {'evidence_id': row['record_id'], 'title': str(row.get('title') or row['record_id'])[:240],
        'url': _source_url(row), 'source_name': source.get('name') or row.get('provider'),
        'source_kind': row.get('source_kind'), 'document_id': row.get('document_id'),
        'version_id': row.get('version_id') or row['record_id'], 'published_at': published, 'acquired_at': acquired,
        'source_sha256': source.get('sha256'),
        'publication_status': 'claimed_by_source_not_independently_authenticated' if published else 'unknown',
        'version_acquired_after_as_of': bool(acquired and timestamp(acquired) > before)}
    return result


def _report_source_ids(body):
    found = set()
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('evidence_ids', 'supporting_evidence_ids', 'counter_evidence_ids', 'source_evidence_ids') and isinstance(item, list):
                    found.update(rid for rid in item if isinstance(rid, str) and not rid.startswith('context-'))
                elif key not in ('review', 'semantic_checks', 'sources'):
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(body.get('report') or body.get('draft') or body)
    return found


def _previous(knowledge, value, before, gaps):
    if value is None:
        return None
    rid = value if isinstance(value, str) else None
    if isinstance(value, dict):
        rid = value.get('report_id') or value.get('output_id')
    if not rid:
        gaps.append({'reason': 'previous_report_identity_missing'})
        return None
    try:
        body = knowledge.report(rid)
    except (AttributeError, ValueError, KeyError, OSError):
        try:
            output = knowledge.library.output(rid)
            body = {**output['body'], 'created_at':output['created_at']}
        except (AttributeError, ValueError, KeyError, OSError):
            gaps.append({'reason': 'previous_report_unavailable'})
            return None
    try:
        if not body.get('created_at') or timestamp(body['created_at']) > before:
            gaps.append({'reason': 'previous_report_not_created_by_as_of'})
            return None
    except (ValueError, TypeError, OverflowError):
        gaps.append({'reason': 'previous_report_time_invalid'})
        return None
    return {'report_id': rid, 'created_at': body['created_at'],
        'title': (body.get('report') or body.get('draft') or {}).get('title'),
        'source_ids': _report_source_ids(body)}


def build_report_event_context(knowledge, sources, *, query='', as_of=None,
        availability_mode='observed', max_events=6, max_sources=12,
        previous_report=None, related_queries=None, max_related_queries=2):
    """Read bounded event/version context without silently inventing semantics.

    ``sources`` are selected evidence rows or IDs, re-opened through the source
    store. ``related_queries`` are at most two explicit local public-entity
    queries, either strings or ``{'query': ..., 'source': ...}``. If absent, a
    nonempty main query is used once. Related search candidates remain unverified
    and never merge distinct URLs into an event. No provider is constructed.

    Previous-report comparison requires a resolvable saved report ID; membership
    changes are evidence additions/reuse, not confirmed factual or view changes.
    """
    if availability_mode not in ('observed', 'strict'):
        raise ValueError('Unknown event memory availability mode')
    if not isinstance(query, str) or related_queries is not None and not isinstance(related_queries, (list, tuple)):
        raise ValueError('Supply a query string and bounded related-query sequence')
    if not 1 <= max_events <= 12 or not 1 <= max_sources <= 24 or not 0 <= max_related_queries <= 2:
        raise ValueError('Invalid event context bounds')
    as_of = as_of or utcnow()
    before = timestamp(as_of)
    if isinstance(sources, (str, bytes)):
        raise ValueError('Supply a bounded sequence of selected evidence rows or IDs')
    selected = list(sources)
    if len(selected) > 150:
        raise ValueError('Bound source retrieval before event context construction')
    gaps = []; records = {}; attempted = set(); initial_ids = []; relations = []
    stats = {'source_read_attempts': 0, 'relationship_queries': 0, 'related_queries': 0,
        'event_history_limit': 120, 'source_budget_exhausted': False}
    library = getattr(knowledge, 'library', None)

    def read(rid, *, initial=False):
        if not isinstance(rid, str) or not rid:
            return None
        if rid in records:
            return records[rid]
        if rid in attempted:
            return None
        if len(attempted) >= max_sources:
            stats['source_budget_exhausted'] = True
            gaps.append({'reason': 'source_read_budget_exhausted'})
            return None
        attempted.add(rid); stats['source_read_attempts'] += 1
        try:
            row = knowledge.get(rid)
            if row.get('record_id') != rid:
                raise ValueError('Evidence identity differs')
            allowed, reason = _eligible(row, before, availability_mode)
            if not allowed:
                gaps.append({'reason': reason})
                return None
            if library and library.contains(rid) and library.exclusion_reasons(rid):
                gaps.append({'reason': 'quarantined_source'})
                return None
        except (AttributeError, ValueError, KeyError, OSError, TypeError) as exc:
            gaps.append({'reason': 'source_unavailable_or_integrity_failed', 'error_type': type(exc).__name__})
            return None
        records[rid] = row
        if initial:
            initial_ids.append(rid)
        return row

    for item in selected:
        rid = item if isinstance(item, str) else item.get('record_id') if isinstance(item, dict) else None
        if not rid:
            gaps.append({'reason': 'selected_source_identity_missing'}); continue
        read(rid, initial=True)

    # Classifications are themselves acquired records. Both modes expose only
    # event records actually saved no later than the requested report as-of.
    try:
        history = knowledge.event_history('ALL', cutoff=as_of, limit=120)
    except (AttributeError, ValueError, KeyError, OSError, TypeError) as exc:
        history = []; gaps.append({'reason': 'event_history_unavailable', 'error_type': type(exc).__name__})
    events = defaultdict(list)
    if len(history) >= 120:
        gaps.append({'reason': 'event_history_may_be_truncated'})
    for event in history:
        try:
            known = event.get('available_at')
            if not known or timestamp(known) > before:
                continue
            if not event.get('event_id') or not event.get('review_id'):
                continue
            events[event['event_id']].append(event)
        except (ValueError, TypeError, OverflowError):
            gaps.append({'reason': 'invalid_event_history_time'})
    events = {eid: sorted(items, key=lambda e:(timestamp(e['available_at']), e.get('version', 0)))
              for eid, items in events.items()
              if any(set(e.get('evidence_ids', [])).intersection(records) for e in items)}
    for items in events.values():
        for event in items:
            for rid in event.get('evidence_ids', []):
                read(rid)
            unavailable = sum(rid not in records for rid in event.get('evidence_ids', []))
            if unavailable:
                gaps.append({'reason':'event_evidence_incomplete','source_count':unavailable})

    # Query explicit source links only for the initial bounded selection. Later
    # relationships without their own acquisition timestamp are not strict facts.
    for rid in initial_ids[:6]:
        if not library:
            break
        try:
            packet = library.record_links(rid, known_by=as_of if availability_mode=='strict' else None, limit=12)
            stats['relationship_queries'] += 1
            if packet.get('next_offset') is not None:
                gaps.append({'reason': 'source_relationships_truncated'})
            for relation in packet.get('results', []):
                if relation.get('predicate') not in STRUCTURAL_RELATIONS:
                    continue
                body = relation.get('body') or {}
                known = relation.get('known_at') or body.get('known_at') or body.get('effective_known_at')
                if known:
                    try:
                        if timestamp(known) > before:
                            continue
                    except (ValueError, TypeError, OverflowError):
                        gaps.append({'reason': 'invalid_relationship_time'}); continue
                elif availability_mode == 'strict':
                    gaps.append({'reason': 'relationship_acquisition_unknown'}); continue
                left, right = relation.get('subject'), relation.get('object')
                a, b = read(left), read(right)
                if not a or not b:
                    gaps.append({'reason': 'relationship_endpoint_not_in_context'}); continue
                relations.append({'subject':left,'predicate':relation['predicate'],'object':right,
                    'known_at':known,'basis':'saved_source_relationship',
                    'relationship_time_verified':known is not None,
                    'scope':'A saved structural/review relation; content revision alone is not a publisher correction.'})
        except (AttributeError, ValueError, KeyError, OSError, TypeError) as exc:
            gaps.append({'reason':'source_relationships_unavailable','error_type':type(exc).__name__})
    if len(initial_ids) > 6 and library:
        gaps.append({'reason':'relationship_query_budget_exhausted'})

    related_queries = ([query] if query.strip() else []) if related_queries is None else list(related_queries)
    if len(related_queries) > max_related_queries:
        gaps.append({'reason':'related_query_budget_exhausted'})
    candidates = []
    for item in related_queries[:max_related_queries]:
        params = {'query':item} if isinstance(item, str) else dict(item) if isinstance(item, dict) else {}
        text = params.get('query', '')
        if not isinstance(text, str) or not text.strip() or len(text) > 180 or set(params)-{'query','source'}:
            gaps.append({'reason':'invalid_related_query'}); continue
        try:
            stats['related_queries'] += 1
            if library:
                packet = library.search_documents(text, source=params.get('source'),
                    end=min((as_of, library.window_end), key=timestamp), mode=availability_mode,
                    known_by=as_of if availability_mode=='strict' else None, limit=6)
                found = packet['results']
            else:
                if params.get('source'):
                    gaps.append({'reason':'source_filtered_search_unavailable'}); continue
                found = knowledge.candidate_news(text, cutoff=as_of, mode=availability_mode,
                    original_only=True, material_only=True, include_updates=True)['candidates'][:6]
            # This is source priority only, not a truth/relevance verdict.
            def priority(row):
                name = row.get('source_id') or (row.get('source') or {}).get('name') or str(row.get('dataset_id','')).removeprefix('library.documents.')
                return (name not in OFFICIAL_SOURCE_NAMES, -(timestamp(row['published_at']).timestamp() if row.get('published_at') else 0))
            for found_row in sorted(found, key=priority):
                rid = found_row.get('record_id')
                if rid in initial_ids or any(c['evidence_id']==rid for c in candidates):
                    continue
                row = read(rid)
                if not row:
                    continue
                card = _card(row, before)
                newer = [old for old in initial_ids if records[old].get('published_at') and row.get('published_at') and
                    timestamp(row['published_at']) > timestamp(records[old]['published_at'])]
                candidates.append({**card,'query':text,'source_filter':params.get('source'),
                    'newer_than_selected_source_ids':newer,
                    'publication_lag_seconds':{old:(timestamp(row['published_at'])-timestamp(records[old]['published_at'])).total_seconds() for old in newer},
                    'needs_original_review':True,'event_equivalence_confirmed':False,
                    'scope':'Possible related local-search result. A newer publication is not proof it updates/cancels the selected event.'})
        except (AttributeError, ValueError, KeyError, OSError, TypeError) as exc:
            gaps.append({'reason':'related_search_unavailable','error_type':type(exc).__name__})

    # Union only explicit source/reviewed-event identity; keyword candidates never
    # merge distinct sources. Keep both versions and both sides of disagreement.
    parents = {rid:rid for rid in records}
    def find(rid):
        while parents[rid] != rid:
            parents[rid] = parents[parents[rid]];rid = parents[rid]
        return rid
    def union(ids):
        eligible = [rid for rid in ids if rid in parents]
        for rid in eligible[1:]:
            parents[find(rid)] = find(eligible[0])
    for items in events.values():
        union([rid for e in items for rid in e.get('evidence_ids', [])])
    for relation in relations:
        union([relation['subject'],relation['object']])
    same_sources = defaultdict(list); declared_events = defaultdict(list)
    for rid,row in records.items():
        identity = row.get('document_id') or _source_url(row)
        if identity:
            same_sources[str(identity)].append(rid)
        if row.get('event_id'):
            known = row.get('event_known_at')
            try:
                if availability_mode!='strict' or known and timestamp(known)<=before:
                    declared_events[str(row['event_id'])].append(rid)
            except (ValueError, TypeError, OverflowError):
                gaps.append({'reason':'invalid_declared_event_time'})
    for ids in [*same_sources.values(), *declared_events.values()]:
        union(ids)
    groups = defaultdict(list)
    for rid in records:
        groups[find(rid)].append(rid)
    cards = {rid:_card(row,before) for rid,row in records.items()}
    grouped = []
    for ids in groups.values():
        ids.sort(key=lambda rid:(timestamp(cards[rid]['published_at'] or cards[rid]['acquired_at']).timestamp()
            if cards[rid]['published_at'] or cards[rid]['acquired_at'] else 0, rid))
        event_ids = [eid for eid,items in events.items() if any(set(e.get('evidence_ids',[])).intersection(ids) for e in items)]
        attached = [r for r in relations if r['subject'] in ids and r['object'] in ids]
        if event_ids:
            kind = 'reviewed_event'
        elif any(set(v).intersection(ids) for v in declared_events.values()):
            kind = 'declared_event'
        elif len(ids)>1:
            identities={str(records[rid].get('document_id') or _source_url(records[rid]) or rid) for rid in ids}
            kind = 'source_updates' if len(identities)==1 else 'explicit_relation_group'
        else:
            kind = 'unlinked_source'
        updates=[]
        for eid in event_ids:
            for event in events[eid]:
                updates.append({'event_id':eid,'version':event.get('version'),'recorded_status':event.get('status'),
                    'recorded_at':event['available_at'],'review_id':event['review_id'],
                    'evidence_ids':[rid for rid in event.get('evidence_ids',[]) if rid in records],
                    'evidence_complete':all(rid in records for rid in event.get('evidence_ids',[])),
                    'classification_scope':'Saved analyst classification, not independent event truth'})
        grouped.append({'group_id':'event-context-'+_hash([kind,ids,event_ids]),'group_type':kind,
            'evidence_ids':ids,'event_ids':event_ids,
            'declared_event_ids':[eid for eid,members in declared_events.items() if set(members).intersection(ids)],
            'source_relations':attached,'recorded_updates':updates,
            'unverified':True,'scope': 'Explicit reviewed-event/source relationships only; all members retained. Semantic event status still needs source review.'})
    grouped.sort(key=lambda g:(g['group_type']!='reviewed_event',not any(r['predicate'] in ('contradicts','unresolved_conflict','corrects') for r in g['source_relations']),-len(g['evidence_ids']),g['group_id']))
    shown=grouped[:max_events];represented={rid for group in shown for rid in group['evidence_ids']}
    deferred=[rid for rid in records if rid not in represented]
    if deferred:
        gaps.append({'reason':'event_group_budget_exhausted','source_count':len(deferred)})
    prior=_previous(knowledge,previous_report,before,gaps)
    comparison={'status':'available' if prior else 'no_verified_previous_report',
        'semantic_fact_changes_confirmed':False,'scope':'Added/reused evidence in this bounded context, not a claim that old facts were removed or a viewpoint changed.'}
    if prior:
        old=prior.pop('source_ids');comparison.update(previous_report=prior,
            new_source_ids=[rid for rid in initial_ids if rid not in old],
            reused_source_ids=[rid for rid in initial_ids if rid in old],
            prior_sources_not_in_this_pack_count=len(old-set(records)))
    concise=[f'Report event context: {availability_mode} / {as_of}. {SCOPE}']
    for group in shown:
        concise.append(group['group_type']+': '+', '.join(group['evidence_ids'])+
            ('; saved states: '+', '.join(str(e['recorded_status']) for e in group['recorded_updates']) if group['recorded_updates'] else ''))
    if candidates:
        concise.append('Possible newer/related sources to inspect, not confirmed updates: '+', '.join(c['evidence_id'] for c in candidates))
    if gaps:
        concise.append('Context gaps: '+json.dumps(dict(Counter(g['reason'] for g in gaps)),sort_keys=True))
    prompt='\n'.join(concise)
    if len(prompt)>2800:
        prompt=prompt[:2700]+'\n[Short view truncated; full structured groups, source IDs and gaps remain available.]'
    return {'schema':VERSION,'query':query,'as_of':as_of,'availability_mode':availability_mode,
        'library_window':{'start':getattr(library,'window_start',None),'end':getattr(library,'window_end',None)},
        'source_cards':list(cards.values()),'event_groups':shown,'deferred_source_ids':deferred,
        'newer_related_candidates':candidates,'comparison':comparison,'gaps':gaps,
        'limits':{**stats,'max_sources':max_sources,'max_events':max_events,'max_related_queries':max_related_queries,
            'represented_sources':len(represented),'retained_sources':len(records)},
        'prompt_context':prompt,'scope':SCOPE}
