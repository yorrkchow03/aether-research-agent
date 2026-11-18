"""One bounded same-model audit of factual minimal-report content.

Exact harness-authored process notices are checked by code. Every other target
still receives the generation model's complete source/quote review. This is a
separate, replayable protocol rather than a v14 critic receipt without a critic.
"""
from copy import deepcopy
import hashlib
import json

from research_data.http import utcnow
from .jev_review import TARGET_VERSION, _host_verdict
from .host_review_facts import is_verified_host_target
from .review import EvidenceReviewer, check_review, evidence_text, prepare_review_targets, review_targets

REVIEW_VERSION = 'support-hybrid-minimal-v1'
CHART_REVIEW_VERSION = 'support-hybrid-minimal-v2'


def _digest(value):
    data=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


def _targets(draft):
    return prepare_review_targets(review_targets(draft,review_version=TARGET_VERSION),review_version=TARGET_VERSION)


def _clock_evidence(text):
    """The host needs source clocks even if no model-reviewed claim cites the chart."""
    row = json.loads(text)
    fields = {key: row[key] for key in ('record_id', 'retrieved_at', 'calculated_at', 'created_at') if key in row}
    if isinstance(row.get('source'), dict) and 'retrieved_at' in row['source']:
        fields['source'] = {'retrieved_at': row['source']['retrieved_at']}
    return json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _review_identity(version, body, targets, inner, process_context, research_cutoff, host_evidence):
    payload = {'version': version, 'draft': body, 'targets': targets, 'model_review': inner,
               'process_context': process_context, 'research_cutoff': research_cutoff}
    if version == CHART_REVIEW_VERSION:
        payload['host_only_evidence_text'] = host_evidence
    return _digest(payload)


def _combine(targets, model_review, host_ids):
    normal=[t for t in targets if t['claim_id'] not in host_ids]
    checked=check_review({'verdicts':model_review['verdicts']},normal,model_review['evidence_text'])
    by_id={v['claim_id']:v for v in checked['verdicts']}
    by_id.update({t['claim_id']:_host_verdict(t) for t in targets if t['claim_id'] in host_ids})
    checked.update(verdicts=[by_id[t['claim_id']] for t in targets],claims_checked=len(targets),claims_total=len(targets),
        scope='Generation-model source review for every nonliteral target plus exact host-controlled reduced-report process checks. Source support is not world truth.')
    return checked


class MinimalEvidenceReviewer:
    supports_process_context=True

    def __init__(self,settings,event_sink=None,user_context_id=None,diagnostic_dir=None,
                 cancel=None,research_cutoff=None,max_requests=2):
        if type(max_requests) is not int or not 1<=max_requests<=2:
            raise ValueError('Minimal report audit has at most two physical model requests')
        self.settings=settings;self.cancel=cancel;self.research_cutoff=research_cutoff
        self.inner=EvidenceReviewer(settings,event_sink,user_context_id,diagnostic_dir,cancel,research_cutoff,max_requests)
        self.cache={}

    @property
    def calls(self):return self.inner.calls

    @property
    def max_requests(self):return self.inner.max_requests

    @property
    def rounds(self):return self.inner.rounds

    @property
    def max_rounds(self):return self.inner.max_rounds

    @property
    def user_context_id(self):return self.inner.user_context_id

    @user_context_id.setter
    def user_context_id(self,value):self.inner.user_context_id=value

    def review(self,draft,lookup,*,process_context=None):
        from .reports import ReportDraft
        draft=draft if isinstance(draft,ReportDraft) else ReportDraft.model_validate(draft)
        if draft.delivery_kind!='minimal':raise ValueError('MinimalEvidenceReviewer accepts only reduced reports')
        if self.cancel and self.cancel.is_set():raise RuntimeError('Review cancelled')
        body=draft.model_dump();targets=_targets(draft)
        source_ids={rid for t in targets for rid in t['evidence_ids']}
        context_texts={rid:evidence_text(lookup(rid),targets=[t for t in targets if rid in t['evidence_ids']]) for rid in source_ids}
        host_ids=[t['claim_id'] for t in targets if is_verified_host_target(t,body,context_texts,process_context)]
        normal=[t for t in targets if t['claim_id'] not in host_ids]
        if not normal:raise ValueError('A minimal report needs factual targets reviewed by the model')
        # No critic and no additional provider. The inner auditor still covers
        # all remaining summary sentences, factual claims and custom limits.
        model_review=self.inner.review_claims(normal,lookup,identity_payload=body,
            include_user_context=False,scope_critic=False,process_context=process_context)
        host_evidence = {rid: _clock_evidence(context_texts[rid])
                         for rid in sorted(source_ids - model_review['evidence_text'].keys())}
        version = CHART_REVIEW_VERSION if host_evidence else REVIEW_VERSION
        identity = _review_identity(version,body,targets,model_review,process_context,self.research_cutoff,host_evidence)
        if identity in self.cache:return self.cache[identity]
        result=_combine(targets,model_review,host_ids)
        result.update(review_id='review-'+identity[:24],review_version=version,
            reviewed_at=utcnow(),reviewer_model=model_review['reviewer_model'],
            reviewer_provider='generation_model',requested_model=self.settings.model_id,
            draft_sha256=_digest(body),targets=targets,evidence_text=model_review['evidence_text'],
            model_review=deepcopy(model_review),host_literal_target_ids=host_ids,
            summary_context=model_review.get('summary_context'),report_scope=body.get('scope'),
            research_cutoff=self.research_cutoff,process_context=deepcopy(process_context),
            usage=deepcopy(model_review.get('usage',{})),
            request_usages=deepcopy(model_review.get('request_usages',[])),
            format_repairs=model_review.get('format_repairs',0),
            request_budget={'max_requests':self.max_requests,'requests_used':self.calls,
                            'max_rounds':self.max_rounds,'rounds_used':self.rounds,'sdk_max_retries':0,
                            'scope':'One factual audit plus at most one format repair, without a scope critic or provider fallback.'})
        if host_evidence:
            result['host_only_evidence_text'] = host_evidence
        verify_saved_minimal_review(result,draft)
        self.cache[identity]=result
        return result


