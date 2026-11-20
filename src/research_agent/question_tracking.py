"""Connect saved research questions to plan items and individual tool calls."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat
from threading import Lock
from typing import Literal

from deepagents.backends.utils import validate_path
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

from .question_progress import describe_attempt, file_read_repair, missing_file_read, repeat_decision


_TAG = re.compile(r'^\s*\[(Q[A-Za-z0-9_-]{1,62})\]\s*')
_LIVE_ID = re.compile(r'live-[a-f0-9]{12,64}')
_TASK_ARTIFACT = re.compile(r'/memory/task-state/artifacts/([A-Za-z0-9_-]{1,128})/([a-f0-9]{20,96})\.txt')
_ARTIFACT_SHA = re.compile(r'[a-f0-9]{64}')
SOURCE_ACTIONS = {'recall_memory', 'find_saved_news_url', 'read_evidence', 'search_web', 'fetch_webpage',
                  'request_public_api', 'register_calculation', 'execute', 'task'}

QUESTION_INSTRUCTIONS = """For a multi-source investigation, make each concrete evidence question
a write_todos item prefixed with a stable ID such as [Q1] or [Q2]. Keep its ID across plan updates.
Pass question_id on focused source lookups (use 'shared' for a genuinely cross-question lookup); prefix each evidence-reader task description
with its [Q1] ID. A broad multi-topic discovery call may be shared and unassigned. Each question
retains its findings, citations, open gaps and previous attempts. Use these records to choose the next
lookup and to draft the corresponding report section. If a tool reports no_progress, reuse the result,
change source/method, or defer the question and disclose its remaining gap. Use update_research_question
to read one question or record a supported conclusion, resolve a gap, or defer it. Keep observed limits
of coverage in the report. Reader completion alone does not settle a question."""


def question_tag(text):
    match = _TAG.match(text) if isinstance(text, str) else None
    return match.group(1) if match else None


def active_question_scope(state):
    active = [question_tag(item.get('content')) for item in state.get('todos', [])
              if item.get('status') == 'in_progress']
    return active[0] if len(active) == 1 else None


def question_index(memory, job_id):
    questions = memory.state(job_id).get('research_state', {}).get('questions', [])
    return {row['question_id']: row for row in questions}


def register_plan_questions(memory, job_id, todos):
    known = question_index(memory, job_id)
    for item in todos:
        text = item.get('content', '')
        qid = question_tag(text)
        if qid and qid not in known:
            wording = _TAG.sub('', text, count=1).strip()
            memory.put_question(job_id, qid, wording[:800])
            known[qid] = True


def bind_question(memory, job_id, call, state, *, separate_questions=False):
    name, args = call['name'], call.get('args', {})
    if name not in SOURCE_ACTIONS or (name == 'task' and args.get('subagent_type') != 'evidence-reader'):
        return None
    if name == 'recall_memory' and args.get('kind') not in ('news', 'research', 'events'):
        return None
    description = args.get('description', '') if name == 'task' else ''
    explicit = args.get('question_id') or question_tag(description)
    if explicit == 'shared':
        return None
    if not explicit and name == 'recall_memory' and len(args.get('facet_queries') or []) > 1:
        return None
    todos = state.get('todos', [])
    tags = [qid for item in todos if (qid := question_tag(item.get('content')))]
    active = [question_tag(item.get('content')) for item in todos
              if item.get('status') == 'in_progress']
    qid = explicit or (active[0] if len(active) == 1 else None)
    if not qid and not description and not tags and not separate_questions:
        return None
    known = question_index(memory, job_id)
    if not explicit and name == 'read_evidence' and args.get('record_id'):
        # Parallel reads can concern different questions even with one active todo.
        # Prefer a checked original over a record retrieved among other candidates.
        rid = args['record_id']
        for fields in (('opened_evidence_ids', 'registered_evidence_ids', 'evidence_ids'),
                       ('candidate_evidence_ids',)):
            matches = [row for row in known.values() if any(rid in row.get(field, []) for field in fields)]
            if len(matches) == 1:
                return matches[0]
            if matches:
                break
    if not qid and tags:
        # The execute shell wrapper has no question_id field. Source SDK calls
        # inside the command can carry their own explicit binding.
        if name == 'execute':
            return None
        choices = ', '.join(tags)
        if description:
            raise ValueError('Prefix the reader description with its question ID ('+choices+').')
        raise ValueError('Select the question for this focused lookup using question_id ('+choices+
                         '), or question_id="shared" for a cross-topic lookup. No source call was made.')
    if qid in known:
        return known[qid]
    if separate_questions and description:
        raise ValueError('Open a concrete question before assigning this reader task.')
    if description:
        # Older plans can still delegate by topic. Derive a repeatable ID from the
        # exact assignment, and return it in the ordinary task memory view.
        if explicit and tags:
            raise ValueError('Unknown question ID. Use a saved plan ID or create the question first.')
        qid = qid or 'Q' + hashlib.sha256(description.encode()).hexdigest()[:12]
        memory.put_question(job_id, qid, _TAG.sub('', description, count=1).strip()[:800])
        return question_index(memory, job_id)[qid]
    if explicit:
        raise ValueError('Unknown research question ID. Use the ID of a saved plan item or create it with update_research_question.')
    return None


def guard_question_call(row, call):
    attempt = describe_attempt(call['name'], call.get('args', {}))
    decision = repeat_decision(attempt, row.get('attempts', []))
    if decision:
        return {**decision, 'question_id': row['question_id'], 'gaps': row.get('gaps', [])[:3]}
    return None


def question_result_payload(call, result):
    # Native task returns a Command containing a ToolMessage. Keep the text
    # exactly as returned by this invocation, without parsing a repr.
    update = getattr(result, 'update', None)
    messages = update.get('messages', []) if isinstance(update, dict) else []
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.tool_call_id == call['id']:
            return msg.content
    return getattr(result, 'content', None) or update or str(result)


def tool_selection_name(tool):
    if isinstance(tool, dict):
        function = tool.get('function')
        return tool.get('name') or (function.get('name') if isinstance(function, dict) else None)
    return tool if isinstance(tool, str) else getattr(tool, 'name', None)


class FileReadProgress:
    """Track native missing-file results per active question or unassigned job."""

    def __init__(self, backend, workspace, memory=None, job_id=None, previous_job_id=None):
        self.backend = backend
        self.workspace = Path(workspace).resolve()
        self.memory = memory
        self.job_ids = tuple(job for job in (job_id, previous_job_id) if job)
        self.errors = {}
        self.lock = Lock()

    def _target(self, call):
        path = call.get('args', {}).get('file_path')
        if not isinstance(path, str) or not path or len(path) > 4096:
            return None
        try:
            virtual = validate_path(path)
            resolved = self.backend._resolve_path(virtual)
            relative = '/' + resolved.relative_to(self.workspace).as_posix()
            try:
                kind = 'directory' if stat.S_ISDIR(resolved.stat().st_mode) else None
            except (FileNotFoundError, NotADirectoryError):
                kind = 'missing'
        except (AttributeError, OSError, ValueError, RuntimeError):
            return None
        return virtual, relative, kind

    def _saved_api_candidates(self, target):
        if not self.memory or not target.startswith('/data/live/api/'):
            return []
        slug = Path(target).stem.casefold()
        candidates = []
        for job in self.job_ids:
            try:
                observations = self.memory.state(job)['research_state']['observations']
            except (KeyError, ValueError, OSError):
                continue
            checked = 0
            for observation in reversed(observations):
                if (observation.get('tool_name') not in ('request_public_api', 'sdk:public-api')
                        or observation.get('status') != 'success'):
                    continue
                checked += 1
                if checked > 12:
                    break
                try:
                    receipt = self.memory.read_artifact(job, observation['artifact_id'])
                    if receipt['bytes'] > 250_000:
                        continue
                    packet = json.loads(receipt['text'])
                    rid, path = packet.get('record_id'), packet.get('data_file')
                    if not isinstance(rid, str) or not _LIVE_ID.fullmatch(rid) or path != '/data/live/' + rid + '.json':
                        continue
                    source = packet.get('source') or {}
                    name = re.sub(r'[^a-z0-9-]+', '_', str(source.get('name', '')).casefold()).strip('_')
                    if not name or not (slug == name or slug.startswith(name + '_')):
                        continue
                    resolved = self.backend._resolve_path(path)
                    if resolved != self.workspace / path.lstrip('/') or not resolved.is_file():
                        continue
                except (AttributeError, KeyError, TypeError, OSError, ValueError, RuntimeError):
                    continue
                candidate = {'record_id': rid, 'data_file': path}
                if candidate not in candidates:
                    candidates.append(candidate)
                if len(candidates) == 2:
                    return candidates
        return candidates

    def _saved_artifact_candidate(self, target):
        match = _TASK_ARTIFACT.fullmatch(target)
        if not match or not self.memory:
            return None
        job_id, typed_digest = match.groups()
        if job_id not in self.job_ids:
            return None
        rows = self.memory.artifacts(job_id, limit=500)
        for prefix_length in (24, 20):
            if len(typed_digest) < prefix_length or (prefix_length == 20 and len(rows) == 500):
                continue
            prefix = typed_digest[:prefix_length]
            matches = [row for row in rows if _ARTIFACT_SHA.fullmatch(row.get('sha256', ''))
                       and row['sha256'].startswith(prefix)]
            if len(matches) > 1:
                return None
            if not matches:
                continue
            receipt = matches[0]
            digest = receipt['sha256']
            file_path = f'/memory/task-state/artifacts/{job_id}/{digest}.txt'
            try:
                resolved = self.backend._resolve_path(file_path)
                if (resolved == Path(receipt['path']).resolve() == self.workspace / file_path.lstrip('/')
                        and resolved.is_file() and receipt['artifact_id'] == 'artifact-' + digest[:24]):
                    return {'artifact_id': receipt['artifact_id'], 'file_path': file_path}
            except (KeyError, OSError, ValueError, RuntimeError):
                pass
            return None
        return None

    def _repair(self, path, kind, scope, *, skipped=False):
        packet = file_read_repair(path, kind, self._saved_api_candidates(path),
            self._saved_artifact_candidate(path), skipped=skipped)
        if scope:
            packet['question_id'] = scope
        return packet

    def before(self, call, state):
        target = self._target(call)
        if target is None:
            return None
        _, path, kind = target
        scope = active_question_scope(state)
        key = (scope, path)
        with self.lock:
            if kind is None:
                self.errors.pop(key, None)
                return None
            recorded_kind, attempts = self.errors.get(key, (None, ()))
            skipped = kind == recorded_kind and len(attempts) >= 2
        return self._repair(path, kind, scope, skipped=True) if skipped else None

    def after(self, call, state, result):
        if not isinstance(result, ToolMessage) or result.status != 'error':
            return None
        target = self._target(call)
        if target is None:
            return None
        virtual, path, kind = target
        if not kind or not missing_file_read(result.content, virtual):
            return None
        scope = active_question_scope(state)
        key = (scope, path)
        with self.lock:
            recorded_kind, attempts = self.errors.get(key, (None, ()))
            if kind != recorded_kind:
                attempts = ()
            if call['id'] not in attempts:
                attempts = (*attempts, call['id'])[-2:]
            self.errors[key] = (kind, attempts)
        return self._repair(path, kind, scope)


def question_tool(memory, job_id, *, periodic_workflow=None):
    @tool
    def update_research_question(question_id: str, question: str | None = None,
            status: Literal['open','needs_evidence','ready','deferred'] | None = None,
            conclusion: str | None = None, evidence_ids: list[str] | None = None,
            gaps: list[str] | None = None, candidate_ids: list[str] | None = None,
            user_excerpt: str | None = None, lead_id: str | None = None,
            merge_into: str | None = None) -> str:
        """Read or update a concrete question. Period reports open questions after discovery using actual candidate_ids, a verbatim user_excerpt, or a lead_id. Ready needs a conclusion, checked evidence IDs and no remaining gaps. Use merge_into to consolidate an overlapping question into an existing one."""
        fields = (question, status, conclusion, evidence_ids, gaps)
        row = question_index(memory, job_id).get(question_id)
        if merge_into:
            if any(value is not None for value in (*fields, candidate_ids, user_excerpt, lead_id)):
                raise ValueError('Merge a question in a separate call.')
            row = memory.merge_questions(job_id, question_id, merge_into)
            question_id = merge_into
        elif any(value is not None for value in fields):
            origin = None
            if row is None and periodic_workflow:
                origin = periodic_workflow.origin_for(candidate_ids, user_excerpt, lead_id)
            if row and row.get('merged_into'):
                raise ValueError('This question is merged. Update ' + row['merged_into'] + ' instead.')
            row = memory.put_question(job_id, question_id, question, status=status,
                                conclusion=conclusion, evidence_ids=evidence_ids, gaps=gaps, origin=origin)
            if origin and not row['duplicate']:
                periodic_workflow.emit('periodic_question_opened', {
                    'question_id': question_id, 'question': row['question'], 'origin': row['origin'],
                })
        if row is None:
            raise ValueError('Unknown question; provide the research question text to create it.')
        # Full source text and full reader artifacts remain addressable via their IDs.
        view = {k:row.get(k) for k in ('question_id','question','status','conclusion',
                                       'evidence_ids','gaps','conflicts','origin','merged_into','merged_questions')}
        findings = row.get('reader_findings', [])
        view['reader_findings'] = findings[-6:]
        view['recent_attempts'] = [{k:v for k,v in attempt.items() if k in ('label','tool','outcome')}
                                   for attempt in row.get('attempts', [])[-3:]]
        if row.get('progress_hint'):
            view['progress_hint'] = row['progress_hint']
        view['total_findings'] = len(findings)
        return json.dumps(view, ensure_ascii=False)
    return update_research_question
