"""Structured, recoverable review failures; complete receipts stay on disk."""
from copy import deepcopy
import json
import re


class ReviewNeedsRevisionError(ValueError):
    def __init__(self, feedback):
        self.feedback=feedback
        super().__init__('Source review '+feedback['review_id']+' needs revision; '+str(feedback['issue_count'])+' issues. Read '+feedback['review_path'])

    def retain_draft(self,draft_id):
        self.feedback.update(draft_id=draft_id,draft_path='/drafts/'+draft_id+'.json')
        return self


def _at_path(body,path):
    value=body
    try:
        for part in path.split('/')[1:]:
            part=part.replace('~1','/').replace('~0','~')
            value=value[int(part)] if isinstance(value,list) else value[part]
        return value
    except (KeyError,IndexError,TypeError,ValueError):return None


def review_failure_feedback(review,draft,*,answer=False,source_navigation=None):
    """Keep every failed target, its legal edit location and complete correction."""
    from .review import _claim_path
    rid=review['review_id']
    if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',rid):
        raise ValueError('Review receipt ID is invalid')
    body=draft.model_dump() if hasattr(draft,'model_dump') else draft
    targets={t['claim_id']:t for t in review.get('targets',[]) if isinstance(t,dict)}
    issues=[]
    for index,raw in enumerate(review.get('revision_required',[])):
        target=targets.get(raw['claim_id'],{})
        path=raw.get('claim_path') or target.get('claim_path') or _claim_path({'claim_id':raw['claim_id'],**target})
        current=_at_path(body,path) if path else None
        kind='string' if isinstance(current,str) else 'claim' if isinstance(current,dict) and 'evidence_ids' in current else 'structured'
        item={key:raw[key] for key in ('claim_id','status','reason','correction','origin','issue_kind','finding_id','missing_evidence_ids','scope_alignment','claim_text','claim_span','context_status','conflict_confirmed') if key in raw}
        item.update(claim_path=path,field_kind=kind,details_pointer='/revision_required/'+str(index))
        if target.get('summary_span'):
            item.update(summary_span=target['summary_span'],failed_text=target.get('statement',''),
                parent_context_pointer=target.get('parent_context_pointer'))
        if kind=='string':
            item['editable_fields']=[path]
            item['edit_rule']='Replace this whole string; it has no nested evidence_ids field. Correct/remove the original unsupported premise; do not replace it with a new unbound external fact.'
            if not answer:item['source_binding_path']='/narrative_evidence_ids'
            removable=(path and (path.startswith('/questions_to_watch/') or path.startswith('/limitations/') and len(body.get('limitations',[]))>1))
            if path and path.startswith('/limitations/'):
                item['edit_rule']='Replace this limitation with a corrected statement. Narrow the specific unsupported premise while preserving independent evidence-coverage, timing, sampling and risk qualifications that still apply.'
            if removable:
                item['remove_item_example']={'op':'remove','path':path}
                item['remove_rule']=('Remove only if every applicable qualification is already preserved elsewhere. Keep at least one honest limitation.'
                    if path.startswith('/limitations/') else
                    'You may remove this optional follow-up instead of substituting another factual premise. Remove multiple indices in descending order.')
        elif kind=='claim':
            item['editable_fields']=[path+'/statement',path+'/evidence_ids']
            item['source_binding_path']=path+'/evidence_ids'
            item['source_binding_current_value']=current['evidence_ids']
        elif path:item['editable_fields']=[path]
        if current is not None:
            text=current if isinstance(current,str) else json.dumps(current,ensure_ascii=False)
            if len(text)<=240:item['current_value']=current
            else:item.update(current_value_preview=text[:240],current_value_complete=False)
        item['source_evidence_ids']=list(dict.fromkeys(q['record_id'] for q in raw.get('quotes',[]) if isinstance(q,dict) and q.get('record_id')))
        if source_navigation:
            candidates=source_navigation.get('candidates_by_target',{}).get(raw['claim_id'])
            if candidates:item['candidate_source_navigation']=candidates
        issues.append(item)
    # No source bodies, quote text or raw tool kwargs are copied into failures.
    verdicts=[{**{k:v[k] for k in ('claim_id','status','reason','correction','premise_assessment','scope_alignment') if k in v},
               'quotes':[{'record_id':q['record_id']} for q in v.get('quotes',[]) if isinstance(q,dict) and q.get('record_id')]}
              for v in review.get('verdicts',[])]
    slim_review={k:review[k] for k in ('review_id','status','review_version','review_stage','claims_checked','claims_total','unassessed_claim_ids','host_issues','revision_required') if k in review}
    slim_review['verdicts']=verdicts
    slim_review['revision_required']=[{**{k:v for k,v in row.items() if k!='quotes'},
        'quotes':[{'record_id':q['record_id']} for q in row.get('quotes',[]) if isinstance(q,dict) and q.get('record_id')]}
        for row in review.get('revision_required',[])]
    texts=review.get('evidence_text')
    aliases={'R'+str(i+1):source for i,source in enumerate(sorted(texts))} if isinstance(texts,dict) else {}
    feedback={'status':'error','error_type':'review_needs_revision','review_id':rid,
        'review_path':'/reviews/'+rid+'.json','review_status':review.get('status'),
        'issue_count':len(issues),'failed_target_count':len({r['claim_id'] for r in issues}),
        'issues':issues,'source_aliases':aliases,'review':slim_review,
        'repair':'Address all listed failed targets before resubmitting. Check candidate originals before choosing citations; a source recalled/opened earlier is not automatically included in this review. Either explicitly bind genuine support or remove/narrow the same unsupported premise. Do not introduce another external fact without inspecting and binding its source in the same edit. Full reasons/quotes remain in review_path. Never modify evidence or count an unsuccessful save as delivery.',
        'binding_rule':('O/K/I and other real Claims require their own evidence_ids. Summary/questions/limitations/judgment may use the union of explicitly cited report sources plus narrative_evidence_ids. Add sources used only by those text fields to the narrative_evidence_ids array. Plain IDs in a string do not bind sources.' if not answer else 'Use actual Claim evidence_ids fields; next_steps are strings. Do not invent nested source fields.')}
    if not answer:
        current=list(body.get('narrative_evidence_ids',[]))
        feedback['narrative_source_binding']={'path':'/narrative_evidence_ids','current_value':current,
            'whole_array_replace_example':{'op':'replace','path':'/narrative_evidence_ids','value':current},
            'instruction':'The example preserves the CURRENT array; it adds no candidate. After checking exact support, supply the complete replacement array retaining needed current IDs and adding only saved IDs you select (maximum 10). Or remove/narrow the unsupported premise. Never invent a string/evidence_ids path.'}
    if source_navigation:feedback['source_navigation']={k:v for k,v in source_navigation.items() if k!='candidates_by_target'}
    return feedback


