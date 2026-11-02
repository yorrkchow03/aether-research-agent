"""Deterministic task-memory reducer over host tool envelopes.

Source text, console output and derived notes are never promoted to verified
facts. The caller is the host tool tracer, not an LLM-controlled mutation API.
Full redacted outputs remain the authority for omitted details.
"""
from __future__ import annotations

import hashlib
import json
import re

VERSION = 'working-research-state-v2'
ID = re.compile(r'^(?:live|page|web|series|calc|context)-[a-f0-9]{12,64}$')
SOURCE_KEYS = ('record_id', 'collection_record_id')
PUBLICATION_TOOLS = frozenset({'publish_report', 'submit_research_answer'})
REVIEW_TOOLS = frozenset({'review_report', 'review_answer'})
SOURCE_TOOLS = frozenset({'read_evidence', 'register_calculation', 'request_public_api',
    'fetch_webpage', 'search_web', 'recall_memory', 'record_news_event'})
SOURCE_SDK = frozenset({'library-evidence', 'library-series', 'library-search',
    'fetch', 'public-api', 'archive-market', 'archive-chain', 'archive-events',
    'indicators', 'eth-rpc', 'protocol-activity', 'memory-news','memory-research','memory-cards'})


def serial(value):
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True,
        ensure_ascii=False, default=str)


def digest(value):
    return hashlib.sha256(serial(value).encode()).hexdigest()


def envelope(payload):
    """Parse an ordinary top-level JSON result, never prose/code substrings."""
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str) and len(payload) <= 4_000_000:
        try:
            value = json.loads(payload)
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError):
            pass
    return {}


def empty_state():
    return {'version': VERSION, 'sources': [], 'checks': [], 'unknowns': [],
        'conflicts': [], 'publications': [], 'derived_notes': [], 'observations': [],
        'scope': 'Derived host-result navigation. Opened sources and successful tools are not verified facts; model reviews remain attributed judgments.'}


def _ids(value):
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and ID.fullmatch(item)))


def _text(value, maximum=1000):
    if not isinstance(value, (str, int, float, bool)) or value is None:
        return ''
    text = str(value)
    return text if len(text) <= maximum else text[:maximum] + ' [Preview; inspect artifact.]'


def _item(kind, body, artifact_id, observation_id):
    selected = {**body, 'kind': kind, 'artifact_id': artifact_id,
        'observation_id': observation_id}
    selected['entry_id'] = kind + '-' + digest(selected)[:24]
    return selected


def _source(row, artifact_id, observation_id, status='candidate'):
    if not isinstance(row, dict):
        return None
    rid = row.get('record_id') or row.get('collection_record_id')
    if not isinstance(rid, str) or not ID.fullmatch(rid):
        return None
    card = {'evidence_id': rid, 'source_status': status,
        'artifact_id': artifact_id, 'observation_id': observation_id,
        'scope': 'Pointer and source metadata only; no semantic verification.'}
    for key in ('title', 'dataset_id', 'published_at', 'retrieved_at', 'available_at',
            'first_seen_at', 'observed_at', 'period_end', 'cutoff', 'mode', 'url'):
        if key in row and isinstance(row[key], (str, int, float, bool, type(None))):
            card[key] = _text(row[key], 600) if row[key] is not None else None
    omitted = []
    for key in ('values', 'units'):
        value = row.get(key)
        if not isinstance(value, dict):
            continue
        selected = {}
        for name, item in value.items():
            if (len(selected) < 30 and isinstance(name, str) and len(name) <= 160
                    and isinstance(item, (str, int, float, bool, type(None)))
                    and len(serial(item)) <= 250):
                selected[name] = item
            else:
                omitted.append(key + '.' + str(name)[:80])
        if selected:
            card[key] = selected
    if omitted:
        card['omitted_numeric_fields'] = omitted[:40]
        card['numeric_view_incomplete'] = True
    return card


