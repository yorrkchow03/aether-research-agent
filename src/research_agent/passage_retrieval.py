"""Exact-passage retrieval and optional Jev rank fusion over saved originals.

The ranking unit is a source slice, never a whole-document excerpt bundle.
Scores are navigation heuristics, not calibrated relevance or truth. Canonical
evidence IDs remain source IDs; passage IDs only identify offsets within them.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import math
import re
import sqlite3

from research_data.http import utcnow
from research_data.library.time import timestamp
from .knowledge import identify_assets, news_coverage, source_content_fingerprint


VERSION = 'exact-passage-bm25-potion-rrf-v1'
SCOPE = ('Exact saved-source passages for navigation. Relevance, an embedding '
         'score or a Jev label does not establish source truth or independence.')
_RELATIONS = {'contradicts', 'unresolved_conflict', 'corrects', 'supersedes', 'content_revision_of'}
_STOP = {'a','an','the','this','that','with','from','for','and','or','of','in','on','at','to',
         'is','are','was','were','be','been','as','by','it','its','about','what','which',
         'how','please','give','me','report','research','analyse','analyze'}


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def retrieval_limits(start=None, end=None, limit=None, budget_chars=None):
    """Choose body capacity from task scope, independently of source metadata."""
    if limit is None:
        days = (timestamp(end)-timestamp(start)).total_seconds()/86400 if start and end else 0
        limit = 32 if days >= 21 else 20 if days >= 3 else 12 if days else 8
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 64:
        raise ValueError('Passage limit must be an integer in 1..64')
    if budget_chars is None:
        budget_chars = 1400*limit
    if isinstance(budget_chars, bool) or not isinstance(budget_chars, int) or not 400 <= budget_chars <= 250000:
        raise ValueError('Passage body budget must be 400..250000 characters')
    return limit, budget_chars


def _terms(text):
    # SQLite's unicode tokenizer does not segment an unspaced Chinese query.
    # Index overlapping Han bigrams as an additional deterministic FTS field.
    terms = [term.casefold() for term in re.findall(r'[A-Za-z0-9_]+', text) if term.casefold() not in _STOP]
    for run in re.findall(r'[\u3400-\u9fff]+', text):
        terms.extend(run[i:i+2] for i in range(len(run)-1))
        if len(run) == 1:
            terms.append(run)
    return list(dict.fromkeys(terms))


def _han_terms(text):
    return ' '.join(term for term in _terms(text) if re.search(r'[\u3400-\u9fff]', term))


def _keyword(passages, query):
    terms = _terms(query)[:80]
    if not terms:
        return []
    with closing(sqlite3.connect(':memory:')) as db:
        db.execute('CREATE VIRTUAL TABLE passages USING fts5(passage_id UNINDEXED,title,text,han)')
        db.executemany('INSERT INTO passages VALUES(?,?,?,?)', [
            (p['passage_id'],p.get('title') or '',p['excerpt'],_han_terms((p.get('title') or '')+' '+p['excerpt']))
            for p in passages])
        match = ' OR '.join('"'+term.replace('"','""')+'"' for term in terms)
        return [(pid,float(score)) for pid,score in db.execute(
            'SELECT passage_id,bm25(passages,0,2,1,1) FROM passages WHERE passages MATCH ? '
            'ORDER BY bm25(passages,0,2,1,1),passage_id',(match,))]


def _source_catalog(row):
    source = row.get('source') or {}
    return {k:v for k,v in {
        'record_id': row['record_id'], 'title': row.get('title'),
        'url': row.get('url') or source.get('url'),
        'published_at': row.get('published_at'),
        'retrieved_at': row.get('retrieved_at') or row.get('acquired_at'),
        'first_seen_at': row.get('first_seen_at') or row.get('available_at') or row.get('retrieved_at') or row.get('acquired_at'),
        'source_kind': row.get('source_kind'), 'source_sha256': source.get('sha256'),
        'source_content_sha256': source_content_fingerprint(row),
    }.items() if v is not None}


def _eligible(sources, cutoff, mode, start, end, asset, acquisition_cutoff=None):
    if len(sources)>150:
        raise ValueError('Bound the canonical source pool to 150 before passage retrieval')
    rows, gaps, seen = [], [], {}
    for row in sources:
        rid = row.get('record_id')
        if not isinstance(rid,str) or not rid:
            gaps.append({'reason':'missing_evidence_id'});continue
        if (row.get('private') or rid.startswith('context-') or
                str(row.get('dataset_id','')).startswith(('private.','local.user_','local.portfolio'))):
            gaps.append({'record_id':rid,'reason':'private_context_is_not_public_news'});continue
        if row.get('quarantined') or row.get('exclusion_reasons') or row.get('library_exclusion_reasons') or 'quarantined' in (row.get('quality_flags') or []):
            gaps.append({'record_id':rid,'reason':'currently_quarantined_source'});continue
        text = row.get('text')
        if not isinstance(text,str) or not text.strip():
            gaps.append({'record_id':rid,'reason':'original_body_unavailable'});continue
        assets=row.get('assets') or ([row['asset']] if row.get('asset') else identify_assets(row))
        if asset!='ALL' and asset not in assets:
            gaps.append({'record_id':rid,'reason':'outside_asset_scope'});continue
        try:
            published=timestamp(row['published_at']) if row.get('published_at') else None
            known=[timestamp(value) for value in (
                row.get('first_seen_at'),row.get('available_at'),row.get('retrieved_at'),
                row.get('acquired_at'),(row.get('source') or {}).get('retrieved_at')) if value]
            if published and published>timestamp(cutoff):
                gaps.append({'record_id':rid,'reason':'publication_after_cutoff'});continue
            if mode=='strict' and (not known or max(known)>timestamp(cutoff)):
                gaps.append({'record_id':rid,'reason':'not_known_by_cutoff'});continue
            if acquisition_cutoff and (not known or max(known)>timestamp(acquisition_cutoff)):
                gaps.append({'record_id':rid,'reason':'not_acquired_by_knowledge_cutoff'});continue
            if (start or end) and published is None:
                gaps.append({'record_id':rid,'reason':'publication_time_unknown_for_window'});continue
            if published and ((start and published<timestamp(start)) or (end and published>=timestamp(end))):
                gaps.append({'record_id':rid,'reason':'outside_publication_window'});continue
        except (ValueError,TypeError,OverflowError):
            gaps.append({'record_id':rid,'reason':'invalid_source_time'});continue
        key=(_sha(text),row.get('published_at'),max(known).isoformat() if known else None)
        if rid in seen:
            if seen[rid]!=key:
                raise ValueError('A canonical source ID cannot be rebound to different content or times')
            continue
        seen[rid]=key;rows.append(row)
    return rows,gaps


def _passages(sources, max_chars):
    rows=[]
    for row in sources:
        text=row['text'];end=min(len(text),max_chars);catalog=_source_catalog(row)
        for start in range(0,end,1200):
            finish=min(start+1400,end);excerpt=text[start:finish];digest=_sha(excerpt)
            rows.append({**{k:catalog[k] for k in ('record_id','title','url','published_at','retrieved_at','first_seen_at','source_kind') if k in catalog},
                'passage_id':row['record_id']+':span:'+str(start)+':'+str(finish)+':'+digest[:12],
                'evidence_id':row['record_id'],'excerpt':excerpt,
                'start':start,'end':finish,'sha256':digest,
                'source_slices':[{'start':start,'end':finish,'sha256':digest}],
                'source_content_sha256':catalog['source_content_sha256'],
                'source_sha256':catalog.get('source_sha256'),
                'source_text_truncated':start!=0 or finish!=len(text),
                'source_text_characters':len(text),
                'input_excerpt_sha256':digest,'source_scope':SCOPE})
            if finish==end:break
    return rows


def _nonoverlapping(order, by_id):
    """Suppress duplicate text/high-overlap within a source, never across versions."""
    kept=[];ranges={};texts={}
    for pid in order:
        row=by_id[pid];rid=row['record_id']
        duplicate=row['sha256'] in texts.get(rid,set())
        overlapping=any(max(0,min(row['end'],b)-max(row['start'],a))/min(row['end']-row['start'],b-a)>=.65
                        for a,b in ranges.get(rid,[]))
        if duplicate or overlapping:continue
        kept.append(pid);ranges.setdefault(rid,[]).append((row['start'],row['end']))
        texts.setdefault(rid,set()).add(row['sha256'])
    return kept


def _rerank(order, by_id, query, judge, max_judgments, start, end, research_task=None):
    """RRF only within accepted-label slots; all abstention slots stay fixed."""
    assess=getattr(judge,'assess',judge);receipts=[];accepted=[];relevance_scores={}
    question=query
    if start or end:
        question+='\nPublication window: '+str(start or 'unspecified')+' inclusive to '+str(end or 'unspecified')+' exclusive. Important counterevidence and corrections remain relevant.'
    for pid in order[:max_judgments]:
        row=by_id[pid]
        # Source ID remains canonical. The exact excerpt binds each cache entry
        # independently; passage_id is a navigation identifier, not evidence.
        evidence={**row,'source':{k:v for k,v in {
            'sha256':row.get('source_sha256'),'url':row.get('url')}.items() if v is not None}}
        try:
            arguments={'question':question,'evidence':[evidence],'criterion_version':'passage-window-rank-v1'}
            if research_task is not None:
                from .evidence_judgments import PASSAGE_CRITERION
                arguments.update(question=research_task['original_user_question'],research_task=research_task,
                    retrieval_subquestion=query,criterion_version=PASSAGE_CRITERION)
            result=assess('relevance',**arguments)
            if not isinstance(result,dict):raise ValueError('Invalid judgment envelope')
        except Exception as exc:
            result={'status':'error','task':'relevance','error_type':type(exc).__name__}
        receipt={**result,'passage_id':pid};receipts.append(receipt)
        allowed=('direct_evidence','useful_background','unrelated') if research_task is not None else ('relevant','background','irrelevant')
        if research_task is not None:
            dimensions=result.get('dimensions') or {}
            purpose=dimensions.get('relevance') or {}
            label=purpose.get('choice') or result.get('label')
            row['relevance_hint']={'status':result.get('status'),'label':label,'confidence':result.get('confidence'),
                'subquestion':dimensions.get('subquestion_relevance'),
                'decision':'retain_baseline_uncertain',
                'scope':'Report usefulness and subquestion usefulness are separate; neither certifies truth, priority or complete coverage.'}
            if label=='needs_context':
                row['relevance_hint']['next_read']={'tool':'read_evidence',
                    'arguments':{'record_id':row['record_id'],'full_text':True},
                    'adjacent_range_hint':{'start':max(0,row['start']-600),'end':row['end']+600},
                    'reason':'Read adjacent canonical source text; bounds are navigation hints and must be checked against the original.'}
        if (result.get('status')=='ok' and result.get('label') in allowed and
                isinstance(result.get('confidence'),(int,float)) and not isinstance(result.get('confidence'),bool) and
                math.isfinite(result['confidence']) and 0<=result['confidence']<=1):
            row.setdefault('relevance_hint',{}).update({k:result.get(k) for k in ('status','label','confidence')})
            row['relevance_hint']['scope']='Optional navigation judgment; not a source truth or completeness verdict.'
            if research_task is not None:row['relevance_hint']['decision']='eligible_for_conservative_slot_rerank'
            accepted.append((pid,result['label']))
            probabilities=result.get('scores')
            value=probabilities.get('relevant') if research_task is None and isinstance(probabilities,dict) else None
            if (isinstance(value,(int,float)) and not isinstance(value,bool) and
                    math.isfinite(value) and 0<=value<=1):
                relevance_scores[pid]=float(value)
    label_order=({'direct_evidence':0,'useful_background':1,'unrelated':2} if research_task is not None else
        {'relevant':0,'background':1,'irrelevant':2})
    jev=[]
    for label in sorted(label_order,key=label_order.get):
        group=[item for item in accepted if item[1]==label]
        # Scores are used only to form an ordinal rank among reliable judgments
        # of the same label. Missing scores keep their within-label baseline
        # slots; stable sorting preserves the baseline for equal scores.
        scored=iter(sorted((item for item in group if item[0] in relevance_scores),
                           key=lambda item:-relevance_scores[item[0]]))
        jev.extend(next(scored) if item[0] in relevance_scores else item for item in group)
    jev_rank={pid:i for i,(pid,_) in enumerate(jev,1)}
    base_rank={pid:i for i,(pid,_) in enumerate(accepted,1)}
    scores={pid:1/(60+base_rank[pid])+2/(60+jev_rank[pid]) for pid,_ in accepted}
    reordered=sorted((pid for pid,_ in accepted),key=lambda pid:(-scores[pid],base_rank[pid]))
    slots={pid for pid,_ in accepted};items=iter(reordered)
    final=[next(items) if pid in slots else pid for pid in order]
    return final,receipts,{
        'strategy':'accepted-slot-weighted-rrf', 'formula':'1/(60+baseline_accepted_rank)+2/(60+jev_accepted_rank)',
        'strategy_version':'passage-task-use-slot-rrf-v3' if research_task is not None else 'passage-jev-slot-rrf-v2',
        'label_order':list(label_order),
        'within_label_order':'Baseline order; class utility is not a validated fine-grained priority score' if research_task is not None else 'Descending valid scores.relevant; missing-score slots and equal-score baseline order are retained',
        'accepted_count':len(accepted),'judgment_count':len(receipts),
        'abstained_or_unavailable_count':len(receipts)-len(accepted),
        'unjudged_positions_unchanged':True,
        'scope':'Heuristic ordinal reordering, not calibrated probabilities, source truth or deletion. Jev scores never mix numerically with retrieval scores.',
        'accepted_ranks':[{ 'passage_id':pid,'baseline_accepted_rank':base_rank[pid],
            'jev_label':label,'jev_relevance_score':relevance_scores.get(pid),
            'jev_accepted_rank':jev_rank[pid],'fused_rank_score':scores[pid] } for pid,label in accepted],
    }


def retrieve_passages(sources, query='', *, hybrid=None, limit=None, budget_chars=None,
                      judge=None, max_judgments=32, cutoff=None, mode='observed',
                      start=None,end=None,dependencies=None,relations=None,asset='ALL',research_task=None,acquisition_cutoff=None):
    """Rank independent exact passages; preserve canonical citation provenance.

    ``sources`` are reopened canonical originals, including independent dense
    candidates gathered by the caller. ``hybrid`` reuses its existing local
    Potion encoder and source-span vector cache; no provider is constructed.
    ``budget_chars`` counts only passage body text, never provenance metadata.
    """
    if mode not in ('observed','strict') or asset not in ('ALL','ETH','BTC'):
        raise ValueError('Invalid passage availability/asset mode')
    if not isinstance(query,str) or len(query)>2000 or isinstance(max_judgments,bool) or not isinstance(max_judgments,int) or not 0<=max_judgments<=64:
        raise ValueError('Invalid passage query/judgment bounds')
    cutoff=cutoff or utcnow()
    if start and end and timestamp(start)>=timestamp(end):raise ValueError('Passage start must precede end')
    if end and timestamp(end)>timestamp(cutoff):raise ValueError('Passage end cannot exceed cutoff')
    limit,budget_chars=retrieval_limits(start,end,limit,budget_chars)
    sources=list(sources);original_count=len(sources)
    sources,gaps=_eligible(sources,cutoff,mode,start,end,asset,acquisition_cutoff)
    max_chars=getattr(hybrid,'max_document_chars',100000)
    passages=_passages(sources,max_chars);by_id={p['passage_id']:p for p in passages}
    for row in sources:
        if len(row['text'])>max_chars:
            gaps.append({'record_id':row['record_id'],'reason':'source_character_cap','used_chars':max_chars,'original_chars':len(row['text'])})
    lexical=_keyword(passages,query) if query.strip() and passages else []
    dense=[];embedding={};dense_status='not_configured' if hybrid is None or hybrid.encoder is None else 'not_needed_empty_query_or_corpus'
    if hybrid is not None and hybrid.encoder is not None and sources and query.strip():
        try:
            vectors,parts,embedding=hybrid._document_vectors(sources)
            q=hybrid._matrix(hybrid.encoder.encode([query]),1)[0]
            if vectors.shape[1]!=len(q):raise ValueError('Query/passage vector dimensions differ')
            scores=vectors@q
            offsets={(p['record_id'],p['start'],p['end']):p['passage_id'] for p in passages}
            dense=sorted([(offsets[(p['record_id'],p['start'],p['end'])],float(score)) for p,score in zip(parts,scores)],key=lambda p:(-p[1],p[0]))
            dense_status='ok'
        except (ValueError,TypeError,KeyError,RuntimeError,OSError) as exc:
            dense_status='unavailable';gaps.append({'reason':'semantic_encoder_unavailable','error_type':type(exc).__name__})
    ranks={};totals={};k=getattr(hybrid,'rrf_k',60)
    for name,ranking in (('keyword',lexical),('dense',dense)):
        for rank,(pid,score) in enumerate(ranking,1):
            ranks.setdefault(pid,{})[name]={'rank':rank,'score':score}
            totals[pid]=totals.get(pid,0)+1/(k+rank)
    ordered=sorted(totals,key=lambda pid:(-totals[pid],pid))
    if not query.strip():
        # Empty query means a timeline/source overview, not arbitrary vectors.
        dated=sorted(sources,key=lambda r:(-timestamp(r.get('published_at') or r.get('retrieved_at') or r.get('acquired_at') or cutoff).timestamp(),r['record_id']))
        by_source={r['record_id']:[p['passage_id'] for p in passages if p['record_id']==r['record_id']] for r in dated}
        ordered=[items[i] for i in range(max((len(v) for v in by_source.values()),default=0)) for items in by_source.values() if i<len(items)]
    ordered=_nonoverlapping(ordered,by_id)
    candidate_limit=min(256,max(64,limit*4));at_limit=len(ordered)>candidate_limit
    ordered=ordered[:candidate_limit]
    for rank,pid in enumerate(ordered,1):
        by_id[pid]['ranking']={**ranks.get(pid,{}),'rrf_score':totals.get(pid,0),'baseline_rank':rank}
    base_order=list(ordered);receipts=[];jev_meta={'strategy':'off','judgment_count':0,'accepted_count':0}
    if judge is not None and query.strip() and ordered and max_judgments:
        ordered,receipts,jev_meta=_rerank(ordered,by_id,query,judge,max_judgments,start,end,research_task)
    for rank,pid in enumerate(ordered,1):by_id[pid]['ranking']['final_rank']=rank
    # Source-level relationship closure preserves one best exact passage for
    # every endpoint, without pretending that a relationship is proved by it.
    links={rid:set(ids) for rid,ids in (dependencies or {}).items()}
    for relation in relations or []:
        if relation.get('predicate') not in _RELATIONS:continue
        if acquisition_cutoff:
            try:
                if not relation.get('known_at') or timestamp(relation['known_at'])>timestamp(acquisition_cutoff):continue
            except (ValueError,TypeError,OverflowError):continue
        if mode=='strict':
            try:
                if not relation.get('known_at') or timestamp(relation['known_at'])>timestamp(cutoff):continue
            except (ValueError,TypeError,OverflowError):continue
        left,right=relation.get('subject'),relation.get('object')
        if left and right:
            links.setdefault(left,set()).add(right);links.setdefault(right,set()).add(left)
    for rid,partners in list(links.items()):
        for other in partners:links.setdefault(other,set()).add(rid)
    best={}
    for pid in ordered:best.setdefault(by_id[pid]['record_id'],pid)
    # Dependency endpoints that had no query hit remain available as explicitly
    # linked contrary/correcting context, never as claimed independent matches.
    for p in passages:best.setdefault(p['record_id'],p['passage_id'])
    selected=[];seen=set();body_chars=0;protected_groups=[]
    for pid in ordered:
        if pid in seen:continue
        rid=by_id[pid]['record_id'];group={rid};pending=[rid]
        while pending:
            for other in links.get(pending.pop(),set()):
                if other not in group:group.add(other);pending.append(other)
        if group-set(best):
            gaps.append({'reason':'related_evidence_endpoint_unavailable','evidence_ids':sorted(group)});continue
        addition=[pid]+[best[other] for other in sorted(group-{rid})]
        addition=list(dict.fromkeys(p for p in addition if p not in seen))
        cost=sum(len(by_id[p]['excerpt']) for p in addition)
        if len(selected)+len(addition)>limit or body_chars+cost>budget_chars:
            if len(group)>1:gaps.append({'reason':'related_evidence_group_exceeds_context','evidence_ids':sorted(group)})
            continue
        selected.extend(addition);seen.update(addition);body_chars+=cost
        if len(group)>1 and sorted(group) not in protected_groups:protected_groups.append(sorted(group))
        if len(selected)>=limit:break
    result=[by_id[pid] for pid in selected];source_ids=list(dict.fromkeys(p['record_id'] for p in result))
    irrelevant_selected=sum(p.get('relevance_hint',{}).get('status')=='ok' and
        p.get('relevance_hint',{}).get('label') in ('irrelevant','unrelated') for p in result)
    selection={'algorithm':VERSION,'unit':'exact_source_passage','policy':'passage_hybrid_jev_rank' if receipts else 'passage_hybrid_rrf',
        'candidate_count':len(ordered),'source_candidate_count':original_count,'eligible_source_count':len(sources),
        'eligible_passage_count':len(passages),'candidate_pool_limit':candidate_limit,'candidate_pool_at_limit':at_limit,
        'selected_count':len(result),'selected_source_count':len(source_ids),'limit':limit,
        'effective_limit':limit,'content_characters':body_chars,
        'remaining_ranked_passages':len([pid for pid in ordered if pid not in seen]),
        'more_available':any(pid not in seen for pid in ordered),
        'next_action':'If the question is not covered, increase max_results or issue a focused query for the missing topic/date; a partial result is not complete research.',
        'budget':budget_chars,'budget_chars':budget_chars,'cost_estimate':body_chars,
        'cost_unit':'Unicode characters in exact passage bodies only; metadata excluded; not provider tokens',
        'excluded':gaps,'semantic_calls':len(receipts),'semantic_scores_used':jev_meta['accepted_count'],
        'jev_irrelevant_selected_count':irrelevant_selected,
        'jev_selection_warning':'Slot reranking can retain irrelevant passages. Inspect topic and asset scope; abstention is unknown relevance. Do not treat every selected passage as a report-worthy event.' if irrelevant_selected else None,
        'semantic_request_bound':max_judgments if judge else 0,'jev_reranking':jev_meta,
        'retrieval':'BM25 per exact passage + independent Potion passage cosine, fused by reciprocal rank',
        'empty_query_policy':'date/source breadth; no embedding or Jev call' if not query.strip() else None,
        'baseline_passage_ids':base_order,'ranked_passage_ids':ordered,
        'time_policy':{'publication_start_inclusive':start,'publication_end_exclusive':end,
            'publication_cutoff':cutoff,'acquisition_cutoff':acquisition_cutoff or (cutoff if mode=='strict' else None),
            'mode':mode,'window_clock':'published_at; acquisition time never substitutes for missing publication time',
            'retrospective':mode=='observed'},
        'scope':SCOPE}
    return {'retrieval_unit':'passage','results':result,'source_ids':source_ids,'passage_ids':selected,
        'source_catalog':{row['record_id']:_source_catalog(row) for row in sources if row['record_id'] in source_ids},
        'selection':selection,'gaps':gaps,'as_of':cutoff,'mode':mode,'scope':SCOPE,
        'hybrid_retrieval':{'retrieval_mode':'passage_hybrid_rrf' if dense_status=='ok' else 'passage_keyword_only',
            'dense_status':dense_status,'embedding':embedding,'lexical_passages':len(lexical),'dense_passages':len(dense),
            'eligible_source_count':len(sources),'scope':SCOPE},
        'coverage':news_coverage([row for row in sources if row['record_id'] in source_ids],start=start,end=end),
        'candidate_coverage':news_coverage(sources,start=start,end=end,pool_truncated=at_limit),
        'conflicts':[{'evidence_ids':group,'basis':'explicit_source_or_reviewed_relation','all_endpoints_selected':True,
            'scope':'Originals/qualifications still require reading; selection is not confirmation.'} for group in protected_groups],
        'judgment_receipts':receipts}
