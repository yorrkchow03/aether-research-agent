"""Checkpoints and tool boundaries for discovery-first period reports."""
from __future__ import annotations

from pathlib import Path
import re
from threading import RLock

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from .planning import ResearchPlanningMiddleware
from .question_tracking import question_tag
from .research_state import ID, envelope
from .research_policy import recent_report_window
from research_data.http import dumps
from research_data.normalize import iso, timestamp


PERIOD_STAGES = ('Discover sources', 'Verify selected evidence', 'Synthesize and draft', 'Review and publish')
PHASES = ('discover', 'verify', 'draft', 'review')
PUBLICATION = {'publish_report', 'publish_saved_report'}
PERIOD_PLAN_INSTRUCTIONS = (
    'Use the research skill for period-report methods. The four items in write_todos track phases. '
    'Work on individual research questions through update_research_question.'
)
PERIOD_TODO_DESCRIPTION = (
    'Save the four research phases with their current status. Follow the research skill. '
    'Create concrete research questions using update_research_question after discovery.'
)
_ASSET = {'ETH': r'\bETH\b|\bEthereum\b|以太坊', 'BTC': r'\bBTC\b|\bBitcoin\b|比特币'}


def period_assets(prompt):
    assets = tuple(asset for asset, pattern in _ASSET.items() if re.search(pattern, prompt, re.I))
    return assets or ('ETH',)


def explicit_archive_scope(prompt):
    return bool(re.search(
        r'仅(?:用|使用|依据)(?:本地|已保存|归档)|只(?:用|查|使用)(?:本地|已保存|归档)|不要联网|不联网|'
        r'\b(?:offline|archive[- ]only|without (?:web|internet) access|no (?:web|internet) access|'
        r'only (?:use )?(?:the )?(?:local|saved|archived) (?:data|sources|materials))\b',
        prompt, re.I,
    ))


def periodic_report_window(prompt, created_at):
    if window := recent_report_window(prompt, created_at):
        return window
    # Accept one explicit pair of UTC dates or clocked bounds. Preserve
    # existing interpretation for prompts with several possible ranges.
    matches = re.findall(
        r'(?<![0-9-])20[0-9]{2}-[0-9]{2}-[0-9]{2}'
        r'(?:T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2}))?(?![0-9T])',
        prompt,
    )
    if len(matches) == 2:
        try:
            start, end = (timestamp(value if 'T' in value else value + 'T00:00:00Z') for value in matches)
            if start < end <= timestamp(created_at):
                return iso(start), iso(end)
        except ValueError:
            pass
    return None


def requests_periodic_report(prompt):
    return not re.search(
        r'补写|补成|草稿|限证据稿|上一份报告|上次报告|续写|继续未完成|'
        r'\b(?:continue (?:the unfinished|preparing)|draft|rewrite|revise|saved (?:brief|report))\b', prompt, re.I,
    )