def verify_saved_minimal_review(review,draft,lookup=None):
    from .reports import ReportDraft
    draft=draft if isinstance(draft,ReportDraft) else ReportDraft.model_validate(draft)
    version = review.get('review_version')
    if version not in (REVIEW_VERSION, CHART_REVIEW_VERSION) or draft.delivery_kind!='minimal':
        raise ValueError('Unsupported minimal-report review protocol')
    body=draft.model_dump();targets=_targets(draft)
    if review.get('draft_sha256')!=_digest(body) or review.get('targets')!=targets:
        raise ValueError('Saved minimal audit differs from the complete exact draft')
    if review.get('report_scope')!=body.get('scope'):
        raise ValueError('Saved minimal report scope differs from the draft')
    reviewed_texts = review.get('evidence_text')
    host_evidence = review.get('host_only_evidence_text', {})
    if not isinstance(reviewed_texts, dict) or not isinstance(host_evidence, dict):
        raise ValueError('Saved minimal evidence is invalid')
    if version == CHART_REVIEW_VERSION:
        expected = {rid for target in targets for rid in target['evidence_ids']} - reviewed_texts.keys()
        if (not expected or host_evidence.keys() != expected or
            any(not isinstance(value, str) or _clock_evidence(value) != value
                or json.loads(value).get('record_id') != rid for rid, value in host_evidence.items())):
            raise ValueError('Saved minimal host-only clock records differ from exact target bindings')
        if lookup is not None and any(
            _clock_evidence(evidence_text(lookup(rid),targets=[t for t in targets if rid in t['evidence_ids']])) != value
            for rid, value in host_evidence.items()
        ):
            raise ValueError('Saved minimal host-only clocks differ from the archived records')
    elif 'host_only_evidence_text' in review:
        raise ValueError('Unexpected host-only evidence in a legacy minimal receipt')
    proof_texts = {**reviewed_texts, **host_evidence}
    host_ids=[t['claim_id'] for t in targets if is_verified_host_target(t,body,proof_texts,review.get('process_context'))]
    if review.get('host_literal_target_ids')!=host_ids:
        raise ValueError('Saved minimal host-literal set differs from exact process text')
    normal=[t for t in targets if t['claim_id'] not in host_ids]
    inner=review.get('model_review')
    if not normal or not isinstance(inner,dict) or inner.get('targets')!=normal:
        raise ValueError('Minimal model audit must cover every nonliteral target exactly')
    if review.get('reviewer_provider')!='generation_model' or str(inner.get('reviewer_model','')).startswith('jev-'):
        raise ValueError('Minimal generation-model audit cannot substitute another provider')
    if review.get('reviewer_model')!=inner.get('reviewer_model'):
        raise ValueError('Saved minimal model identity differs from the inner audit')
    texts=inner.get('evidence_text')
    if not isinstance(texts,dict) or set(texts)!={rid for t in normal for rid in t['evidence_ids']} or texts!=review.get('evidence_text'):
        raise ValueError('Saved minimal source set differs from the nonliteral targets')
    summary=inner.get('summary_context')
    if (not isinstance(summary,dict) or summary.get('text')!=body['summary'] or
        summary.get('sha256')!=hashlib.sha256(body['summary'].encode()).hexdigest() or
        summary!=review.get('summary_context')):
        raise ValueError('Saved minimal summary context differs from the exact draft')
    checked=check_review({'verdicts':inner.get('verdicts',[])},normal,texts)
    for key in ('status','verdicts','host_issues','claims_checked','supported','unsupported','contradicted','revision_required'):
        if inner.get(key)!=checked[key]:raise ValueError('Saved inner minimal audit mismatch: '+key)
    result=_combine(targets,inner,host_ids)
    for key in ('status','verdicts','host_issues','claims_checked','claims_total','supported','unsupported','contradicted','revision_required'):
        if review.get(key)!=result[key]:raise ValueError('Saved minimal combined audit mismatch: '+key)
    budget=review.get('request_budget',{})
    if (type(budget.get('max_requests')) is not int or not 1<=budget['max_requests']<=2 or
        type(budget.get('requests_used')) is not int or not 1<=budget['requests_used']<=budget['max_requests'] or
        budget.get('max_rounds')!=1 or budget.get('rounds_used')!=1 or budget.get('sdk_max_retries')!=0 or
        len(inner.get('request_usages',[]))!=budget['requests_used']):
        raise ValueError('Saved minimal audit physical-request budget is invalid')
    for key,default in (('usage',{}),('request_usages',[]),('format_repairs',0)):
        if review.get(key)!=inner.get(key,default):raise ValueError('Saved minimal usage or format accounting differs from inner audit')
    identity = _review_identity(version, body, targets, inner, review.get('process_context'),
                                review.get('research_cutoff'), host_evidence)
    if review.get('review_id')!='review-'+identity[:24]:raise ValueError('Saved minimal input identity mismatch')
    return result
