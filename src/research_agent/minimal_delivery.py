"""Bounded final delivery: exact approved facts, or a literal evidence-gap notice."""
import hashlib
from research_data.http import dumps, utcnow

NOTICE_TITLE = 'Evidence availability notice'
NOTICE_SUMMARY = 'A source-reviewed factual report could not be completed within this attempt. The requested research conclusion remains unresolved.'
NOTICE_REASON = 'No final factual report passed all required publication checks.'
NOTICE_LIMIT = 'This is an evidence-gap delivery notice, not an approved research conclusion. The original draft and check results are retained.'
MINIMAL_SUMMARY = 'This reduced report contains only the observations listed below. Unresolved conclusions from the full draft have been omitted.'
MINIMAL_LIMIT = 'The requested research scope is only partially answered. Unresolved interpretations, forecasts and recommendations have been omitted.'


def evidence_gap_notice(scope=None):
    from .reports import ReportDraft
    return ReportDraft(title=NOTICE_TITLE, summary=NOTICE_SUMMARY, scope=scope,
        delivery_kind='evidence_gap', observations=[], judgment='insufficient_evidence',
        judgment_reason=NOTICE_REASON, limitations=[NOTICE_LIMIT])


def validate_notice(draft):
    expected=evidence_gap_notice(draft.scope)
    if draft.model_dump()!=expected.model_dump():
        raise ValueError('Evidence-gap notices accept only the fixed host text and no external factual claims')


def notice_review(draft):
    validate_notice(draft)
    digest=hashlib.sha256(dumps(draft.model_dump(),sort_keys=True).encode()).hexdigest()
    return dict(review_id='notice-'+digest[:24], review_version='support-harness-notice-v1',
        status='notice', reviewer_model='programmatic', reviewed_at=utcnow(),
        draft_sha256=digest, targets=[], evidence_text={}, verdicts=[],
        claims_checked=0, claims_total=0, supported=0, unsupported=0, contradicted=0,
        host_issues=[], revision_required=[], scope='Literal host delivery notice; no source-support certification.')


def verify_notice_review(review,draft):
    from .reports import ReportDraft
    draft=draft if isinstance(draft,ReportDraft) else ReportDraft.model_validate(draft)
    validate_notice(draft)
    expected=notice_review(draft)
    for key in ('status','review_version','review_id','draft_sha256','targets','evidence_text','verdicts','claims_checked','claims_total'):
        if review.get(key)!=expected[key]:raise ValueError('Invalid evidence-gap notice receipt: '+key)


def minimal_report(draft,review,maximum=2):
    """Never interpret an unflagged/partial target as approval; copy exact Claims."""
    from .reports import ReportDraft
    targets={t['claim_id']:t for t in review.get('targets',[])}
    verdicts={v['claim_id']:v for v in review.get('verdicts',[])}
    blocked={i.get('claim_id') for i in [*review.get('revision_required',[]),*review.get('host_issues',[])]}
    kept=[];ids=[]
    for prefix,claims in (('O',draft.observations),('K',draft.key_changes)):
        for index,claim in enumerate(claims,1):
            cid=prefix+str(index);v=verdicts.get(cid,{});t=targets.get(cid,{})
            if cid in blocked or v.get('status')!='supported' or v.get('scope_alignment') not in ('aligned','not_applicable'):
                continue
            if any(t.get(k)!=value for k,value in claim.model_dump().items()):continue
            bound=set(claim.evidence_ids)
            if not any(q.get('record_id') in bound and q.get('purpose','support')=='support' and q.get('text') for q in v.get('quotes',[])):
                continue
            kept.append(claim.model_copy(deep=True));ids.append(cid)
            if len(kept)>=maximum:break
        if len(kept)>=maximum:break
    if not kept:return None,[]
    return ReportDraft(title='Limited evidence report', summary=MINIMAL_SUMMARY,
        scope=draft.scope, delivery_kind='minimal', observations=kept,
        limitations=[MINIMAL_LIMIT]),ids
