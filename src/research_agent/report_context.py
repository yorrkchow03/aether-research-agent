"""Saved-draft delivery guidance and completion after checked publication."""
import re

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.tools import tool
from research_data.http import dumps, utcnow


DELIVERY_TOOLS = {'save_report_draft', 'publish_report', 'publish_saved_report', 'read_evidence', 'read_file', 'update_research_question'}
CALCULATION_TOOLS = {'analyze_market', 'register_calculation'}
PUBLICATION_TOOLS = {'publish_report', 'publish_saved_report'}
_VALIDATION_ITEM = re.compile(r'validation failed at (/(?:observations|key_changes)/[0-9]+)\.')
DRAFT_TOOLS = PUBLICATION_TOOLS | {'save_report_draft'}


def report_checkpoint_tool(service):
    """Retain a model-authored draft without spending a semantic review attempt."""
    from .chat_reply import requests_report
    from .report_recovery import LivePublishArguments
    from .reports import ReportDraft, validate_report

    @tool(args_schema=LivePublishArguments)
    def save_report_draft(draft: ReportDraft, user_request: str = '') -> str:
        """Save an unpublished report from verified evidence in the requested scope. Use statement and evidence_ids for facts or figures found only in source text, exact saved metric/value/unit for structured figures, and disclose material gaps."""
        service._check_delivery_cancelled()
        if not service.report_allowed:
            raise ValueError('This task cannot save report drafts')
        # Check the original request held by the runtime. Copying part of it
        # into a model argument is redundant for this unpublished checkpoint.
        if service.report_request_prompt is not None and not requests_report(service.report_request_prompt):
            raise ValueError('A chat report checkpoint requires an explicit report request')
        if draft.delivery_kind != 'full' or not (draft.observations or draft.key_changes):
            raise ValueError('Save a substantive report draft with sourced observations or events')
        if (service.require_report_scope and draft.scope is None) or not service._report_window_matches(draft.scope):
            raise ValueError('The draft scope must match the requested UTC dates')
        # The reserve should survive routine format and evidence-reference errors.
        # Semantic review is still required at publication.
        validate_report(draft, service.lookup, as_of=utcnow() if service.allow_live else service.cutoff)
        receipt = service._save_report_draft(ReportDraft.model_validate(draft.model_dump()))
        service.last_report_checkpoint_id = receipt
        return dumps({'status': 'draft_saved', 'draft_id': receipt, 'published': False})

    return save_report_draft


class CompleteAfterPublication(AgentMiddleware):
    """Finish only after publish passed every check and durably saved a report."""
    def __init__(self,service,emit):self.service,self.emit=service,emit

    def wrap_model_call(self,request,handler):
        if self.service.published_reports and self.service.last_report:
            self.emit('host_publication_completion',{'published_reports':list(self.service.published_reports),
                'provider_requests':0,'scope':'Host receipt after verified model-authored publication.'})
            return ModelResponse(result=[AIMessage(content='The source-checked report has been saved.',
                additional_kwargs={'aether_host_completion':True})])
        return handler(request)


def delivery_view(messages):
    """Legacy helper; keep every source view and tool pair in the request.

    Discovery/repair phases do not establish that earlier qualifications can be
    removed. Context pressure is handled by native token-aware summarization.
    """
    return list(messages),False,0


