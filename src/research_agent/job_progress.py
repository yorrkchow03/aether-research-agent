"""Project the private research event log into short, public-facing stage codes."""

import json


_TAIL_BYTES = 64 * 1024
_PUBLICATION_TOOLS = {'publish_report', 'submit_research_answer', 'review_report'}
_TOOL_STAGES = {
    'recall_memory': 'library',
    'recall_news': 'library',
    'recall_research': 'library',
    'sdk:collection-status': 'library',
    'sdk:local-market': 'library',
    'sdk:library-series': 'library',
    'sdk:library-search': 'library',
    'sdk:memory-news': 'library',
    'sdk:memory-research': 'library',
    'sdk:memory-cases': 'library',
    'search_web': 'web',
    'fetch_webpage': 'web',
    'request_public_api': 'web',
    'sdk:fetch': 'web',
    'read_evidence': 'evidence',
    'sdk:library-evidence': 'evidence',
    'execute': 'analysis',
    'register_calculation': 'analysis',
    'analyze_market': 'analysis',
}


def _stage(event, previous, conversation=False):
    kind, name, status = (event.get(key) for key in ('kind', 'name', 'status'))
    if kind in ('reader_started','reader_model_started','news_rerank_judgment'):
        return 'evidence'
    if kind=='plan_saved':
        return 'planning'
    if kind in ('harness_started','model_started') and not previous:
        return 'responding' if conversation else 'planning'
    if kind=='saved_context_loaded':
        return 'responding'
    if kind in ('source_validation_started', 'review_started'):
        return 'verification'
    if kind in ('report_pending', 'answer_pending', 'review_invalid'):
        return 'revision'
    if kind == 'review_finished' and status == 'needs_revision':
        return 'revision'
    if isinstance(name, str) and name in _PUBLICATION_TOOLS:
        if kind == 'tool_started':
            return 'verification'
        if kind == 'tool_failed' or (kind == 'tool_finished' and status == 'error'):
            return 'revision'
        if kind == 'tool_finished' and status == 'success' and name != 'review_report':
            return 'finalizing'
    if kind == 'tool_started' and isinstance(name, str):
        return _TOOL_STAGES.get(name)
    return None


def research_progress(job, events_path):
    """Read a bounded recent history and never return raw event fields."""
    stages = []
    try:
        with events_path.open('rb') as events:
            size = events.seek(0, 2)
            if size > _TAIL_BYTES:
                events.seek(size - _TAIL_BYTES - 1)
                if events.read(1) != b'\n':
                    events.readline(_TAIL_BYTES)
            else:
                events.seek(0)
            lines = events.read(size - events.tell()).splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        stage = _stage(event, stages,job.get('response_mode')=='conversation')
        if stage:
            if stage in stages:
                stages.remove(stage)
            stages.append(stage)
            stages = stages[-3:]
    return {
        'job_id': job['job_id'],
        'stage': stages[-1] if stages else ('responding' if job.get('response_mode')=='conversation' else 'working' if job.get('last_event') else 'starting'),
        'previous': stages[:-1],
    }
