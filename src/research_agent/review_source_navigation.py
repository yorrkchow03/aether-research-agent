"""Task-local source pointers for repairing citations, never factual support."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .live import atomic
from .research_state import ID

_STOP=set('a an and are as at be been being but by data did do does for from had has have in into is it its no not of on only or our post report research says source sources supplied than that the their these this through to was were which with without'.split())


def _terms(text):
    return {word.casefold() for word in re.findall(r'[\w]+',str(text)) if len(word)>2 and not word.isdecimal()}-_STOP


def _virtual(path,workspace):
    path=Path(path).resolve();workspace=Path(workspace).resolve()
    return '/'+path.relative_to(workspace).as_posix() if path.is_relative_to(workspace) and path.is_file() else None


def _reader_id_from_task(payload):
    """Decode only host task envelopes, never search arbitrary source prose."""
    import ast,io,tokenize
    if not isinstance(payload,dict):return None
    if not payload.get('reader_id') and isinstance(payload.get('messages'),list) and len(payload['messages'])==1:
        message=payload['messages'][0]
        if not isinstance(message,str):return None
        try:
            tokens=iter(tokenize.generate_tokens(io.StringIO(message).readline))
            first,second,third=next(tokens),next(tokens),next(tokens)
            if first.string!='content' or second.string!='=' or third.type!=tokenize.STRING:return None
            payload=json.loads(ast.literal_eval(third.string))
        except (ValueError,TypeError,SyntaxError,StopIteration,tokenize.TokenError):return None
    reader=payload.get('reader_id') if isinstance(payload,dict) else None
    return reader if isinstance(reader,str) and re.fullmatch(r'[a-f0-9]{32}',reader) else None


def source_repair_navigation(service,review):
    """Use only this job's host observations and at most three actual readers.

    Lexical title matches are discovery hints. Canonical public lookup supplies
    display metadata; no candidate is read, cited, or passed to the reviewer by
    this helper. A source not displayed here remains unknown.
    """
    working=getattr(service,'working_memory',None);job=getattr(service,'job_id',None)
    if not working or not job:return None
    state=working.state(job);research=state.get('research_state') or {}
    snapshot=working.state_snapshot(job);state_path=_virtual(snapshot['path'],service.workspace)
    if not state_path:return None
    pool={}
    rows=research.get('sources',[])
    for index,row in list(enumerate(rows))[-192:]:
        rid=row.get('evidence_id')
        if not isinstance(rid,str) or not ID.fullmatch(rid):continue
        status='opened' if row.get('source_status')=='opened' else 'recalled'
        if rid not in pool or status=='opened':
            pool[rid]={'record_id':rid,'title':str(row.get('title') or ''),'source_status':status,
                'pointer':{'file_path':state_path,'json_pointer':'/research_state/sources/'+str(index)}}
    # Only actual task outputs recorded under this job may name reader files.
    # Do not glob workspace readers, read another job, or inspect debug traces.
    seen=set()
    for observation in reversed(research.get('observations',[])):
        if observation.get('tool_name')!='task' or observation.get('status')!='success':continue
        try:
            raw=working.read_artifact(job,observation['artifact_id'])
            payload=json.loads(raw['text'])
            if not isinstance(payload,dict):continue
            rid=_reader_id_from_task(payload)
            if not isinstance(rid,str) or not re.fullmatch(r'[a-f0-9]{32}',rid) or rid in seen:continue
            if len(seen)>=3:break
            seen.add(rid)
            path=Path(service.workspace)/'memory/readers'/rid/'navigation.json'
            virtual=_virtual(path,service.workspace)
            if not virtual:continue
            navigation=json.loads(path.read_text())
            if not isinstance(navigation,dict) or navigation.get('reader_id')!=rid or not isinstance(navigation.get('opened_sources'),dict):continue
            for source,row in list(navigation.get('opened_sources',{}).items())[:48]:
                if not isinstance(source,str) or not ID.fullmatch(source) or not isinstance(row,dict):continue
                pool[source]={'record_id':source,'title':str(row.get('title') or ''),'source_status':'opened',
                    'pointer':{'file_path':virtual,'json_pointer':'/opened_sources/'+source}}
        except (ValueError,KeyError,OSError,TypeError):continue
    targets={row['claim_id']:row for row in review.get('targets',[]) if isinstance(row,dict) and row.get('claim_id')}
    failed={row['claim_id']:row for row in review.get('revision_required',[]) if isinstance(row,dict) and row.get('claim_id')}
    cited={rid for target in targets.values() for rid in target.get('evidence_ids',[])}
    by_target={};canonical={}
    for label,finding in failed.items():
        target=targets.get(label,{})
        words=_terms(' '.join(str(target.get(k,'')) for k in ('statement','question','condition'))+' '+str(finding.get('claim_text','')))
        focus=_terms(finding.get('claim_text','')) or words
        bound=set(target.get('evidence_ids',[]));selected=[]
        ranked=sorted(pool.values(),key=lambda row:(-(4*len(focus&_terms(row['title']))+len(words&_terms(row['title']))),row['source_status']!='opened',row['record_id']))
        for row in [row for row in ranked if focus&_terms(row['title']) and row['record_id'] not in bound][:8]:
            rid=row['record_id']
            if rid not in canonical:
                try:
                    original=service.lookup(rid)
                    canonical[rid]=original if isinstance(original,dict) and original.get('record_id')==rid and service._public_judgment_row(original) else None
                except (ValueError,KeyError,OSError):canonical[rid]=None
            original=canonical[rid]
            if not original:continue
            title=str(original.get('title') or row['title'])
            selected.append({'record_id':rid,'title':title[:160],
                **({'title_is_preview':True} if len(title)>160 else {}),
                'published_at':original.get('published_at'),'source_status':row['source_status'],
                'cited_in_report':rid in cited,'bound_to_this_target':False,'pointer':row['pointer'],
                'next_read':{'tool':'read_evidence','arguments':{'record_id':rid}}})
            if len(selected)==3:break
        if selected:by_target[label]=selected
    if not by_target:return None
    packet={'version':'source-repair-navigation-v1','job_id':job,'candidates_by_target':by_target,
        'reader_navigation_count':len(seen),
        'scope':'Task-local lexical title matches only. Recalled is not opened; opened is not support. Relevance and semantic support remain unverified. Inspect the exact original before choosing citations; no source was added to the report or reviewer.'}
    raw=json.dumps(packet,ensure_ascii=False,sort_keys=True).encode();digest=hashlib.sha256(raw).hexdigest()
    review_id=review.get('review_id','')
    if not isinstance(review_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',review_id):return None
    path=Path(service.workspace)/'reviews'/(review_id+'-source-navigation-'+digest[:12]+'.json')
    if not path.resolve().is_relative_to(Path(service.workspace).resolve()):return None
    atomic(path,packet)
    return {'artifact':{'file_path':_virtual(path,service.workspace),'json_pointer':'/candidates_by_target'},
        'candidates_by_target':by_target,'scope':packet['scope']}


def review_process_context(service):
    """Bounded same-job host records of research process, never world evidence."""
    working=getattr(service,'working_memory',None);job=getattr(service,'job_id',None)
    if not working or not job:return None
    try:observations=(working.state(job).get('research_state') or {}).get('observations',[])
    except (KeyError,ValueError,OSError):return None
    packet={'version':'review-process-context-v1','job_id':job,'facts':[],
        'scope':'Recorded task-local research process only; not evidence of world facts or absence of external events.',
        'omitted':False}
    def add(kind,data,pointer):
        fact={'process_id':'P'+str(len(packet['facts'])+1),'kind':kind,'data':data,'pointer':pointer}
        packet['facts'].append(fact)
        if len(json.dumps(packet,ensure_ascii=False))>4000:
            packet['facts'].pop();packet['omitted']=True;return False
        return True
    def canonical(rid):
        if not isinstance(rid,str) or not ID.fullmatch(rid):return None
        try:
            row=service.lookup(rid)
            if not isinstance(row,dict) or row.get('record_id')!=rid or not service._public_judgment_row(row):return None
            url=row.get('url') or (row.get('source') or {}).get('url')
            if not isinstance(url,str) or not url.startswith(('https://','http://')):return None
            return {'record_id':rid,'url':url,'source_kind':str(row.get('source_kind') or '')[:80]}
        except (KeyError,ValueError,OSError,TypeError):return None
    seen=set();readers=0
    for obs in reversed(observations):
        if obs.get('status')!='success' or obs.get('tool_name')!='recall_memory':continue
        try:
            artifact=working.read_artifact(job,obs['artifact_id']);path=_virtual(artifact['path'],service.workspace)
            payload=json.loads(artifact['text'])
            if not isinstance(payload,dict):continue
            coverage=payload.get('candidate_coverage')
            if not path or not isinstance(coverage,dict):continue
            data={k:coverage[k] for k in ('start','end_exclusive','document_count','unique_origin_urls','publisher_host_count','gaps') if k in coverage}
            add('retrieval_coverage',data,{'file_path':path,'json_pointer':'/candidate_coverage'})
            break
        except (KeyError,ValueError,OSError,TypeError):continue
    # Reader IDs are accepted only from successful task results owned by this job.
    for obs in reversed(observations):
        if obs.get('status')!='success' or obs.get('tool_name')!='task':continue
        try:
            payload=json.loads(working.read_artifact(job,obs['artifact_id'])['text'])
            reader=_reader_id_from_task(payload)
            if not isinstance(reader,str) or not re.fullmatch(r'[a-f0-9]{32}',reader) or reader in seen:continue
            if readers>=3:packet['omitted']=True;break
            seen.add(reader);readers+=1
            path=Path(service.workspace)/'memory/readers'/reader/'navigation.json';virtual=_virtual(path,service.workspace)
            if not virtual:continue
            nav=json.loads(path.read_text())
            if not isinstance(nav,dict) or nav.get('reader_id')!=reader or not isinstance(nav.get('opened_sources'),dict):continue
            records=[row for rid in nav['opened_sources'] if (row:=canonical(rid))]
            if records:
                while records and not add('recorded_source_inspection',{'sources':records},
                        {'file_path':virtual,'json_pointer':'/opened_sources'}):records.pop()
        except (KeyError,ValueError,OSError,TypeError):continue
    for obs in reversed(observations):
        if obs.get('status')!='success' or obs.get('tool_name')!='read_evidence':continue
        try:
            artifact=working.read_artifact(job,obs['artifact_id']);path=_virtual(artifact['path'],service.workspace)
            payload=json.loads(artifact['text'])
            if not isinstance(payload,dict):continue
            record=payload.get('record',payload)
            if not isinstance(record,dict) or not path:continue
            row=canonical(record.get('record_id'))
            if row:add('recorded_source_inspection',{'sources':[row]},
                {'file_path':path,'json_pointer':'/record' if 'record' in payload else ''})
        except (KeyError,ValueError,OSError,TypeError):continue
    return packet if packet['facts'] else None