def review_failure_view(feedback,*,artifact=None,workspace_root=None,max_chars=30000):
    """Keep every unique failed target and every issue index, never cut JSON."""
    view=deepcopy({k:v for k,v in feedback.items() if k!='review'})
    view['view']='structured-review-failure-v1'
    view['review']={k:v for k,v in feedback['review'].items() if k in ('review_id','status','review_version','review_stage','claims_checked','claims_total')}
    grouped={}
    for source in view['issues']:
        row=grouped.get(source['claim_id'])
        if row is None:
            row=deepcopy(source);row['details_pointers']=[source['details_pointer']]
            row['details_indices']=[int(source['details_pointer'].rsplit('/',1)[-1])]
            grouped[source['claim_id']]=row
        else:
            row['details_pointers'].append(source['details_pointer'])
            row['details_indices'].append(int(source['details_pointer'].rsplit('/',1)[-1]))
            row.setdefault('additional_findings',[]).append({k:v for k,v in source.items()
                if k in ('reason','correction','issue_kind','missing_evidence_ids','scope_alignment','details_pointer')})
    view['issues']=list(grouped.values())
    view['failed_target_count']=len(view['issues'])
    view['detail_pointer_template']='/revision_required/{index}'
    if artifact:
        from pathlib import Path
        safe={k:artifact[k] for k in ('artifact_id','sha256','bytes') if k in artifact}
        if workspace_root and artifact.get('path'):
            path=Path(artifact['path']).resolve();workspace=Path(workspace_root).resolve()
            if path.is_relative_to(workspace):safe['path']='/'+path.relative_to(workspace).as_posix()
        view['artifact']=safe
    measure=lambda:len(json.dumps(view,ensure_ascii=False))
    if measure()>max_chars:
        for item in view['issues']:item.pop('candidate_source_navigation',None)
        if view.get('source_navigation'):view['source_navigation']['details_required']=True
        for item in view['issues']:
            for key,limit in (('reason',140),('correction',180)):
                value=item.get(key,'')
                if len(value)>limit:item[key+'_preview']=value[:limit];item.pop(key,None)
            item.pop('additional_findings',None)
            item.pop('current_value',None);item.pop('current_value_preview',None)
        view['details_required']=True
    if measure()>max_chars:
        view['issues']=[{k:item[k] for k in ('claim_id','claim_path','details_indices') if k in item}
                        for item in view['issues']]
        view['source_aliases']={}
        view['details_required']=True
    # Host-produced IDs/paths are bounded by 90 targets. All original issue
    # indices remain navigable even when one target has several host findings.
    return view
