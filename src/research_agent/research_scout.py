"""Isolated, evidence-first discovery for a host-defined periodic research scope."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from threading import Lock
from uuid import uuid4
from urllib.parse import urlsplit

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from .knowledge import canonical_url
from .live import atomic
from .report_presentation import ReportScope
from .research_state import ID
from research_data.normalize import timestamp


_NAVIGATION_SCOPE = 'Source candidates for navigation. Inspect the saved original before citing or making a claim.'
_PREVIEW_CHARS = 8000


def _publication_time(value, start, end):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Date-only provider metadata is sufficient for complete UTC days.
        if len(value) != 10 or any((start.hour, start.minute, start.second, start.microsecond,
                                    end.hour, end.minute, end.second, end.microsecond)):
            return None
        value += 'T00:00:00Z'
    return timestamp(value)


def _source_candidates(rows, channel, scope):
    start, end = timestamp(scope['start']), timestamp(scope['end'])
    candidates = []
    counts = {'returned_rows': len(rows), 'outside_window': 0, 'unknown_publication_time': 0,
              'invalid_rows': 0}
    prefix = 'page-' if channel == 'archive' else 'web-'
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            counts['invalid_rows'] += 1
            continue
        rid, url = row.get('record_id'), row.get('url')
        if (not isinstance(rid, str) or not ID.fullmatch(rid) or not rid.startswith(prefix)
                or not isinstance(url, str) or len(url) > 2048):
            counts['invalid_rows'] += 1
            continue
        if rid in seen:
            continue
        seen.add(rid)
        try:
            # Canonicalize only for duplicate detection. Preserve exact URLs and
            # forum post fragments in the evidence navigation candidates.
            identity = canonical_url(url), urlsplit(url).fragment
        except ValueError:
            counts['invalid_rows'] += 1
            continue
        published = row.get('published_at')
        clock = _publication_time(published, start, end)
        if clock is None:
            counts['unknown_publication_time'] += 1
            if channel == 'archive':
                continue
        elif not start <= clock < end:
            counts['outside_window'] += 1
            continue
        title = row.get('title')
        candidate = {'record_id': rid, 'url': url,
            'title': title.strip()[:240] if isinstance(title, str) and title.strip() else rid,
            'published_at': published if clock is not None else None,
            'channel': channel, 'source_status': 'candidate',
            'publication_window': 'in_window' if clock is not None else 'unknown'}
        if isinstance(row.get('retrieved_at'), str):
            candidate['retrieved_at'] = row['retrieved_at']
        candidates.append((identity, candidate))
    return candidates, counts


def _combine(archive, web):
    """Prefer archive originals without dropping distinct saved source versions."""
    archive_urls = {key for key, _ in archive}
    web_by_url = {}
    for key, row in web:
        if key not in archive_urls:
            web_by_url.setdefault(key, row)
    groups = ([row for _, row in archive], list(web_by_url.values()))
    return [group[index] for index in range(max(map(len, groups))) for group in groups if index < len(group)]


def _preview(result):
    preview = {**result, 'candidates': []}
    for candidate in result['candidates']:
        preview['candidates'].append(candidate)
        if len(json.dumps(preview, ensure_ascii=False)) > _PREVIEW_CHARS - 100:
            preview['candidates'].pop()
            break
    preview['omitted_candidate_count'] = result['candidate_count'] - len(preview['candidates'])
    return preview


def _archive_catalog_receipt(packet):
    """Keep selected originals first and save only navigation metadata."""
    catalog = {row['record_id']: row for row in packet['scout_candidate_catalog']}
    selected = {record_id: catalog[record_id] for record_id in packet['source_ids']}
    return {'results': list(selected.values()),
            'eligible_candidates': list({**selected, **catalog}.values()),
            'candidate_coverage': packet['candidate_coverage'],
            'gaps': packet['gaps'],
            'selection': {key: packet['selection'][key] for key in (
                'eligible_source_count', 'selected_source_count', 'time_policy')}}


def build_research_scout(service, workspace, emit=lambda *_: None, cancel=None, redact=str,
                         *, scope, allow_web=None, on_complete=None, include_candidate_catalog=False):
    """Build a native isolated task that performs one shared scoped discovery.

    Delegated model prose cannot change the public query, source permissions or
    UTC scope. Subsequent invocations reuse the first real tool receipts.
    """
    scope = ReportScope.model_validate(scope).model_dump()
    workspace = Path(workspace)
    lock = Lock()
    completed = None

    def observe(call_id, tool, payload, status):
        working, job_id = getattr(service, 'working_memory', None), getattr(service, 'job_id', None)
        if working is not None and job_id is not None:
            try:
                working.observe_tool(job_id, tool, payload, status=status, tool_call_id=call_id)
            except Exception as exc:
                emit('working_memory_error', {'stage': 'scout_source', 'error_type': type(exc).__name__})

    def perform(scout_id, channel, tool, arguments, available):
        if cancel is not None and cancel.is_set():
            return [], {'status': 'cancelled'}, None
        if not available:
            return [], {'status': 'unavailable'}, None
        call_id = f'scout-{scout_id}-{channel}'
        event = {'scout_id': scout_id, 'channel': channel, 'name': tool, 'call_id': call_id}
        emit('tool_started', {**event, 'arguments_preview': json.dumps(arguments, ensure_ascii=False)})
        packet = None
        try:
            if channel == 'archive' and include_candidate_catalog:
                packet = _archive_catalog_receipt(service.scout_news_pack(**arguments))
            else:
                operation = service.news_pack if channel == 'archive' else service.online_search
                packet = operation(**arguments)
            if (not isinstance(packet, dict) or packet.get('status') in ('error', 'failed')
                    or not isinstance(packet.get('results'), list)):
                raise ValueError('Source tool did not return a valid results packet')
            rows, counts = _source_candidates(packet.get('eligible_candidates', packet['results']), channel, scope)
        except Exception as exc:
            payload = {'status': 'error', 'error': redact(str(exc))[:800], 'error_type': type(exc).__name__}
            if isinstance(packet, dict):
                payload['returned_packet'] = packet
            observe(call_id, tool, payload, 'error')
            emit('tool_failed', {**event, 'error': payload['error'], 'error_type': payload['error_type']})
            return [], {'status': 'error', 'error_type': payload['error_type']}, payload
        observe(call_id, tool, packet, 'success')
        emit('tool_finished', {**event, 'status': 'success'})
        # A malformed non-empty response does not establish an empty search.
        state = 'success' if rows else 'incomplete' if counts['invalid_rows'] else 'no_results'
        return rows, {'status': state, 'candidate_count': len(rows), **counts}, packet

    def discover():
        scout_id = uuid4().hex
        artifact = f'/memory/scouts/{scout_id}.json'
        candidate_map = f'/memory/scouts/{scout_id}-candidates.json'
        emit('scout_started', {'scout_id': scout_id, 'scope': scope})
        asset = scope['assets'][0] if len(scope['assets']) == 1 else 'ALL'
        archive_args = {'query': '', 'asset': asset, 'original_only': True, 'material_only': True,
                        'start': scope['start'], 'end': scope['end']}
        entries = {'ETH': 'Ethereum (ETH)', 'BTC': 'Bitcoin (BTC)'}
        subjects = ' and '.join(entries[asset] for asset in scope['assets'])
        web_args = {'query': (f'{subjects} material ecosystem developments published from {scope["start"]} '
                              f'inclusive to {scope["end"]} exclusive UTC. Find material developments across the '
                              'date range and their original published sources.'),
                    'time_range': 'Any', 'max_results': 8}
        archive, archive_status, archive_packet = perform(scout_id, 'archive', 'recall_memory', archive_args,
            bool(getattr(service, 'knowledge', None)))
        web, web_status, web_packet = perform(scout_id, 'web', 'search_web', web_args,
            allow_web is not False and bool(getattr(service, 'allow_live', False))
            and getattr(service, 'mode', None) != 'strict' and bool(getattr(service, 'web_search', None)))
        channels = {'archive': archive_status, 'web': web_status}
        states = {item['status'] for item in channels.values()}
        gaps = [f'{name} channel: {report["status"]}.' for name, report in channels.items()
                if report['status'] != 'success']
        if any(item.get('unknown_publication_time') for item in channels.values()):
            gaps.append('Some results lack a usable publication time; verify their date before treating them as in-window.')
        candidates = _combine(archive, web)
        if candidates:
            status = 'partial' if states & {'error', 'cancelled', 'incomplete'} else 'completed'
        else:
            status = 'no_evidence' if 'no_results' in states and states <= {'no_results', 'unavailable'} else 'incomplete'
        result = {'scout_id': scout_id, 'status': status, 'scope': scope, 'channels': channels,
                  'candidates': candidates, 'candidate_count': len(candidates), 'gaps': gaps,
                  'artifact': artifact, 'next_read': {'file_path': candidate_map, 'json_pointer': '/result/candidates'},
                  'candidate_scope': _NAVIGATION_SCOPE}
        receipt = {'result': result, 'tool_receipts': {'archive': {'arguments': archive_args, 'result': archive_packet},
                   'web': {'arguments': web_args, 'result': web_packet}}}
        receipt = json.loads(redact(json.dumps(receipt, ensure_ascii=False)))
        atomic(workspace / artifact.lstrip('/'), receipt)
        result = receipt['result']
        atomic(workspace / candidate_map.lstrip('/'), {'result': result})
        if on_complete is not None:
            try:
                on_complete(result)
            except Exception as exc:
                emit('scout_receipt_error', {'scout_id': scout_id, 'error_type': type(exc).__name__})
        emit('scout_finished', {'scout_id': scout_id, 'status': status, 'channels': channels,
                                'candidate_count': len(candidates), 'artifact': artifact, 'model_calls': 0})
        return _preview(result)

    def run(_state):
        nonlocal completed
        with lock:
            if completed is None:
                completed = discover()
            else:
                emit('scout_reused', {'scout_id': completed['scout_id'], 'artifact': completed['artifact']})
        return {'messages': [AIMessage(content=json.dumps(completed, ensure_ascii=False))]}

    return {'name': 'research-scout', 'mode': 'isolated',
            'description': 'Build a broad source map for the fixed report scope using saved public news and available public search. Returns candidates, channel statuses, and gaps for focused follow-up.',
            'runnable': RunnableLambda(run)}
