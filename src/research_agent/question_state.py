"""Bounded question state derived from explicit assessments and host tool receipts."""
from __future__ import annotations

import hashlib
import json
import re

from .research_state import ID, derive_observation, envelope


QUESTION_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
QUESTION_STATUSES = ('open', 'needs_evidence', 'ready', 'deferred')


def validate_question_id(value):
    if not isinstance(value, str) or not QUESTION_ID.fullmatch(value):
        raise ValueError('Use a question ID with 1–64 letters, digits, underscores or hyphens.')
    return value


def _bounded_text(value, field, length, *, allow_empty=False):
    if not isinstance(value, str) or len(value) > length or (not allow_empty and not value.strip()):
        raise ValueError(f'Provide bounded {field} text of at most {length} characters.')
    return value.strip()


def question_patch(question, status, conclusion, evidence_ids, gaps):
    patch = {}
    if question is not None:
        patch['question'] = _bounded_text(question, 'question', 800)
    if status is not None:
        if status not in QUESTION_STATUSES:
            raise ValueError('Question status must be open, needs_evidence, ready or deferred.')
        patch['status'] = status
    if conclusion is not None:
        patch['conclusion'] = _bounded_text(conclusion, 'conclusion', 1600, allow_empty=True)
    if evidence_ids is not None:
        if (not isinstance(evidence_ids, list) or len(evidence_ids) > 24
                or any(not isinstance(value, str) or not ID.fullmatch(value) for value in evidence_ids)):
            raise ValueError('Use at most 24 canonical evidence IDs for a question assessment.')
        patch['evidence_ids'] = list(dict.fromkeys(evidence_ids))
    if gaps is not None:
        if not isinstance(gaps, list) or len(gaps) > 24:
            raise ValueError('Provide at most 24 remaining gaps as text entries.')
        patch['gaps'] = list(dict.fromkeys(_bounded_text(value, 'gap', 600) for value in gaps))
    return patch


def assess_question(question_id, previous, patch):
    if previous is None:
        if 'question' not in patch:
            raise ValueError('A new question requires its question text.')
        previous = {'question_id': question_id, 'question': patch['question'], 'status': 'open',
            'conclusion': '', 'evidence_ids': [], 'gaps': [], 'attempts': [], 'observations': [],
            'candidate_evidence_ids': [], 'opened_evidence_ids': [], 'registered_evidence_ids': [],
            'reader_findings': [], 'conflicts': [], 'progress_hint': None,
            'scope': 'Task-local assessment and navigation. Publication checks still apply.'}
    if 'question' in patch and ' '.join(patch['question'].split()) != ' '.join(previous['question'].split()):
        raise ValueError('The question ID already names a different question. Use a new ID for new scope.')
    updated = {**previous, **{key: value for key, value in patch.items() if key != 'question'}}
    if patch.get('gaps') and 'status' not in patch and updated['status'] in ('open', 'ready'):
        updated['status'] = 'needs_evidence'
    if updated['status'] == 'ready' and (not updated['conclusion'] or not updated['evidence_ids'] or updated['gaps']):
        raise ValueError('A ready question requires a sourced conclusion and no remaining gaps.')
    return updated


def question_tool_packet(payload):
    """Unwrap plain host JSON and single text blocks, never data embedded in prose."""
    if isinstance(payload, str) and len(payload) <= 4_000_000:
        try:
            payload = json.loads(payload)
        except ValueError:
            return {}
    if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict):
        block = payload[0]
        if block.get('type') == 'text':
            return envelope(block.get('text'))
    return payload if isinstance(payload, dict) else {}


def _rows(packet, key):
    value = packet.get(key)
    return value if isinstance(value, list) else []


def _reader_file(root, reader_id, name):
    directory = root.parent / 'readers' / reader_id
    try:
        path = directory / (name + '.json')
        if path.resolve() != path or not path.is_file() or path.stat().st_size > 250_000:
            return {}
        packet = envelope(path.read_text())
    except (OSError, ValueError):
        return {}
    return packet if packet.get('reader_id') == reader_id else {}


