"""Saved-draft delivery guidance and completion after checked publication."""
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage, SystemMessage


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
    """Offer saved-draft repair guidance without changing history or tools."""
    def __init__(self,emit=lambda *_:None,service=None):self.emit,self.service=emit,service

    def wrap_model_call(self,request,handler):
        draft_id=getattr(self.service,'latest_report_draft_id',None)
        if draft_id:
            original=request.system_message or SystemMessage(content='')
            content=list(original.content) if isinstance(original.content,list) else [{'type':'text','text':original.content}]
            content.append({'type':'text','text':f'Saved draft: /drafts/{draft_id}.json. For validation/review corrections, prefer publish_saved_report with explicit field edits rather than regenerating the report; empty edits reuse the exact draft. Use actual current feedback to decide what needs correction. Reuse validated quotes and calculations. Inspect a missing qualification or source when necessary. Earlier evidence and tool history remain available; the existence of a draft does not prove research is complete. All publication checks still apply.'})
            request=request.override(system_message=original.model_copy(update={'content':content}))
        return handler(request)