def derive_observation(tool_name, payload, *, status='success', artifact_id,
        observation_id):
    """Interpret only the supported host-result structure for this tool role."""
    if not isinstance(tool_name, str) or not 1 <= len(tool_name) <= 160:
        raise ValueError('A bounded actual tool name is required')
    if status not in ('success', 'error'):
        raise ValueError('Tool observation status must be success or error')
    obj = envelope(payload)
    result = {'sources': [], 'checks': [], 'unknowns': [], 'conflicts': [],
        'publications': []}
    failed = status == 'error' or obj.get('status') in ('error', 'failed', 'unavailable')
    if failed:
        result['unknowns'].append(_item('tool_failure', {
            'tool': tool_name, 'status': 'unresolved',
            'error': _text(obj.get('error') or obj.get('reason') or payload, 1600),
            'meaning': 'Failure/unavailable data; not a zero value or disproved event.'}, artifact_id, observation_id))
        return result
    sdk_name = tool_name.removeprefix('sdk:') if tool_name.startswith('sdk:') else None
    source_role = tool_name in SOURCE_TOOLS or sdk_name in SOURCE_SDK
    if source_role:
        root = obj.get('record') if isinstance(obj.get('record'), dict) else obj
        root_status = 'registered_derived' if tool_name == 'register_calculation' else 'opened' if tool_name in ('read_evidence', 'fetch_webpage') or sdk_name == 'library-evidence' else 'candidate'
        card = _source(root, artifact_id, observation_id, root_status)
        if card:
            result['sources'].append(card)
        # Collections are single reproducible inputs, not thousands of automatically
        # verified constituent facts. Candidate results stay candidate pointers.
        if obj.get('collection_record_id') and not card:
            card = _source(obj, artifact_id, observation_id)
            if card:
                result['sources'].append(card)
        for value in (obj.get('results') or [])[:60] if isinstance(obj.get('results'), list) else []:
            card = _source(value, artifact_id, observation_id)
            if card:
                result['sources'].append(card)
            if (tool_name=='recall_memory' or sdk_name=='memory-research') and isinstance(value,dict) and (value.get('report_id') or value.get('output_id')):
                for notice in value.get('memory_notices',[]) if isinstance(value.get('memory_notices'),list) else []:
                    if not isinstance(notice,dict) or notice.get('assessment') not in ('needs_correction','needs_more_evidence'):continue
                    result['unknowns'].append(_item('previous_report_claim_requires_recheck',{
                        'status':'unresolved','report_id':value.get('report_id') or value.get('output_id'),'claim_id':_text(notice.get('claim_id'),80),
                        'assessment':notice['assessment'],'reviewer_kind':_text(notice.get('reviewer_kind'),80),
                        'correction':_text(notice.get('correction'),1400),
                        'evidence_ids':_ids(notice.get('review_evidence_ids',[]) or notice.get('source_evidence_ids',[]) or notice.get('evidence_ids',[])),
                        'replacement_report_id':notice.get('replacement_report_id'),
                        'scope':'Attributed review/update notice; reread originals. Not independently verified market truth.'},artifact_id,observation_id))
        for gap in obj.get('gaps', [])[:80] if isinstance(obj.get('gaps'), list) else []:
            if isinstance(gap, dict):
                body = {key: gap[key] for key in ('reason', 'record_id', 'error_type', 'status') if isinstance(gap.get(key), (str, int, bool))}
                body['status'] = 'unresolved'
                body['evidence_ids'] = _ids(gap.get('evidence_ids', []))
                result['unknowns'].append(_item('retrieval_gap', body, artifact_id, observation_id))
        for conflict in obj.get('conflicts', [])[:80] if isinstance(obj.get('conflicts'), list) else []:
            if not isinstance(conflict, dict):
                continue
            ids = _ids(conflict.get('evidence_ids', []))
            if len(ids) < 2:
                continue
            body = {'evidence_ids': ids, 'status': 'unresolved',
                'relation': _text(conflict.get('relation') or 'unresolved'),
                'basis': _text(conflict.get('basis') or 'tool_reported_relation'),
                'scope': 'Attributed candidate/reviewed relation; not an independently established contradiction.'}
            result['conflicts'].append(_item('source_conflict', body, artifact_id, observation_id))
        if isinstance(obj.get('event_context'), dict):
            # Related-update candidates are requests to inspect another original,
            # never proof of a changed real-world event status.
            context = obj['event_context']
            candidates = context.get('newer_related_candidates') or []
            if isinstance(candidates, list) and candidates:
                ids = [v.get('evidence_id') or v.get('record_id') for v in candidates if isinstance(v, dict)]
                result['unknowns'].append(_item('related_updates_to_check', {
                    'status': 'unresolved', 'evidence_ids': _ids(ids),
                    'candidate_count': len(candidates),
                    'scope': 'Read originals and verify event identity/update direction before asserting a revision.'}, artifact_id, observation_id))
    if tool_name in PUBLICATION_TOOLS or tool_name in REVIEW_TOOLS:
        validation = obj.get('validation')
        if isinstance(validation, dict):
            result['checks'].append(_item('numeric_source_contract', {
                'status': _text(validation.get('status') or 'unknown'),
                'numeric_claims_checked': validation.get('numeric_claims_checked'),
                'scope': 'Exact field/units/source contract only; not method or prose correctness.'}, artifact_id, observation_id))
        review = obj.get('review') if isinstance(obj.get('review'), dict) else obj if tool_name in REVIEW_TOOLS else {}
        if review:
            result['checks'].append(_item('source_review', {
                'review_id': _text(review.get('review_id')),
                'status': _text(review.get('status') or 'unknown'),
                'scope': 'Attributed source-support review; not independent source truth.'}, artifact_id, observation_id))
            verdicts = review.get('verdicts', [])
            for verdict in verdicts[:90] if isinstance(verdicts, list) else []:
                if not isinstance(verdict, dict):
                    continue
                check = {'claim_id': _text(verdict.get('claim_id'), 80),
                    'status': _text(verdict.get('status') or 'unknown', 80),
                    'reason': _text(verdict.get('reason'), 1000),
                    'evidence_ids': _ids([q.get('record_id') for q in verdict.get('quotes', []) if isinstance(q, dict)]) if isinstance(verdict.get('quotes'), list) else [],
                    'scope': 'Attributed review target; no conversion to verified market fact.'}
                entry = _item('review_target', check, artifact_id, observation_id)
                result['checks'].append(entry)
                if check['status'] in ('insufficient', 'contradicted'):
                    result['unknowns'].append(_item('review_issue', {
                        **check, 'review_status': check['status'], 'status': 'unresolved',
                        'correction': _text(verdict.get('correction'), 1000)}, artifact_id, observation_id))
        if tool_name in PUBLICATION_TOOLS and obj.get('status') == 'saved':
            path = obj.get('report_path') or obj.get('answer_path')
            result['publications'].append(_item('saved_publication', {
                'path': _text(path, 800), 'status': 'saved',
                'source_review_status': review.get('status', 'not_supplied'),
                'scope': 'Saved publication receipt; not proof that every claim is true.'}, artifact_id, observation_id))
    # Overflow is explicit and points to the entire redacted envelope.
    for key in ('gaps', 'conflicts', 'results'):
        value = obj.get(key)
        cap = 60 if key == 'results' else 80
        if source_role and isinstance(value, list) and len(value) > cap:
            result['unknowns'].append(_item('omitted_tool_items', {
                'field': key, 'count': len(value), 'preview_limit': cap,
                'status': 'unresolved', 'scope': 'Inspect the full task artifact before deciding omitted items are immaterial.'}, artifact_id, observation_id))
    return result


