"""Recognize repeated evidence attempts within one research question."""

import hashlib
import json
import re
from threading import Lock


_SEARCH_TOOLS = {
    'recall_memory', 'recall_news', 'search_events', 'find_saved_news_url',
    'search_web', 'sdk:memory-news', 'sdk:memory-research', 'sdk:library-search',
}
_READ_TOOLS = {'read_evidence', 'sdk:library-evidence'}
_CAPTURE_TOOLS = {'fetch_webpage', 'request_public_api', 'sdk:fetch', 'sdk:public-api'}
_ATTEMPT_TOOLS = _SEARCH_TOOLS | _READ_TOOLS | _CAPTURE_TOOLS | {'task', 'execute'}
_VARIANT_FIELDS = {'query', 'description', 'facet_queries', 'related_queries', 'max_results', 'limit', 'full_text', 'command'}
_RECORD_ID = re.compile(r'(?:page|web|live|series|calc|context)-[a-f0-9]{12,64}')
_QUERY_SECRET = re.compile(
    r'\bbearer\s+|\b(?:api[ _-]?key|token|password|authorization|credential|secret)\s*[:=]|'
    r'\b(?:sk-|gh[pousr]_)[a-z0-9_-]{8,}', re.I)
_OPAQUE_TOKEN = re.compile(r'[A-Za-z0-9_+/=-]{20,}')
_URL = re.compile(r'(?i)(?:https?://|www\.)\S+|\S+\?[^\s=]*=\S*')


def missing_file_read(content, validated_path):
    """Recognize the pinned native file tool's exact missing-target error."""
    return isinstance(content, str) and content == f"Error: File '{validated_path}' not found"


def file_read_repair(target, kind, captures=(), saved_artifact=None, *, skipped=False):
    reason = 'directory_requires_ls' if kind == 'directory' else 'file_not_found'
    packet = {'status': 'no_progress' if skipped else 'error', 'reason': reason, 'tool': 'read_file'}
    if kind == 'directory':
        packet['next_action'] = 'Use ls with the exact virtual directory path, then select a file from the returned listing.'
    elif target.startswith('/memory/task-state/artifacts/'):
        packet['next_action'] = ('Use the exact path from the working-memory receipt or ls on its artifact directory. '
            'Changing limit or offset cannot recover a mistyped artifact name.')
        if saved_artifact:
            packet['saved_artifact_candidate'] = saved_artifact
    elif target.startswith('/data/live/'):
        packet['next_action'] = ('Use the exact data_file or record_id from the public API receipt. '
            'Call read_evidence with that record_id, pass its data_file to read_file, or use '
            'client.read_json(packet) in the terminal. Use ls(path="/data/live") to locate saved files. '
            'Changing limit or offset cannot recover a missing file.')
        if captures:
            packet['saved_api_candidates'] = captures
            packet['candidate_scope'] = 'Existing captures of the matching endpoint. Check their request window before reuse.'
    else:
        packet['next_action'] = ('Use ls with the virtual parent directory and read an exact returned file path. '
            'If an earlier tool is creating the file, retry after it has saved the file. Changing limit or offset will not locate it.')
    if skipped:
        packet['previous_attempts'] = 2
    return packet


class ToolChoiceProgress:
    """Detect a repeated blocked choice across separate model decisions."""

    def __init__(self):
        self._lock = Lock()
        self._stable = set()
        self._skipped = set()
        self._other = False
        self._previous_skip = None

    def observe(self, tool, scope=None, *, skipped=False, stable_failure=False):
        with self._lock:
            if skipped:
                self._skipped.add((tool, scope))
            elif stable_failure:
                self._stable.add((tool, scope))
            else:
                self._other = True

    def next_choice(self):
        """Consume the finished tool round; parallel results count as one choice."""
        with self._lock:
            keys = self._stable | self._skipped
            current = next(iter(keys)) if len(keys) == 1 and not self._other else None
            blocked = current is not None and current in self._skipped
            change = current if blocked and current == self._previous_skip else None
            if change or current != self._previous_skip:
                self._previous_skip = current if blocked and not change else None
            self._stable.clear()
            self._skipped.clear()
            self._other = False
            return change