def _reader_projection(payload, root, artifact_id, observation_id):
    from .review_source_navigation import _reader_id_from_task

    reader_id = _reader_id_from_task(payload)
    if not reader_id:
        gaps = [value[:600] for value in _rows(payload, 'gaps')[:6]
            if isinstance(value, str) and value.strip()]
        return {}, [], [], gaps, payload
    navigation = _reader_file(root, reader_id, 'navigation')
    saved_result = _reader_file(root, reader_id, 'validated-result')
    result = saved_result or payload
    if result.get('reader_id') != reader_id:
        return {'reader_id': reader_id}, [], [], [], payload
    base = '/memory/readers/' + reader_id + '/'
    result_path = base + 'validated-result.json' if saved_result else None
    reader = {'reader_id': reader_id, 'reader_status': result.get('status', 'unknown')}
    if result_path:
        reader['reader_artifact'] = result_path
    if navigation:
        reader['reader_navigation'] = base + 'navigation.json'
    opened = navigation.get('opened_sources')
    sources = [rid for rid in opened if ID.fullmatch(rid)] if isinstance(opened, dict) else []
    findings = []
    for index, finding in enumerate(_rows(result, 'findings')[:6]):
        if not isinstance(finding, dict) or not isinstance(finding.get('finding'), str):
            continue
        witness_quotes = _rows(finding, 'citations')
        citations = []
        for citation in witness_quotes[:3]:
            if not isinstance(citation, dict):
                continue
            rid, quote = citation.get('evidence_id'), citation.get('quote')
            start, end = citation.get('start'), citation.get('end')
            if (not isinstance(rid, str) or not ID.fullmatch(rid) or not isinstance(quote, str)
                    or not 0 < len(quote) <= 500 or type(start) is not int or type(end) is not int
                    or start < 0 or end - start != len(quote)
                    or citation.get('sha256') != hashlib.sha256(quote.encode()).hexdigest()):
                continue
            citations.append({key: citation[key] for key in ('evidence_id', 'quote', 'start', 'end', 'sha256')})
        if not citations or len(citations) != len(witness_quotes):
            continue
        entry = {'finding': finding['finding'][:600], 'citations': citations,
            'reader_id': reader_id, 'artifact_id': artifact_id, 'observation_id': observation_id,
            'json_pointer': '/findings/' + str(index),
            'scope': 'Reader-derived finding with source-quote provenance. Verify semantic support.'}
        if result_path:
            entry['file_path'] = result_path
        findings.append(entry)
        sources.extend(citation['evidence_id'] for citation in citations)
    gaps = [value[:600] for value in _rows(result, 'gaps')[:6] if isinstance(value, str) and value.strip()]
    conflicts = [{'description': value[:560], 'origin': 'reader', 'reader_id': reader_id,
        'artifact_id': artifact_id, 'observation_id': observation_id,
        **({'file_path': result_path} if result_path else {}),
        'json_pointer': '/conflicts/' + str(index)}
        for index, value in enumerate(_rows(result, 'conflicts')[:4]) if isinstance(value, str) and value.strip()]
    reader['conflicts'] = conflicts
    return reader, sources, findings, gaps, result