class PeriodicWorkflow(AgentMiddleware):
    """Persist real phase receipts in the same job store as the question ledger."""

    def __init__(self, working, job_id, scope, prompt, emit, *, leads, continued_from=None,
                 archive_only=False, scout_available=True):
        self.working = working
        self.job_id = job_id
        self.scope = scope
        self.prompt = prompt
        self.emit = emit
        self.archive_only = archive_only
        self.scout_available = scout_available
        self.lock = RLock()
        self.data = {'phase': 'prepare', 'scope': scope}
        if continued_from:
            try:
                previous = working.state(continued_from).get('periodic_workflow')
            except KeyError:
                previous = None
            if previous and previous['scope'] == scope:
                self.data = dict(previous)
                self.data['continued_from'] = continued_from
        self.leads = self.data.get('leads') or leads
        self._turn = None
        self._persist()
        if 'continued_from' in self.data:
            self.emit('periodic_workflow_resumed', {
                'continued_from': self.data['continued_from'], 'phase': self.phase(),
                'lead_snapshot_reused': 'leads' in self.data,
            })

    def _persist(self):
        self.working.update(self.job_id, periodic_workflow=self.data)

    def phase(self):
        with self.lock:
            return self.data['phase']

    def recover_draft(self):
        if self.phase() == 'draft':
            self._advance('review', 'recovered_draft')

    def has_verified_evidence(self):
        state = self.working.state(self.job_id)['research_state']
        return any(
            row.get('origin') and not row.get('merged_into') and any(
                rid.startswith(('page-', 'live-', 'series-', 'calc-'))
                for rid in (*row['opened_evidence_ids'], *row['registered_evidence_ids'])
            ) for row in state.get('questions', [])
        )

    def is_resumed(self):
        return bool(self.data.get('continued_from'))

    def reserve_delivery(self):
        if self.phase() == 'verify' and self.has_verified_evidence():
            self._advance('draft', 'verified_delivery_reserve')

    def pending_originals(self):
        """Return real saved originals already attached as candidates, without their text."""
        rows = self.working.state(self.job_id)['research_state'].get('questions', [])
        return [
            (row['question_id'], rid)
            for row in rows if not row.get('merged_into')
            for rid in row['candidate_evidence_ids']
            if rid.startswith(('page-', 'live-', 'series-')) and rid not in row['opened_evidence_ids']
        ][:6]

    def verification_handoff(self):
        """Suggest the next verification work from recorded public candidates."""
        discovery = self.data.get('discovery', {})
        candidates = [row['record_id'] for row in discovery.get('candidates', [])]
        rows = [row for row in self.working.state(self.job_id)['research_state'].get('questions', [])
                if row.get('origin') and not row.get('merged_into')]
        if not rows and candidates:
            return {'action': 'open_question', 'candidate_ids': candidates[:6],
                    'candidate_map': (discovery.get('next_read') or {}).get('file_path')}
        for row in rows:
            if row['status'] not in ('open', 'needs_evidence') or any(
                obs.get('tool') == 'task' for obs in row['observations']
            ):
                continue
            pending = [rid for rid in row['candidate_evidence_ids']
                       if rid.startswith('page-') and rid not in row['opened_evidence_ids']]
            if len(pending) >= 3:
                return {'action': 'delegate', 'question_id': row['question_id'],
                        'question': row['question'][:180], 'candidate_ids': pending[:4]}
        return None

    def parent_news_view(self, payload, receipt, workspace):
        """Send exact metadata to the lead while retaining the complete passage packet."""
        packet = envelope(payload)
        if not packet or packet.get('retrieval_unit') != 'passage':
            return None
        rows = packet.get('results')
        if not isinstance(rows, list):
            return None
        artifact = {key: receipt[key] for key in ('artifact_id', 'sha256', 'bytes')}
        path = Path(receipt['path']).resolve()
        if workspace and path.is_relative_to(Path(workspace).resolve()):
            artifact.update(path='/' + path.relative_to(Path(workspace).resolve()).as_posix(),
                            json_pointer='/results')
        sources = {}
        for row in rows:
            if isinstance(row, dict) and isinstance(rid := row.get('record_id'), str) and ID.fullmatch(rid):
                sources.setdefault(rid, row)
        if not sources:
            return None
        gaps = packet.get('gaps')
        view = {'view': 'periodic-candidate-sources-v1', 'status': 'success', 'artifact': artifact,
                'retrieved_passage_count': len(rows), 'candidate_source_count': len(sources),
                'results': [], 'omitted_candidate_count': len(sources),
                'gap_count': len(gaps) if isinstance(gaps, list) else 0,
                'scope': 'Candidate metadata only. Open an original by record_id for a single focused '
                         'check, or pass linked originals to the evidence reader for a question. '
                         'The artifact retains the selected passages, their order and retrieval gaps.'}
        budget = 4000
        for rid, row in sources.items():
            card = {'record_id': rid, 'title': str(row.get('title') or rid)[:240]}
            card.update({key: row[key] for key in ('published_at', 'source_kind')
                         if isinstance(row.get(key), str)})
            view['results'].append(card)
            view['omitted_candidate_count'] -= 1
            if len(dumps(view)) > budget:
                view['results'].pop()
                view['omitted_candidate_count'] += 1
                break
        return dumps(view)

    def _advance(self, phase, trigger):
        with self.lock:
            previous = self.phase()
            if previous == phase:
                return
            self.data['phase'] = phase
            index = PHASES.index(phase) if phase in PHASES else len(PHASES)
            self.data['stages'] = [
                {'content': label, 'status': 'completed' if i < index else 'in_progress' if i == index else 'pending'}
                for i, label in enumerate(PERIOD_STAGES)
            ]
            self._persist()
            self.emit('periodic_stage_changed', {
                'from': previous, 'phase': phase, 'trigger': trigger, 'stages': self.data['stages'],
            })

    def record_discovery(self, packet):
        with self.lock:
            if self.phase() != 'discover' or 'discovery' in self.data:
                return
            self.data['discovery'] = packet
            self.emit('periodic_discovery_recorded', {
                'scout_id': packet.get('scout_id'), 'status': packet['status'],
                'candidate_ids': [row['record_id'] for row in packet['candidates']],
                'channels': packet['channels'], 'gaps': packet['gaps'],
                'artifact': packet.get('artifact'),
            })
            self._advance('verify', 'scout_receipt')

    def origin_for(self, candidate_ids, user_excerpt, lead_id):
        ids = list(dict.fromkeys(candidate_ids or []))
        if len(ids) > 24 or any(not isinstance(rid, str) or not ID.fullmatch(rid) for rid in ids):
            raise ValueError('Provide at most 24 canonical candidate IDs.')
        with self.lock:
            discovery = self.data.get('discovery')
        if not discovery:
            raise ValueError('Wait for the actual research-scout receipt before opening a question.')
        known = {row['record_id'] for row in discovery['candidates']}
        known.update(row['evidence_id'] for row in
                     self.working.state(self.job_id)['research_state'].get('sources', []))
        if not set(ids) <= known:
            raise ValueError('Candidate IDs must have appeared in an actual source receipt for this report.')
        eligible = {row['lead_id'] for category in ('user_leads', 'questions_to_watch')
                    for row in self.leads.get(category, [])}
        if ids:
            origin = {'kind': 'current_retrieval', 'candidate_ids': ids}
            if lead_id in eligible:
                origin['lead_id'] = lead_id
            return origin
        if lead_id in eligible:
            return {'kind': 'prior_lead', 'lead_id': lead_id}
        if user_excerpt:
            if (not isinstance(user_excerpt, str) or not 4 <= len(user_excerpt.strip()) <= 250
                    or user_excerpt not in self.prompt
                    or re.fullmatch(r'[\s\W]*(?:请|给我|帮我|写|生成|并|保存|一份|一篇|以太坊|'
                                    r'比特币|最近|过去|近|一|七|周|天|月|的|研究|报告|月报|周报|'
                                    r'ETH|BTC|Ethereum|Bitcoin|\d|[\s\W])+', user_excerpt, re.I)):
                raise ValueError('Quote the specific topic the user requested. Asset and period alone do not name a topic.')
            return {'kind': 'user_request', 'excerpt': user_excerpt}
        if lead_id:
            raise ValueError('Choose a lead_id from get_research_leads for this report.')
        raise ValueError('Open a question with candidate_ids, a verbatim user_excerpt, or a saved lead_id.')

    def _start_turn(self):
        # A parallel tool call cannot race a sibling's phase transition.
        with self.lock:
            self._turn = (self.phase(), 'leads' in self.data, self.has_verified_evidence())

    def wrap_model_call(self, request, handler):
        self._start_turn()
        return handler(request)

    async def awrap_model_call(self, request, handler):
        self._start_turn()
        return await handler(request)

    def _allow(self, call, phase, leads_read, verified):
        name, args = call['name'], call.get('args', {})
        if name == 'get_research_leads' or ResearchPlanningMiddleware._preparation(call):
            return None
        if name == 'write_todos':
            if not leads_read:
                return 'Call get_research_leads first, even if history is empty.'
            todos = args.get('todos')
            if not isinstance(todos, list) or not all(isinstance(row, dict) for row in todos):
                return 'Save the four phases from the research skill.'
            if tuple(row.get('content') for row in todos) != PERIOD_STAGES:
                return 'Use only the four exact phase names in the research skill. Add concrete questions separately.'
            active = [index for index, row in enumerate(todos) if row.get('status') == 'in_progress']
            if len(active) != 1:
                return 'Mark exactly one phase in progress.'
            index = active[0]
            if any(row.get('status') != ('completed' if i < index else 'in_progress' if i == index else 'pending')
                   for i, row in enumerate(todos)):
                return 'Mark earlier phases completed and later phases pending.'
            targets = {'prepare': {0}, 'discover': {0}, 'verify': {1, 2}, 'draft': {1, 2}, 'review': {1, 3}}
            if index not in targets.get(phase, set()) or (index == 2 and not verified):
                return 'Follow the reported phase. Drafting needs an original source opened or a registered calculation on a research question.'
            return None
        if phase == 'prepare':
            return 'Read the research skill and get_research_leads, then save the four-phase plan.'
        if name == 'task' and args.get('subagent_type') == 'research-scout':
            return None if phase == 'discover' else 'The broad scout already returned. Use its candidates to plan targeted work.'
        if phase == 'discover':
            if self.scout_available:
                return 'Run the research-scout task first and wait for the discovery receipt.'
            return 'Discovery is unavailable. Use the recorded coverage gap.'
        if phase == 'draft' and name in {'execute', 'recall_memory', 'search_web', 'fetch_webpage',
                                         'request_public_api', 'register_calculation', 'task'}:
            return 'Save the draft using verified sources, or update the plan back to Verify selected evidence for an essential gap.'
        if self.archive_only and name in {'search_web', 'fetch_webpage', 'request_public_api', 'execute'}:
            return 'This report uses local archived evidence only.'
        if name == 'task' and args.get('subagent_type') == 'evidence-reader':
            qid = question_tag(args.get('description'))
            if not qid:
                return 'Prefix the reader assignment with an existing question ID, such as [Q1].'
            try:
                self.working.get_question(self.job_id, qid)
            except KeyError:
                return 'Open that question from a real receipt with update_research_question first.'
        if name == 'save_report_draft' and (phase not in ('draft', 'review') or not verified):
            return 'Verify an original or calculation on a question, then update the plan to Synthesize and draft.'
        if name in PUBLICATION and phase != 'review':
            return 'Save a substantive draft before submitting it for review.'
        return None

    @staticmethod
    def _succeeded(result, call_id):
        if isinstance(result, ToolMessage):
            return result.status != 'error'
        update = getattr(result, 'update', None)
        return isinstance(update, dict) and any(
            isinstance(msg, ToolMessage) and msg.tool_call_id == call_id and msg.status != 'error'
            for msg in update.get('messages', [])
        )

    def _reject_call(self, call):
        with self.lock:
            phase, leads_read, verified = self._turn or (
                self.phase(), 'leads' in self.data, self.has_verified_evidence())
        error = self._allow(call, phase, leads_read, verified)
        if error:
            self.emit('periodic_tool_blocked', {'phase': phase, 'tool': call['name'],
                                              'call_id': call['id'], 'reason': error})
            return ToolMessage(content=error, name=call['name'], tool_call_id=call['id'], status='error')

    def wrap_tool_call(self, request, handler):
        call = request.tool_call
        blocked = self._reject_call(call)
        return blocked if blocked is not None else self._finish_call(call, handler(request))

    async def awrap_tool_call(self, request, handler):
        call = request.tool_call
        blocked = self._reject_call(call)
        return blocked if blocked is not None else self._finish_call(call, await handler(request))

    def _finish_call(self, call, result):
        if not self._succeeded(result, call['id']):
            return result
        name = call['name']
        if name == 'get_research_leads':
            with self.lock:
                if 'leads' not in self.data:
                    self.data['leads'] = self.leads
                    self._persist()
                    previous = self.leads.get('previous_report') or {}
                    self.emit('periodic_leads_read', {
                        'user_lead_count': len(self.leads.get('user_leads', [])),
                        'watch_count': len(self.leads.get('questions_to_watch', [])),
                        'previous_report_id': previous.get('report_id'),
                    })
        elif name == 'write_todos':
            index = next(i for i, row in enumerate(call['args']['todos']) if row['status'] == 'in_progress')
            self._advance(PHASES[index], 'plan_saved')
            if index == 0 and not self.scout_available:
                self.record_discovery({'status': 'incomplete', 'candidates': [], 'gaps': ['The scout is disabled.'],
                    'channels': {'archive': {'status': 'unavailable'}, 'web': {'status': 'disabled'}}})
        elif name == 'save_report_draft':
            self._advance('review', 'draft_saved')
        elif name in PUBLICATION:
            self._advance('complete', 'checked_publication')
        return result
