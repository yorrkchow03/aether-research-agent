"""Attach dated, attributed report-review notices without rewriting old memories.

A user flag is not a fact. A saved model review is an attributed judgment. This
module joins those notices to retrieval results, preserving the original summary
and source IDs and filtering every review/update by its actual acquisition time.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from research_data.http import utcnow
from research_data.normalize import timestamp

SCOPE = ('Earlier research is context, not a new factual source. Review notices '
    'are attributed judgments, not independently established truth. Reopen the '
    'original evidence and relevant update before reusing an affected claim.')


def _hash(value):
    body=json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    return hashlib.sha256(body.encode()).hexdigest()


def original_evidence_ids(body):
    """Extract report/answer citations, excluding review quotes and personal context."""
    found=[]
    def walk(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key in ('evidence_ids','supporting_evidence_ids','counter_evidence_ids','source_evidence_ids') and isinstance(item,list):
                    for rid in item:
                        if isinstance(rid,str) and not rid.startswith('context-') and rid not in found:found.append(rid)
                elif key not in ('review','semantic_checks','sources','run_metadata'):walk(item)
        elif isinstance(value,list):
            for item in value:walk(item)
    walk(body.get('report') or body.get('draft') or body)
    return found


def attach_report_notices(row, body, provider=None, *, cutoff=None, report_lookup=None,
                          max_notices=6):
    """Return a new retrieval row; callbacks must be read-only and local.

    ``provider`` is ReportFeedbackStore or callable(report_id, cutoff=...).
    Metadata is filtered again here even if the provider ignores its cutoff.
    A report-body hash mismatch suppresses that notice rather than rebinding it.
    """
    if not 1<=max_notices<=20:raise ValueError('Report notice bound must be 1..20')
    result=dict(row);ids=original_evidence_ids(body)
    if not ids:ids=list(dict.fromkeys(row.get('source_evidence_ids',[])))
    result['source_evidence_ids']=ids
    result['original_source_evidence_ids']=list(ids)
    result.update(memory_notices=[],memory_status='historical_context',next_report_ids=[],
        unreviewed_concern_count=0,notice_context='',notice_scope=SCOPE)
    if provider is None:return result
    report_id=row.get('report_id') or row.get('output_id')
    as_of=cutoff or utcnow();before=timestamp(as_of)
    call=getattr(provider,'list_for_report',provider)
    try:
        items=call(report_id,cutoff=as_of)
        if not isinstance(items,list):raise ValueError('Invalid local notice response')
    except (ValueError,KeyError,OSError,TypeError,sqlite3.Error) as exc:
        result.update(memory_status='notice_lookup_unavailable',notice_gaps=[{'reason':'report_notice_lookup_unavailable','error_type':type(exc).__name__}])
        return result
    notices=[];gaps=[]
    for item in items:
        try:
            if item.get('report_id')!=report_id or timestamp(item['known_at'])>before:continue
            if item.get('report_sha256')!=_hash(body):
                gaps.append({'reason':'notice_report_binding_mismatch'});continue
            # Reconstruct eligible events; do not trust a current latest_review
            # or status supplied by a callback for a historical query.
            events=[e for e in item.get('events',[]) if timestamp(e['known_at'])<=before]
            review=next((e for e in reversed(events) if e.get('kind')=='review'),None)
            if not review:
                result['unreviewed_concern_count']+=1;continue
            if review.get('assessment') not in ('needs_correction','needs_more_evidence','not_reproduced'):
                gaps.append({'reason':'unknown_review_assessment'});continue
            notice={'feedback_id':item.get('feedback_id'),'claim_id':item.get('claim_id'),
                'assessment':review['assessment'],'reviewer_kind':review.get('reviewer_kind','unknown'),
                'review_known_at':review['known_at'],'correction':str(review.get('correction') or '')[:1600],
                'correction_preview_only':len(str(review.get('correction') or ''))>1600,
                'review_evidence_ids':list(dict.fromkeys(review.get('evidence_ids',[]))),
                'receipt_sha256':(review.get('receipt') or {}).get('sha256'),
                'status':'reviewed','scope':SCOPE}
            resolution=next((e for e in reversed(events) if e.get('kind')=='resolve' and
                e.get('review_event_id')==review.get('event_id')),None)
            if resolution and review['assessment']=='needs_correction' and resolution.get('source_review_checked'):
                rid=resolution.get('replacement_report_id')
                replacement=report_lookup(rid) if report_lookup and rid else None
                if replacement is not None:
                    if timestamp(replacement['created_at'])>before:
                        gaps.append({'reason':'replacement_report_not_created_by_cutoff'});resolution=None
                    elif resolution.get('replacement_report_sha256')!=_hash(replacement):
                        gaps.append({'reason':'replacement_report_binding_mismatch'});resolution=None
                elif report_lookup:
                    gaps.append({'reason':'replacement_report_unavailable'});resolution=None
                if resolution:
                    notice.update(status='source_reviewed_update_available',replacement_report_id=rid,
                        resolution_known_at=resolution['known_at'])
            notices.append(notice)
        except (ValueError,KeyError,TypeError,OSError,OverflowError):
            gaps.append({'reason':'invalid_or_unavailable_notice_metadata'})
    notices.sort(key=lambda n:(timestamp(n['review_known_at']),n.get('feedback_id') or ''),reverse=True)
    result['memory_notices']=notices[:max_notices]
    if len(notices)>max_notices:gaps.append({'reason':'report_notices_truncated','total':len(notices)})
    relevant=[n for n in notices if n['assessment'] in ('needs_correction','needs_more_evidence')]
    result['next_report_ids']=list(dict.fromkeys(n['replacement_report_id'] for n in result['memory_notices'] if n.get('replacement_report_id')))
    if relevant:
        result['memory_status']='has_source_reviewed_updates' if all(n.get('replacement_report_id') for n in relevant) else 'affected_claims_require_recheck'
    elif notices:result['memory_status']='reviewed_concerns_recorded'
    if result['memory_notices']:
        pieces=['Review notices for earlier report '+str(report_id)+' (do not reuse affected claims as current facts):']
        for n in result['memory_notices']:
            text=str(n.get('claim_id') or 'summary')+' | '+n['assessment']+' | '+n['reviewer_kind']+' | '+n['review_known_at']
            if n['correction']:text+=' | '+n['correction']
            if n['correction_preview_only']:text+=' | Preview only; read full saved review before applying conditions.'
            if n.get('replacement_report_id'):text+=' | Read update '+n['replacement_report_id']
            pieces.append(text)
        full='\n'.join(pieces)
        result['notice_context_truncated']=len(full)>5000
        result['notice_context']=full if len(full)<=5000 else full[:4880]+'\n[Notice preview truncated; read the complete saved review notices before reusing affected claims.]'
    if gaps:result['notice_gaps']=gaps
    return result