def project_question_result(tool, payload, *, status, artifact_id, observation_id, root, reader_task=False):
    packet = question_tool_packet(payload)
    source_tool = 'recall_memory' if tool == 'find_saved_news_url' else tool
    changes = derive_observation(source_tool, packet or payload, status=status,
        artifact_id=artifact_id, observation_id=observation_id)
    sources = {'candidate_evidence_ids': [], 'opened_evidence_ids': [], 'registered_evidence_ids': []}
    roles = {'candidate': 'candidate_evidence_ids', 'opened': 'opened_evidence_ids',
        'registered_derived': 'registered_evidence_ids'}
    for source in changes['sources']:
        sources[roles[source['source_status']]].append(source['evidence_id'])
    gaps = []
    for unknown in changes['unknowns']:
        description = unknown.get('reason') or unknown.get('error') or unknown.get('correction')
        gaps.append(str(description)[:600] if description else
            'Inspect unresolved ' + unknown['kind'].replace('_', ' ') + ' in the saved tool result.')
    reader = {}; findings = []
    if reader_task and status == 'success':
        reader, opened, findings, reader_gaps, packet = _reader_projection(packet, root, artifact_id, observation_id)
        sources['opened_evidence_ids'].extend(opened)
        gaps.extend(reader_gaps)
        if reader.get('reader_status') in ('incomplete', 'no_evidence') and not reader_gaps:
            gaps.append('The delegated reader did not produce usable findings. Inspect its saved navigation.')
    conflicts = [*changes['conflicts'], *reader.pop('conflicts', [])]
    for conflict in conflicts:
        description = conflict.get('description')
        if description:
            gaps.append('Check reader-reported conflict: ' + description)
        elif conflict.get('evidence_ids'):
            gaps.append('Compare related or conflicting sources ' + ', '.join(conflict['evidence_ids']) + '.')
    return {'sources': sources, 'reader_findings': findings, 'reader': reader, 'gaps': gaps,
        'conflicts': conflicts, 'attempt_payload': packet or payload}


def merge_question_result(question, receipt, projection, attempt):
    from .question_progress import progress_hint

    result = {**question, 'observations': [*question['observations'], receipt]}
    for field, refs in projection['sources'].items():
        result[field] = list(dict.fromkeys([*question[field], *refs]))
    for field in ('reader_findings', 'conflicts'):
        result[field] = [*question[field], *projection[field]]
    result['gaps'] = list(dict.fromkeys([*question['gaps'], *projection['gaps']]))
    if result['gaps'] != question['gaps'] and question['status'] != 'deferred':
        result['status'] = 'needs_evidence'
    if attempt is not None:
        result['attempts'] = [*question['attempts'], attempt]
        result['progress_hint'] = progress_hint(result['attempts'])
    return result


def question_context(questions, budget):
    """Keep IDs and states together with short navigation to current work."""
    from research_data.http import dumps

    selected = []
    for question in questions:
        row = {key: question[key] for key in ('question_id', 'status')}
        row['question'] = question['question'][:130]
        if not question['conclusion'] and question['reader_findings']:
            finding = question['reader_findings'][-1]
            row['reader_finding'] = finding['finding'][:130]
            row['reader_evidence_id'] = finding['citations'][0]['evidence_id']
        if question['gaps']:
            row['open_gap_count'] = len(question['gaps'])
            row['latest_gap'] = question['gaps'][-1][:130]
        if question['conclusion']:
            row['conclusion'] = question['conclusion'][:130]
        if question['attempts']:
            row['attempt_count'] = len(question['attempts'])
            previous = question['attempts'][-1]
            row['latest_attempt'] = previous['outcome']
            label = previous.get('label')
            if label:
                row['latest_query'] = label[:100]
        hint = question.get('progress_hint')
        if hint and question['status'] in ('open', 'needs_evidence'):
            row['progress_hint'] = {'status': hint['status'], 'reason': hint['reason']}
        sources = question['evidence_ids'] or question['opened_evidence_ids'] or question['registered_evidence_ids']
        if sources:
            row['evidence_ids'] = sources[:2]
        elif question['candidate_evidence_ids']:
            row['candidate_evidence_ids'] = question['candidate_evidence_ids'][:2]
        if len(dumps([*selected, row])) > budget:
            essentials = ('question_id', 'status', 'reader_finding', 'reader_evidence_id')
            row = {key: row[key] for key in essentials if key in row}
        if len(dumps([*selected, row])) > budget:
            row = {key: row[key] for key in ('question_id', 'status')}
            if len(dumps([*selected, row])) > budget:
                break
        selected.append(row)
    return {'items': selected, 'total': len(questions),
        'details': 'Full question attempts, source links and reader results are in the state revision.'}
