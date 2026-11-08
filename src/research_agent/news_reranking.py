"""Bounded optional query-dependent Jev judgments; no Pro fallback or source edits."""
from threading import Lock
from time import perf_counter


class NewsReranker:
    def __init__(self, service, emit=lambda *_: None, cancel=None, *, limit=96, max_seconds=120):
        self.service,self.emit,self.cancel=service,emit,cancel
        self.limit,self.max_seconds=limit,max_seconds
        self.attempts=0;self.spent_seconds=0.;self.failures=0;self.lock=Lock()

    def assess(self, task, **arguments):
        if task!='relevance':raise ValueError('News reranking only assesses relevance')
        with self.lock:
            if self.attempts>=self.limit or self.spent_seconds>=self.max_seconds or self.failures>=3 or (self.cancel and self.cancel.is_set()):
                return {'status':'unavailable','task':task,'scores':{},'reason':'Reranking task bound reached; retain hybrid baseline.'}
            self.attempts+=1
        start=perf_counter()
        # The caller checks public-source/query eligibility. Preserve the exact
        # query and window; generic topic descriptors would erase answer relevance.
        try:result=self.service.assess(task,**arguments)
        except Exception as exc:result={'status':'error','task':task,'scores':{},'error_type':type(exc).__name__}
        with self.lock:
            self.spent_seconds+=perf_counter()-start
            if result.get('status') in ('error','unavailable'):self.failures+=1
        self.emit('news_rerank_judgment',{k:result.get(k) for k in ('status','task','model','provider','label','confidence','cache_hit','latency_seconds','usage','evidence_ids','physical_request_count','billable_request_made','billing')})
        return result