def _digest(value):
    body = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def _attempt_label(tool, args):
    if tool in _READ_TOOLS:
        rid = args.get('record_id')
        return 'Open ' + rid if isinstance(rid, str) and _RECORD_ID.fullmatch(rid) else 'Open a saved source'
    if tool in _CAPTURE_TOOLS:
        return 'Read a public API response' if tool.endswith('public-api') or tool == 'request_public_api' else 'Capture a public source'
    if tool == 'find_saved_news_url':
        return 'Look up an exact saved URL'
    if tool == 'task':
        return 'Run an evidence-reader assignment'
    if tool == 'execute':
        return 'Run a workspace command'
    query = args.get('query')
    if isinstance(query, str):
        query = _URL.sub('[URL]', ' '.join(query.split()))
        if query and not _QUERY_SECRET.search(query):
            return 'Search for ' + _OPAQUE_TOKEN.sub('[redacted]', query)[:100]
    return 'Search for evidence'


def describe_attempt(tool_name, args):
    """Describe a supported evidence tool call without retaining raw arguments."""
    if tool_name not in _ATTEMPT_TOOLS or not isinstance(args, dict):
        return None
    if tool_name == 'task' and args.get('subagent_type') != 'evidence-reader':
        return None
    if tool_name == 'recall_memory' and args.get('kind') not in ('news', 'research', 'events'):
        return None
    inputs = {key: value for key, value in args.items() if key != 'question_id'}
    scope = {key: value for key, value in inputs.items() if key not in _VARIANT_FIELDS}
    try:
        return {'tool': tool_name, 'signature': _digest([tool_name, inputs]),
                'target': _digest([tool_name, scope]), 'label': _attempt_label(tool_name, inputs)}
    except (TypeError, ValueError):
        return None


def _packet(payload):
    if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict):
        if payload[0].get('type') == 'text':
            payload = payload[0].get('text')
    if isinstance(payload, str) and len(payload) <= 2_000_000:
        try:
            payload = json.loads(payload)
        except ValueError:
            return None
    return payload if isinstance(payload, dict) else None


def _record_marker(row, tool):
    if not isinstance(row, dict):
        return None
    rid = next((row[key] for key in ('record_id', 'evidence_id', 'collection_record_id', 'event_id', 'report_id', 'output_id')
                if isinstance(row.get(key), str) and row[key]), None)
    source = row.get('source') if isinstance(row.get('source'), dict) else {}
    url = row.get('url') or source.get('url')
    if tool == 'search_web':
        # Each provider response mints another web- ID even for the same excerpt.
        if not isinstance(url, str) or not url:
            return None
        return [url, row.get('title'), row.get('published_at'), row.get('excerpt')]
    if tool in _CAPTURE_TOOLS:
        # The capture record ID includes the wall clock. Compare actual response content.
        content = source.get('sha256') or row.get('sha256') or row.get('response_preview') or row.get('text')
        if content is None:
            return None
        return [url, row.get('dataset_id'), content]
    if not rid:
        return None
    spans = row.get('source_slices')
    if isinstance(spans, list) and spans:
        return [rid, sorted(_digest([span.get('start'), span.get('end'), span.get('sha256')])
                            for span in spans if isinstance(span, dict)),
                row.get('source_content_sha256') or row.get('full_text_sha256')]
    fields = ('passage_id', 'text', 'excerpt', 'values', 'units', 'records_preview', 'series_preview',
              'response_preview', 'memory_notices')
    return [rid, source.get('sha256'), {key: row[key] for key in fields if key in row}]


