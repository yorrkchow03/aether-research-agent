"""Deterministic navigation for large tool envelopes, with exact artifacts.

This module never summarizes source prose, infers missing values or changes the
SDK result. Selected JSON atoms keep their original values. Omitted material is
recoverable by JSON pointer from the complete redacted artifact.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from pydantic import BaseModel, ValidationError


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _pointer(parent, key):
    return parent + '/' + str(key).replace('~', '~0').replace('/', '~1')


def _fields(value, names):
    return {key: value[key] for key in names if key in value}


def publication_schema_errors(tool, arguments, *, status):
    """Host schema errors only; never infer validation from returned source text."""
    if status!='error' or getattr(tool,'name',None) not in ('publish_report','publish_saved_report'):
        return None
    schema=getattr(tool,'args_schema',None)
    if not isinstance(schema,type) or not issubclass(schema,BaseModel):return None
    try:schema.model_validate(arguments)
    except ValidationError as exc:
        return [{'path':'/'+ '/'.join(str(part).replace('~','~0').replace('/','~1') for part in error['loc']),
            'message':error['msg'],'type':error['type']}
            for error in exc.errors(include_input=False,include_url=False)]
    return None


def schema_error_view(tool, errors, receipt, *, max_chars=6000, root, workspace_root=None):
    """Lead with actual validation paths; retain the full native error on disk."""
    artifact=_fields(receipt,('artifact_id','sha256','bytes'))
    if workspace_root and receipt.get('path'):
        path=Path(receipt['path']).resolve()
        workspace=Path(workspace_root).resolve()
        if path.is_relative_to(workspace):
            artifact['path']='/'+path.relative_to(workspace).as_posix()
    view={'view':'schema-error-tool-view-v1','tool':tool,'status':'error',
        'error_type':'tool_argument_validation','errors':[], 'error_count':len(errors),
        'repair':'Correct the listed argument paths and retry. This invocation failed before publication and did not save a report. The full submitted kwargs/native error remain in artifact; do not reread the full draft unless a listed field needs it.',
        'artifact':artifact,'scope':'Host Pydantic argument validation, not source evidence or publication review.'}
    for error in errors:
        view['errors'].append(error)
        if len(_dump(view))>max_chars:
            view['errors'].pop();break
    view['omitted_error_count']=len(errors)-len(view['errors'])
    if len(_dump(view))>max_chars:raise ValueError('Schema error navigation exceeds view budget')
    return _dump(view)


def _passage_packet(obj):
    """Model-facing selected passages; optional diagnostics stay in the artifact."""
    if not isinstance(obj, dict) or obj.get('retrieval_unit') != 'passage':
        return None
    results = obj.get('results')
    if not isinstance(results, list) or len(results) > 64:
        return None
    names = ('passage_id', 'record_id', 'evidence_id', 'excerpt', 'source_slices',
             'input_excerpt_sha256', 'published_at', 'retrieved_at', 'ranking',
             'source_text_truncated', 'title', 'url', 'source_kind', 'relevance_hint')
    if any(not isinstance(row, dict) or not row.get('passage_id') or
           not isinstance(row.get('excerpt'), str) for row in results):
        return None
    packet = _fields(obj, ('retrieval_unit', 'as_of', 'mode', 'time_policy', 'source_ids', 'source_catalog',
                           'coverage', 'candidate_coverage', 'scope'))
    packet['results'] = [_fields(row, names) for row in results]
    for row in packet['results']:
        ranking=row.get('ranking')
        if isinstance(ranking,dict):
            row['ranking']=_fields(ranking,('baseline_rank','final_rank'))
        hint=row.get('relevance_hint')
        if isinstance(hint,dict):
            row['relevance_hint']=_fields(hint,('status','label','confidence','decision','next_read'))
            row['relevance_hint']['scope']='Navigation only; not source truth, priority or completeness.'
            if isinstance(hint.get('subquestion'),dict):
                row['relevance_hint']['subquestion']=_fields(hint['subquestion'],('choice','confidence'))
    catalog = obj.get('source_catalog')
    if isinstance(catalog, dict):
        packet['source_catalog'] = {rid:_fields(row, ('record_id','title','url','published_at',
            'retrieved_at','first_seen_at','source_kind','source_sha256','source_content_sha256'))
            for rid,row in catalog.items() if isinstance(row,dict)}
    selection = obj.get('selection') or {}
    packet['selection'] = _fields(selection, ('policy', 'candidate_count', 'passage_count',
        'selected_count', 'requested_limit', 'effective_limit', 'budget_chars',
        'content_characters', 'semantic_calls', 'accepted_judgments', 'abstained_judgments',
        'algorithm', 'reranking', 'reason', 'score_formula', 'jev_reranking', 'time_policy',
        'selected_source_count', 'remaining_ranked_passages', 'more_available', 'next_action',
        'limit','eligible_source_count','eligible_passage_count','source_candidate_count',
        'jev_irrelevant_selected_count','jev_selection_warning',
        'cost_estimate','cost_unit','candidate_pool_at_limit','semantic_scores_used'))
    reranking=selection.get('jev_reranking')
    if isinstance(reranking,dict):
        packet['selection']['jev_reranking']=_fields(reranking,('strategy','strategy_version','formula',
            'label_order','within_label_order','accepted_count','judgment_count',
            'abstained_or_unavailable_count','unjudged_positions_unchanged','scope'))
        if 'accepted_ranks' in reranking:
            packet['selection']['jev_reranking']['diagnostics_pointer']='/selection/jev_reranking/accepted_ranks'
    for key in ('conflicts', 'gaps'):
        rows = obj.get(key)
        if isinstance(rows, list):
            packet[key] = rows[:8]
            packet[key + '_count'] = len(rows)
    return packet


def recommended_view_budget(payload, default=6000):
    """Give selected RAG passages room without enlarging unrelated tool outputs."""
    try:
        obj = json.loads(payload) if isinstance(payload, str) else payload
        record=obj.get('record',{}) if isinstance(obj,dict) else {}
        if record.get('view_kind') in ('focused_saved_source','article_saved_source','expanded_saved_source'):
            # Preserve the exact bounded source view, including offsets and
            # omissions, instead of turning a 6005-character read into pointers.
            return min(30000,max(default,len(_dump(obj))+1000))
        packet = _passage_packet(obj)
        if packet is not None:
            return min(180000, max(default, len(_dump(packet)) + 2500))
    except (ValueError, TypeError):
        pass
    return default


def compact_tool_result(tool, text, obj, receipt, *, status, max_chars, root):
    """Fit the whole model view, including metadata, to a character budget.

    Each included source excerpt, numeric value/unit pair, conflict pair and
    review correction is atomic. A large atom becomes a pointer, never a sliced
    numeric value, quotation or alleged complete explanation. The artifact is
    mandatory; small budgets may show navigation alone.
    """
    artifact = _fields(receipt, ('artifact_id', 'path', 'sha256', 'bytes'))
    passages = _passage_packet(obj)
    if passages is not None:
        # Retrieval has already selected bounded, exact fragments. Never spend
        # their display room on long diagnostic histories before the evidence.
        passages.update(view='passage-tool-view-v1', tool=tool, status=status,
                        artifact=artifact, diagnostics_in_artifact=True,
                        all_selected_passages_present=True,
                        source_context_is_partial=True)
        if len(_dump(passages)) <= max_chars:
            return _dump(passages)
    view = {'view': 'progressive-tool-view-v3', 'tool': tool, 'status': status,
        'artifact': artifact, 'metadata': {}, 'navigation': [],
        'read_before_citing': True, 'preview_is_not_full_source': True,
        'original_characters': len(text), 'partial': True,
        'scope': 'Selective navigation. Omitted fields, conditions and full source text remain in the redacted artifact.'}
    if max_chars < 2000 or len(_dump(view)) > max_chars:
        # A long installation path must not defeat a small output budget. The
        # path remains usable relative to this task's working-memory root.
        artifact.pop('path', None)
        artifact['relative_path'] = str(Path(receipt['path']).relative_to(root))
    if len(_dump(view)) > max_chars:
        view.pop('scope')
    if len(_dump(view)) > max_chars:
        # Tool names are host-owned, but a large one need not consume the view.
        view['tool'] = str(tool)[:80]
    if len(_dump(view)) > max_chars:
        raise ValueError('Tool artifact navigation exceeds the requested view budget')
    if tool == 'execute' and Path(root).name == 'task-state' and Path(root).parent.name == 'memory':
        artifact['read_file_path'] = '/memory/task-state/' + Path(receipt['path']).relative_to(root).as_posix()
        view['next_action'] = 'Read a specific output section only if it is needed. Prefer read_file over printing this JSON envelope in execute again.'
        if len(_dump(view)) > max_chars:
            view.pop('next_action')
        if len(_dump(view)) > max_chars:
            artifact.pop('read_file_path')

    def put(key, value):
        old = view['metadata'].get(key)
        existed = key in view['metadata']
        view['metadata'][key] = value
        if len(_dump(view)) <= max_chars:
            return True
        if existed:
            view['metadata'][key] = old
        else:
            del view['metadata'][key]
        return False

    def add(item):
        view['navigation'].append(item)
        if len(_dump(view)) <= max_chars:
            return True
        view['navigation'].pop()
        return False

    def console(pointer, output):
        if not isinstance(output, str) or not output:
            return
        lines = output.splitlines(keepends=True)
        entry = {'pointer': pointer, 'line_count': len(lines), 'characters': len(output), 'lines': []}
        if not add(entry):
            return
        for index in dict.fromkeys([0, len(lines) - 1]):
            line = lines[index]
            item = {'line': index + 1, 'text': line}
            if pointer == '/output' and len(line) > 500:
                item.update(text=line[:500], partial=True)
            entry['lines'].append(item)
            if len(_dump(view)) > max_chars:
                entry['lines'].pop()

    def section(pointer, values, project=None, **context):
        if not isinstance(values, list) or not values:
            return
        entry = {'pointer': pointer, 'total': len(values), 'shown': 0, 'items': [], **context}
        if not add(entry):
            return
        for index, value in enumerate(values):
            item = project(value) if project else value
            if item is None:
                continue
            selected = {'pointer': _pointer(pointer, index), 'value': item}
            entry['items'].append(selected); entry['shown'] += 1
            if len(_dump(view)) > max_chars:
                entry['items'].pop(); entry['shown'] -= 1
                # Keep a navigable identity when the exact prose does not fit.
                identity = _fields(value, ('record_id', 'evidence_id', 'claim_id',
                    'report_id', 'output_id', 'status', 'relation', 'predicate',
                    'subject', 'object', 'evidence_ids')) if isinstance(value, dict) else {}
                if identity:
                    selected = {'pointer': _pointer(pointer, index), 'identity': identity,
                        'details_in_artifact': True}
                    entry['items'].append(selected); entry['shown'] += 1
                    if len(_dump(view)) > max_chars:
                        entry['items'].pop(); entry['shown'] -= 1
                break

    record = obj.get('record') if isinstance(obj.get('record'), dict) else obj
    for source in (obj, record):
        for key in ('status', 'exit_code', 'error_type', 'code', 'record_id',
                    'collection_record_id', 'dataset_id', 'data_file', 'report_path',
                    'answer_path', 'record_count', 'row_count', 'total',
                    'input_record_count', 'query_result_fully_materialized',
                    'cutoff', 'mode', 'published_at', 'retrieved_at', 'observed_at',
                    'period_end', 'available_at'):
            if key not in view['metadata'] and key in source and isinstance(source[key], (str, int, float, bool, type(None))):
                put(key, source[key])

    # Show actual failure/action text before optional diagnostic histories. Long
    # free-text errors stay exact in the artifact; their length and pointer are
    # still visible instead of pretending a clipped message is complete.
    for key in ('error', 'reason', 'repair', 'next_action'):
        if key in obj:
            if not put(key, obj[key]):
                add({'pointer': _pointer('', key), 'characters': len(_dump(obj[key])),
                    'details_in_artifact': True})
    for key in ('stderr', 'stdout'):
        console('/' + key, obj.get(key))
    if tool == 'execute':
        console('/output', obj.get('output'))
    for parent, container in (('', obj), ('/review', obj.get('review')),
                              ('/validation', obj.get('validation'))):
        if not isinstance(container, dict):
            continue
        if parent:
            put(parent[1:] + '_status', container.get('status', 'unknown'))
        verdicts = container.get('verdicts')
        if isinstance(verdicts, list):
            put('review_status_counts', dict(Counter(str(r.get('status', 'unknown'))
                for r in verdicts if isinstance(r, dict))))
            # Successful review quote bodies are on disk. Rejection conditions,
            # exact corrective instructions and original-source pointers lead.
            for index, verdict in enumerate(verdicts):
                if not isinstance(verdict, dict) or verdict.get('status') in ('supported', 'reasonable_inference'):
                    continue
                body = _fields(verdict, ('claim_id', 'status', 'reason', 'correction'))
                quotes = verdict.get('quotes')
                body['evidence_ids'] = (list(dict.fromkeys(q['record_id'] for q in quotes
                    if isinstance(q, dict) and isinstance(q.get('record_id'), str)))
                    if isinstance(quotes, list) else [])
                pointer = _pointer(_pointer(parent, 'verdicts'), index)
                if not add({'pointer': pointer, 'review_issue': body}):
                    add({'pointer': pointer, 'claim_id': verdict.get('claim_id'),
                        'status': verdict.get('status'), 'details_in_artifact': True})
        for key in ('errors', 'issues', 'gaps', 'conflicts'):
            section(_pointer(parent, key), container.get(key))

    # Corrections to recalled reports are essential even if their long summaries
    # are omitted. These remain attributed notices, not verified market truth.
    results = obj.get('results')
    if isinstance(results, list):
        put('result_count', len(results))
        for index, row in enumerate(results):
            if not isinstance(row, dict):
                continue
            notices = row.get('memory_notices')
            if isinstance(notices, list) and notices:
                identity = _fields(row, ('report_id', 'output_id', 'record_id'))
                section('/results/' + str(index) + '/memory_notices', notices, **identity)

    # Preserve explicit relationship endpoints before optional source prose.
    context = obj.get('event_context')
    if isinstance(context, dict):
        put('event_context_counts', {key: len(context[key]) for key in (
            'source_cards', 'event_groups', 'newer_related_candidates', 'gaps')
            if isinstance(context.get(key), list)})
        groups = context.get('event_groups')
        for group_index, group in enumerate(groups if isinstance(groups, list) else []):
            if not isinstance(group, dict):
                continue
            section('/event_context/event_groups/' + str(group_index) + '/source_relations',
                group.get('source_relations'), lambda row: _fields(row, (
                    'subject', 'predicate', 'object', 'known_at', 'basis')) if isinstance(row, dict) else None)
        section('/event_context/newer_related_candidates', context.get('newer_related_candidates'),
            lambda row: _fields(row, ('evidence_id', 'record_id', 'published_at',
                'newer_than_selected_source_ids', 'needs_original_review',
                'event_equivalence_confirmed')) if isinstance(row, dict) else None)
        section('/event_context/gaps', context.get('gaps'))
    if isinstance(record.get('source'), dict):
        put('source', _fields(record['source'], ('source_id', 'name', 'url', 'sha256', 'retrieved_at')))

    values, units = record.get('values'), record.get('units')
    if isinstance(values, dict):
        # Commit values and their supplied units together. A very large value is
        # left untouched on disk. No unit is inferred for a unitless source.
        selected_values, selected_units = {}, {}
        put('numeric_field_count', len(values))
        put('numeric_view_incomplete', True)
        for key, value in values.items():
            next_values = {**selected_values, key: value}
            next_units = {**selected_units, **({key: units[key]} if isinstance(units, dict) and key in units else {})}
            old = dict(view['metadata'])
            view['metadata'].update(values=next_values, units=next_units)
            if len(_dump(view)) > max_chars:
                view['metadata'] = old
                continue
            selected_values, selected_units = next_values, next_units
        if len(selected_values) == len(values):
            put('numeric_view_incomplete', False)
    elif isinstance(units, dict):
        put('units', units)
    for key in ('schema', 'columns', 'coverage', 'window'):
        if key in record:
            put(key, record[key])

    if isinstance(results, list):
        def source_card(row):
            if not isinstance(row, dict):
                return None
            return _fields(row, ('record_id', 'passage_id', 'ranking', 'report_id', 'output_id', 'answer_id',
                'title', 'url', 'published_at', 'retrieved_at', 'acquired_at',
                'source_kind', 'memory_status', 'next_report_ids',
                'original_source_evidence_ids', 'excerpt', 'source_slices',
                'input_excerpt_sha256', 'source_text_truncated'))
        section('/results', results, source_card)

    # Plain console output uses complete lines only; JSON is never chopped into
    # misleading half-values or partial source quotations. Files remain the
    # default route for very large single-line outputs and whole tables.
    if not obj:
        console('', text)
    return _dump(view)
