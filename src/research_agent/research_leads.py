"""Private planning inputs from scoped user memory and the preceding reviewed report."""
import sqlite3

from langchain_core.tools import tool
from research_data.http import dumps

from .report_presentation import report_watch_items


def build_research_leads(*, knowledge, user_memory=None, user_id=None, prompt, as_of, session_id,
                         asset='ETH', task='report', overrides=(), query_topic=None, broad_report=False,
                         window_start=None, window_end=None, series_id=None, series_report_ids=(),
                         mode='observed'):
    """Build one planning snapshot from stores belonging to the caller's workspace."""
    snapshot = {
        'as_of': as_of, 'asset': asset, 'window_start': window_start, 'window_end': window_end,
        'user_leads': [], 'previous_report': None, 'questions_to_watch': [],
        'selection_basis': 'no_eligible_previous_report', 'limitations': [],
        'usage': 'Planning leads only. Verify developments from original sources in the requested window. '
                 'Keep private user notes out of public searches and report citations.',
    }
    if user_memory is not None and user_id:
        try:
            rows = user_memory.recall(user_id, prompt=prompt, as_of=as_of, asset=asset, task=task,
                                      overrides=overrides, query_topic=query_topic, broad_report=broad_report)
            for row in rows:
                fields = ('kind', 'topic', 'text', 'asset', 'task', 'observed_at', 'expires_at')
                snapshot['user_leads'].append({'lead_id': 'user:' + row['event_id'],
                                               **{key: row[key] for key in fields}})
        except (sqlite3.Error, OSError, ValueError):
            snapshot['limitations'].append('user_memory_unavailable')
    if knowledge is None:
        snapshot['selection_basis'] = 'report_history_unavailable'
        return snapshot
    try:
        selected = knowledge.previous_report_for_leads(asset=asset, session_id=session_id, as_of=as_of,
                       window_start=window_start, window_end=window_end, series_id=series_id,
                       series_report_ids=series_report_ids, mode=mode)
    except (sqlite3.Error, OSError, ValueError):
        snapshot['selection_basis'] = 'report_history_unavailable'
        return snapshot
    if selected is None:
        return snapshot

    report_id = selected['report_id']
    draft = selected['body']['report']
    watches = draft.get('questions_to_watch')
    texts = [item for item in watches if isinstance(item, str)] if isinstance(watches, list) else []
    for text in report_watch_items(texts):
        index = watches.index(text) + 1
        item = {'lead_id': f'report:{report_id}:watch:{index}', 'report_id': report_id, 'text': text[:600]}
        if len(text) > 600:
            item['text_truncated'] = True
        snapshot['questions_to_watch'].append(item)
    snapshot['selection_basis'] = selected['selection_basis']
    snapshot['previous_report'] = {
        'report_id': report_id, 'created_at': selected['created_at'], 'period': draft['scope'],
        'delivery_kind': draft.get('delivery_kind', 'full'),
        'watch_status': ('available' if snapshot['questions_to_watch'] else
                         'field_missing' if 'questions_to_watch' not in draft else 'empty'),
    }
    return snapshot


def research_leads_tool(snapshot):
    """Expose the original turn's frozen snapshot on first run and continuation."""
    result = dumps(snapshot)

    @tool
    def get_research_leads() -> str:
        """Read scoped user research preferences and the preceding reviewed report's watch questions for planning."""
        return result

    return get_research_leads