def _reader_markers(packet):
    findings = packet.get('findings')
    # The parent-facing envelope can omit findings and opened ranges. That is
    # insufficient evidence to conclude that the child made no progress.
    if (not isinstance(findings, list) or packet.get('omitted_findings')
            or packet.get('omitted_finding_count') or packet.get('details_in_artifact')):
        return None
    if not findings and packet.get('opened_source_count'):
        return None
    markers = []
    for finding in findings:
        if not isinstance(finding, dict) or not finding.get('finding') or not finding.get('citations'):
            return None
        quotes = [[quote[key] for key in ('evidence_id', 'start', 'end', 'sha256')]
                  for quote in finding['citations'] if isinstance(quote, dict)
                  and all(key in quote for key in ('evidence_id', 'start', 'end', 'sha256'))]
        if len(quotes) != len(finding['citations']):
            return None
        markers.append([finding['finding'], sorted(_digest(quote) for quote in quotes)])
    return markers


def _evidence_markers(packet, tool):
    if tool == 'task':
        return _reader_markers(packet)
    if tool in _SEARCH_TOOLS:
        rows = packet.get('results')
        if not isinstance(rows, list) or len(rows) > 128:
            return None
    else:
        rows = [packet.get('record', packet)]
    markers = [_record_marker(row, tool) for row in rows]
    return None if any(marker is None for marker in markers) else markers


def finish_attempt(attempt, payload, *, status='success'):
    """Fingerprint actual observations before the tool view is shortened."""
    if attempt is None:
        return None
    packet = _packet(payload)
    receipt = {**attempt, 'outcome': 'unknown', 'result_signature': None, 'evidence': []}
    if status in ('error', 'failed') or (packet is not None and packet.get('status') in ('error', 'failed')):
        error = ((packet.get('error') or packet.get('message') or packet.get('reason')) if packet else payload)
        if not isinstance(error, (str, dict)) or not error:
            return receipt
        if isinstance(error, str):
            error = ' '.join(error.split())
        try:
            return {**receipt, 'outcome': 'error', 'result_signature': _digest(error)}
        except (TypeError, ValueError):
            return receipt
    if packet is None or attempt['tool'] == 'execute':
        return receipt
    try:
        markers = _evidence_markers(packet, attempt['tool'])
        if markers is None:
            return receipt
        evidence = sorted({_digest(marker) for marker in markers})
        extra = {key: sorted(_digest(item) for item in packet[key])
                 for key in ('gaps', 'conflicts') if isinstance(packet.get(key), list)}
        signature = _digest([packet.get('status'), evidence, extra])
    except (TypeError, ValueError):
        return receipt
    return {**receipt, 'outcome': 'evidence' if evidence else 'empty',
            'result_signature': signature, 'evidence': evidence}


def progress_hint(prior_attempts):
    """Advise on repeated actual outcomes, including distinct query variants."""
    if len(prior_attempts) < 2:
        return None
    previous, latest = prior_attempts[-2:]
    if (latest.get('target') != previous.get('target')
            or latest.get('outcome') not in ('error', 'empty', 'evidence')
            or latest['outcome'] != previous.get('outcome') or not latest.get('result_signature')
            or latest['result_signature'] != previous.get('result_signature')):
        return None
    reasons = {'error': 'repeated_error', 'empty': 'repeated_empty_result', 'evidence': 'repeated_evidence'}
    return {'status': 'no_progress', 'reason': reasons[latest['outcome']], 'tool': latest['tool'],
            'labels': [row.get('label') or 'Evidence lookup' for row in (previous, latest)],
            'previous_attempts': 2,
            'next_action': 'Reuse the previous result, or choose a materially different source, query, or approach. Keep any unresolved evidence gap visible.'}


def repeat_decision(attempt, prior_attempts):
    """Skip a known retry after two equivalent observed outcomes for this question.

    The caller supplies actual attempts for the current question revision in
    execution order. A new argument set, different result, or unknown outcome
    remains eligible. Shell commands can depend on changed workspace state.
    """
    if attempt is None or attempt['tool'] == 'execute':
        return None
    hint = progress_hint(prior_attempts)
    if hint is None:
        return None
    previous, latest = prior_attempts[-2:]
    if (attempt['target'] != latest.get('target')
            or attempt['signature'] not in (previous.get('signature'), latest.get('signature'))):
        return None
    matched = latest if latest.get('signature') == attempt['signature'] else previous
    return {**hint, 'signature': attempt['signature'], 'label': matched.get('label') or 'Repeated evidence lookup'}