def merge_observation(state, changes, observation):
    """Append idempotent derived entries; never overwrite a financial claim."""
    current = json.loads(json.dumps(state or empty_state()))
    for key in ('sources', 'checks', 'unknowns', 'conflicts', 'publications', 'derived_notes', 'observations'):
        current.setdefault(key, [])
    for key in ('checks', 'unknowns', 'conflicts', 'publications'):
        seen = {row['entry_id'] for row in current[key]}
        for row in changes[key]:
            if row['entry_id'] not in seen:
                current[key].append(row);seen.add(row['entry_id'])
    # Keep actual opened source metadata over a candidate preview. Conflicting
    # metadata remains traceable in its original observation/artifact history.
    rank = {'candidate': 0, 'opened': 1, 'registered_derived': 1}
    positions = {row['evidence_id']: i for i, row in enumerate(current['sources'])}
    for card in changes['sources']:
        position = positions.get(card['evidence_id'])
        if position is None:
            positions[card['evidence_id']] = len(current['sources'])
            current['sources'].append(card)
        elif rank.get(card['source_status'], 0) >= rank.get(current['sources'][position]['source_status'], 0):
            current['sources'][position] = card
    if not any(row['observation_id'] == observation['observation_id'] for row in current['observations']):
        current['observations'].append(observation)
    current['version'] = VERSION
    return current
