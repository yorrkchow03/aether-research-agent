"""Saved-draft delivery guidance and completion after checked publication."""
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.tools import tool
from research_data.http import dumps, utcnow


DELIVERY_TOOLS = {'save_report_draft', 'publish_report', 'publish_saved_report', 'read_evidence', 'read_file'}
CALCULATION_TOOLS = {'analyze_market', 'register_calculation'}
PUBLICATION_TOOLS = {'publish_report', 'publish_saved_report'}


def report_checkpoint_tool(service):
    """Retain a model-authored draft without spending a semantic review attempt."""
    from .chat_reply import requests_report
    from .report_recovery import LivePublishArguments
    from .reports import ReportDraft, validate_report

    @tool(args_schema=LivePublishArguments)
    def save_report_draft(draft: ReportDraft, user_request: str = '') -> str:
        """Save a short unpublished report from scoped evidence already available. Start with 1–3 sourced observations, add supported dated events if available, and name material gaps. Use statement and evidence_ids alone for qualitative facts. Use metric/value/unit together only for an exact saved field. Leave optional document cards and advice for the final draft."""
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
    def __init__(self, emit=lambda *_:None, service=None, *, max_model_calls=None, plan_job_ids=()):
        self.emit, self.service = emit, service
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
            if self.calls > self.limit:
                raise RuntimeError('The report authoring budget ended; proceed with the retained draft and saved evidence.')
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
                phase = 'delivery'
                self.delivery_started = True
                allowed = DELIVERY_TOOLS
                if plan_missing:
                    allowed = allowed | {'write_todos'}
                guidance += (
                    f' End discovery now. {self.limit - self.calls + 1} author responses remain, including this one. '
                    'Submit the source-backed report from saved work now. If you have substantive additions to an '
                    'early checkpoint, submit the enriched report; otherwise publish the saved draft. '
                    'After a validation or review error, use focused field edits. Reopen only the source needed to '
                    'resolve that specific error. Omit a nonessential document card if its exact excerpt is unavailable.'
                )
            elif checkpoint_due:
                phase = 'checkpoint'
                self.checkpoint_attempts += 1
                allowed = {'save_report_draft'}
                guidance += (
                    ' Save a compact unpublished draft now in one save_report_draft call. Use one to three '
                    'supported observations and any already supported distinct events. Copy the requested scope '
                    'and actual source IDs. Use statement and evidence_ids for qualitative facts; use numeric '
                    'fields only for exact saved metric keys, plain numeric values and exact units. Omit optional '
                    'document cards, charts without a checked series, and advice for this checkpoint. State material gaps.'
                )
            else:
                phase = 'discovery'
                allowed = None
                guidance += (
                    ' Start with one date-scoped overview and the central saved measurements. For a broad '
                    'weekly brief, look for distinct material events across the requested dates and themes. '
                    'Use a focused follow-up only if it can add a missing event or change an important conclusion. '
                    'Reuse exact saved passages and delegate only a specific comparison of long or conflicting '
                    'sources. Save a first draft once one defensible observation is available, then fill material gaps.'
                )
            if phase != self.phase:
                self.phase = phase
                self.emit('report_author_phase', {'phase': phase, 'author_call': self.calls,
                                                  'author_limit': self.limit})
            if allowed:
                selected = [tool for tool in request.tools
                            if (tool.get('name') if isinstance(tool, dict) else tool.name) in allowed]
                if selected:
                    overrides['tools'] = selected
        original=request.system_message or SystemMessage(content='')
        content=list(original.content) if isinstance(original.content,list) else [{'type':'text','text':original.content}]
        content.append({'type':'text','text':guidance})
        request=request.override(system_message=original.model_copy(update={'content':content}), **overrides)
        return handler(request)