class ReportContextMiddleware(AgentMiddleware):
    """Save an early model draft, then leave room for research and publication."""
    def __init__(self, emit=lambda *_:None, service=None, *, max_model_calls=None, plan_job_ids=(), periodic_workflow=None):
        self.emit, self.service = emit, service
        self.periodic_workflow = periodic_workflow
        self.calls = 0
        self.limit = min(32, max_model_calls) if max_model_calls is not None else None
        self.delivery_start = max(1, min(18, self.limit - 5)) if self.limit is not None else None
        self.plan_job_ids = plan_job_ids
        self.phase = None
        self.checkpoint_attempts = 0
        self.delivery_started = False

    def wrap_model_call(self,request,handler):
        self.calls += 1
        draft_id=getattr(self.service,'latest_report_draft_id',None)
        if self.limit is not None and self.calls > self.limit:
            raise RuntimeError('The report authoring budget ended; proceed with the retained draft and saved evidence.')
        if self.periodic_workflow is not None:
            return handler(self._periodic_request(request, draft_id))
        guidance = (
            'Start with a dated source lookup or a required metric for the current report. '
            'Task-memory state and shell artifacts are navigation, and can contain outputs created by this same run. '
            'Do not inventory task-memory directories or print one tool artifact after another. '
            'If a tool entry is needed, use its bounded view or read one specific file section. '
            'For an underlying fact, reopen the canonical source ID with read_evidence instead.'
        )
        if draft_id:
            guidance += (
                f' Saved draft: /drafts/{draft_id}.json. Use publish_saved_report with field edits '
                'for concrete validation or review feedback. Empty edits reuse the exact draft. '
                'Reuse validated quotes and calculations. Reopen an underlying source only '
                'if needed to resolve a substantive qualification. All publication checks still apply.'
            )
        overrides = {}
        if self.limit is not None:
            recent_tools = {}
            for message in reversed(request.messages):
                if message.type == 'human':
                    break
                if message.type == 'tool' and message.name not in recent_tools:
                    recent_tools[message.name] = message.status
            plan_missing = bool(self.plan_job_ids and request.state.get('research_plan_job_id') not in self.plan_job_ids)
            self.delivery_started = self.delivery_started or bool(PUBLICATION_TOOLS & recent_tools.keys())
            checkpoint_due = (
                not draft_id and not plan_missing and self.checkpoint_attempts < 2
                and self.calls < self.delivery_start
                and (self.phase == 'checkpoint'
                     or any(recent_tools.get(name) == 'success' for name in CALCULATION_TOOLS)
                     or (self.calls >= 3 and recent_tools.get('read_evidence') == 'success')
                     or (self.calls >= max(2, min(7, self.delivery_start - 2))
                         and getattr(self.service, 'used_records', ())))
            )
            if self.delivery_started or self.calls >= self.delivery_start:
                self.delivery_started = True
                remaining = self.limit - self.calls + 1
                submission_only = bool(draft_id and remaining <= 3)
                phase = 'submission' if submission_only else 'delivery'
                allowed = PUBLICATION_TOOLS if submission_only else DELIVERY_TOOLS
                if plan_missing:
                    allowed = allowed | {'write_todos'}
                guidance += (
                    f' End discovery now. {remaining} author responses remain, including this one. '
                    'Submit the source-backed report from saved work now. If you have substantive additions to an '
                    'early checkpoint, submit the enriched report; otherwise publish the saved draft. '
                    'After a validation or review error, edit the flagged fields directly. Preserve supported facts. '
                    'Remove an unsupported optional clause or use the precise confirmed source attribution. '
                    'Do not replace that clause with a different factual assertion during the repair. '
                    'For a disputed date, check the cited event date separately from publication and capture clocks. '
                    'If the original supports only a broader time interval, use that interval. '
                    'Omit a nonessential document card if its exact excerpt is unavailable.'
                )
                if submission_only:
                    guidance += (' These responses are reserved for submission and focused edits. '
                                 'Use the evidence and feedback already returned. Omit any unresolved optional item.')
                else:
                    guidance += ' Reopen only the source needed to resolve a specific error.'
            elif checkpoint_due:
                phase = 'checkpoint'
                self.checkpoint_attempts += 1
                allowed = {'save_report_draft'}
                guidance += (
                    ' Save a compact unpublished draft now in one save_report_draft call. Use one to three '
                    'supported observations and any already supported distinct events. Copy the requested scope '
                    'and actual source IDs. Put facts and figures found only in an article in statement and '
                    'evidence_ids. Use numeric fields only for exact saved metric keys, raw values and units. Omit optional '
                    'document cards, charts without a checked series, and advice for this checkpoint. State material gaps.'
                )
            else:
                phase = 'discovery'
                allowed = None
                guidance += (
                    ' Start with one date-scoped overview and the central saved measurements. For a broad '
                    'weekly brief, look for distinct material events across the requested dates and themes. '
                    'Use facet queries for a multi-question overview. For a single missing event or qualification, '
                    'omit facet_queries in recall_memory. If you already have its full record_id, use read_evidence '
                    'with that ID and a focused question. '
                    'Reuse exact saved passages and delegate only a specific comparison of long or conflicting '
                    'sources. Save a first draft once one defensible observation is available, then fill material gaps.'
                )
            self._emit_phase(phase)
            if allowed:
                selected = [tool for tool in request.tools
                            if (tool.get('name') if isinstance(tool, dict) else tool.name) in allowed]
                if selected:
                    overrides['tools'] = selected
        return handler(self._add_context(request, guidance, **overrides))

    @staticmethod
    def _repeated_preflight_item(messages):
        """Locate a factual row that still fails the same deterministic check."""
        rejected = []
        for message in reversed(messages):
            if getattr(message, 'type', None) == 'human':
                break
            if getattr(message, 'type', None) != 'tool':
                continue
            if getattr(message, 'name', None) in PUBLICATION_TOOLS | {'save_report_draft'}:
                if getattr(message, 'status', None) != 'error':
                    break
                match = _VALIDATION_ITEM.search(str(message.content))
                if match is None:
                    break
                rejected.append(match.group(1))
                if len(rejected) == 2:
                    return rejected[0] if rejected[0] == rejected[1] else None
        return None

    def _periodic_request(self, request, draft_id):
        workflow = self.periodic_workflow
        phase = workflow.phase()
        remaining = self.limit - self.calls + 1 if self.limit is not None else None
        has_verified_evidence = getattr(workflow, 'has_verified_evidence', None)
        verified = has_verified_evidence() if has_verified_evidence is not None else True
        resumed = workflow.is_resumed() if hasattr(workflow, 'is_resumed') else False
        closing = resumed or (remaining is not None and remaining <= 10)
        if phase == 'verify' and closing and verified and hasattr(workflow, 'reserve_delivery'):
            workflow.reserve_delivery()
            phase = workflow.phase()
        self._emit_phase(phase)
        guidance = f'Current periodic report workflow phase: {phase}.'
        if remaining is not None:
            guidance += f' Remaining author responses: {remaining}.'
        if draft_id and phase in ('draft', 'review'):
            guidance += f' Saved draft: /drafts/{draft_id}.json.'

        tool_names = [tool.get('name') if isinstance(tool, dict) else tool.name for tool in request.tools]
        allowed = set(tool_names) - DRAFT_TOOLS
        if phase == 'verify':
            if not closing and 'task' in allowed and hasattr(workflow, 'verification_handoff'):
                if handoff := workflow.verification_handoff():
                    ids = ', '.join(handoff['candidate_ids'])
                    if handoff['action'] == 'open_question':
                        guidance += (' Use the scout receipt to open concrete questions before broad '
                                     'follow-up retrieval. Candidate IDs: ' + ids + '.')
                        if handoff['candidate_map']:
                            guidance += ' Read the candidate map at ' + handoff['candidate_map'] + ' if needed.'
                    else:
                        tag = '[' + handoff['question_id'] + ']'
                        guidance += (' For the saved question ' + tag + ' ' + handoff['question']
                                     + ', delegate the group of originals via task(subagent_type="evidence-reader", '
                                     'description starting with ' + tag + '). Include the public question, '
                                     'requested dates and linked original IDs: ' + ids + '. '
                                     'Use read_evidence for one focused check or if the reader is unavailable.')
                    self.emit('periodic_verification_handoff_offered', {
                        key: handoff[key] for key in ('action', 'question_id', 'candidate_ids') if key in handoff})
            if verified:
                guidance += ' Advance to drafting when the remaining gaps can be stated in the report.'
            elif closing:
                originals = workflow.pending_originals() if hasattr(workflow, 'pending_originals') else []
                if originals:
                    guidance += (' Open an already captured original with read_evidence and its question_id. '
                                 'For a numerical record omit query and full_text. Captured originals: '
                                 + ', '.join(f'{qid} {rid}' for qid, rid in originals) + '.')
                    allowed = {'read_evidence'}
                else:
                    guidance += (' Turn a candidate into checked evidence by fetching and reading the original. '
                                 'A web search excerpt is a locator. Preserve other gaps in the report.')
                    allowed = {'fetch_webpage', 'read_evidence', 'task', 'update_research_question', 'request_public_api'}
        elif phase == 'draft' and verified:
            allowed = {'save_report_draft'}
            if not closing:
                allowed.add('write_todos')
            guidance += (' Save a concise, substantive draft with save_report_draft now. Use the '
                         'verified material already in the task. Declare coverage gaps. Include one to '
                         'three sourced observations; omit unfinished calculations, unnecessary charts, '
                         'and optional document cards. Capture their source IDs exactly.')
        elif phase == 'review' and draft_id:
            allowed = PUBLICATION_TOOLS
            if not closing or (remaining is not None and remaining > 5):
                allowed = allowed | {'read_file', 'read_evidence', 'write_todos'}
            guidance += (' Submit the saved draft now with publish_saved_report and empty edits for the first '
                         'review. If the reviewer returns issues, use the supplied draft ID and apply '
                         'the specific edits to unsupported claims or disclosures. Preserve supported findings.')
            if path := self._repeated_preflight_item(request.messages):
                allowed = {'publish_saved_report'}
                guidance += (
                    f' The same source-data check rejected {path} twice. If that row is supplemental, '
                    f'use an exact remove edit on {path} and remove the unsupported number where it is '
                    'repeated in narrative fields. Record the coverage gap. Keep checked rows intact. '
                    'Changing the wording or attaching the same source again cannot establish the number.'
                )
        tools = [tool for tool, name in zip(request.tools, tool_names) if name in allowed]
        return self._add_context(request, guidance, tools=tools or request.tools)

    def _emit_phase(self, phase):
        if phase != self.phase:
            self.phase = phase
            self.emit('report_author_phase', {'phase': phase, 'author_call': self.calls,
                                              'author_limit': self.limit})

    @staticmethod
    def _add_context(request, guidance, **overrides):
        original = request.system_message or SystemMessage(content='')
        content = list(original.content) if isinstance(original.content, list) else [{'type': 'text', 'text': original.content}]
        content.append({'type': 'text', 'text': guidance})
        return request.override(system_message=original.model_copy(update={'content': content}), **overrides)
