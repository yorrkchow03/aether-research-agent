"""Independent keyword and local-encoder retrieval over a source memory index.

An optional caller-supplied pretrained encoder performs per-document encoding;
this module never downloads or constructs a provider/model. Without it, the
result explicitly reports keyword-only mode. Cached vectors are bound to model
identity, preprocessing identity and exact input content, not a mutable source ID.
"""
from __future__ import annotations

from contextlib import closing
import json
import math
import re
import sqlite3

import numpy as np

from research_data.http import utcnow
from research_data.library.time import timestamp
from .event_memory_index import SCOPE, _sha, _json, _time, _key


class HybridMemoryRetriever:
    def __init__(self, index, encoder=None, encoder_id=None, *, cache_fingerprint=None,
                 rrf_k=60, max_document_chars=24000):
        if encoder is not None and (not isinstance(encoder_id,str) or not 1<=len(encoder_id)<=240):
            raise ValueError('A supplied encoder requires its explicit model/version identity')
        if not 1<=rrf_k<=1000 or not 1000<=max_document_chars<=100000:
            raise ValueError('Invalid hybrid retrieval configuration')
        self.index=index;self.encoder=encoder;self.encoder_id=encoder_id
        self.rrf_k=rrf_k;self.max_document_chars=max_document_chars
        # Include host passage construction even when the encoder supplies its
        # own fingerprint; old whole-document vectors must never masquerade as
        # source-span vectors or survive a changed document coverage bound.
        self.cache_fingerprint=(cache_fingerprint or 'source-text')+':overlap-passages-v1:'+str(max_document_chars)
        self.namespace=_sha({'encoder':encoder_id,'preprocessing':self.cache_fingerprint})
        with self.index.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS embeddings(namespace TEXT NOT NULL,item_id TEXT NOT NULL,content_sha256 TEXT NOT NULL,vector TEXT NOT NULL,digest TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(namespace,item_id,content_sha256))')

    @staticmethod
    def _matrix(value,count):
        matrix=np.asarray(value,dtype=float)
        if matrix.ndim!=2 or matrix.shape[0]!=count or not matrix.shape[1] or matrix.shape[1]>65536 or not np.isfinite(matrix).all():
            raise ValueError('Encoder must return one finite vector per input')
        norms=np.linalg.norm(matrix,axis=1)
        if np.any(norms<=0):raise ValueError('Encoder returned an empty vector')
        return matrix/norms[:,None]

    def _passages(self,source):
        text=source['text'];end=min(len(text),self.max_document_chars)
        for start in range(0,end,1200):
            finish=min(start+1400,end)
            yield {'record_id':source['record_id'],'start':start,'end':finish,
                'sha256':_sha(text[start:finish]),
                'text':(source.get('title') or '')[:600]+'\n'+text[start:finish]}
            if finish==end:break

    def _document_vectors(self,sources):
        passages=[part for source in sources for part in self._passages(source)]
        texts=[part['text'] for part in passages]
        values=[None]*len(passages);missing=[];hits=0
        with self.index.connect() as db:
            for i,(part,text) in enumerate(zip(passages,texts)):
                key=_sha(text)
                row=db.execute('SELECT vector,digest FROM embeddings WHERE namespace=? AND item_id=? AND content_sha256=?',
                    (self.namespace,part['record_id']+':'+str(part['start'])+':'+str(part['end']),key)).fetchone()
                if row:
                    if _sha(row['vector'])!=row['digest']:raise ValueError('Cached memory vector integrity failed')
                    values[i]=json.loads(row['vector']);hits+=1
                else:missing.append(i)
        # Bounded calls keep full-library preparation independent of the
        # encoder's per-call item bound and avoid a giant cold-query request.
        for begin in range(0,len(missing),128):
            batch=missing[begin:begin+128]
            fresh=self._matrix(self.encoder.encode([texts[i] for i in batch]),len(batch))
            with self.index.connect() as db:
                for i,vector in zip(batch,fresh):
                    values[i]=vector.tolist();body=_json(values[i])
                    db.execute('INSERT OR IGNORE INTO embeddings VALUES(?,?,?,?,?,?)',
                        (self.namespace,passages[i]['record_id']+':'+str(passages[i]['start'])+':'+str(passages[i]['end']),_sha(texts[i]),body,_sha(body),_time(utcnow())))
        return self._matrix(values,len(passages)),passages,{'cache_hits':hits,
            'encoded_documents':len({passages[i]['record_id'] for i in missing}),
            'encoded_passages':len(missing),'passage_count':len(passages),
            'encoder_id':self.encoder_id,'cache_fingerprint':self.cache_fingerprint,'cache_namespace':self.namespace,
            'max_document_chars':self.max_document_chars,
            'truncated_documents':sum(len(s['text'])>self.max_document_chars for s in sources),
            'scope':'Exact overlapping source passages; top passage similarity is navigation, not evidential support.'}

    @staticmethod
    def _keyword(sources,query):
        terms=re.findall(r'\w+',query)[:30]
        if not terms:
            ordered=sorted(sources,key=lambda source:(-(timestamp(source.get('published_at') or source.get('acquired_at')).timestamp() if source.get('published_at') or source.get('acquired_at') else 0),source['record_id']))
            return [(source['record_id'],0.0) for source in ordered]
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE VIRTUAL TABLE eligible USING fts5(record_id UNINDEXED,title,text)')
            db.executemany('INSERT INTO eligible VALUES(?,?,?)',[(s['record_id'],s.get('title') or '',s['text']) for s in sources])
            match=' OR '.join('"'+term.replace('"','""')+'"' for term in terms)
            return [(rid,float(score)) for rid,score in db.execute('SELECT record_id,bm25(eligible,0,4,1) FROM eligible WHERE eligible MATCH ? ORDER BY bm25(eligible,0,4,1),record_id',(match,))]

    def query(self,query='',*,cutoff=None,mode='observed',asset='ALL',limit=8,
              source_limit=10000,candidate_limit=60,entity_key=None,event_key=None):
        if not isinstance(query,str) or len(query)>2000 or not 1<=limit<=30 or not limit<=candidate_limit<=200:
            raise ValueError('Invalid hybrid source query')
        packet=self.index.eligible_sources(cutoff=cutoff,mode=mode,asset=asset,source_limit=source_limit)
        sources=packet.pop('sources');by_id={s['record_id']:s for s in sources}
        if entity_key is not None or event_key is not None:
            # These are explicit candidate keys, never inferred event identity.
            cards=self.index.query('',cutoff=packet['as_of'],mode=mode,asset=asset,limit=30,
                entity_key=_key(entity_key) if entity_key is not None else None,
                event_key=_key(event_key) if event_key is not None else None)
            allowed={c['evidence_id'] for c in cards['results']};sources=[s for s in sources if s['record_id'] in allowed]
            by_id={s['record_id']:s for s in sources}
            if cards['matching_card_count']>30:packet['gaps'].append({'reason':'explicit_event_card_cap','used':30})
        keyword=self._keyword(sources,query);dense=[];source_spans={};meta={'cache_hits':0,'encoded_documents':0,'encoder_id':self.encoder_id}
        dense_status='not_configured' if self.encoder is None else 'not_needed_empty_query_or_corpus'
        if self.encoder is not None and sources and query.strip():
            try:
                # Encode/query all eligible indexed sources independently of keyword hits.
                vectors,passages,meta=self._document_vectors(sources)
                q=self._matrix(self.encoder.encode([query]),1)[0]
                if vectors.shape[1]!=len(q):raise ValueError('Query/document encoder dimensions differ')
                scores=vectors@q
                best={}
                for part,score in zip(passages,scores):
                    rid=part['record_id'];score=float(score)
                    best[rid]=max(best.get(rid,-math.inf),score)
                    source_spans.setdefault(rid,[]).append({k:part[k] for k in ('start','end','sha256')}|{'score':score})
                for rid,spans in source_spans.items():
                    source_spans[rid]=sorted(spans,key=lambda s:(-s['score'],s['start']))[:3]
                dense=sorted(best.items(),key=lambda p:(-p[1],p[0]))
                if meta['truncated_documents']:
                    packet['gaps'].append({'reason':'semantic_document_character_cap','documents':meta['truncated_documents'],'used_chars_per_document':self.max_document_chars})
                dense_status='ok'
            except (ValueError,TypeError,RuntimeError,OSError) as exc:
                dense_status='unavailable';packet['gaps'].append({'reason':'semantic_encoder_unavailable','error_type':type(exc).__name__})
        ranks={};totals={}
        for name,ranking in [('keyword',keyword[:candidate_limit]),('dense',dense[:candidate_limit])]:
            for rank,(rid,score) in enumerate(ranking,1):
                ranks.setdefault(rid,{})[name]={'rank':rank,'score':score}
                totals[rid]=totals.get(rid,0.0)+1.0/(self.rrf_k+rank)
        ordered=sorted(totals,key=lambda rid:(-totals[rid],rid))
        links=self.index.eligible_links(list(by_id),cutoff=packet['as_of'],mode=mode)
        dependencies={}
        for link in links:
            if link['predicate'] in ('contradicts','unresolved_conflict','corrects','supersedes','content_revision_of'):
                dependencies.setdefault(link['subject'],set()).add(link['object'])
                dependencies.setdefault(link['object'],set()).add(link['subject'])
        selected=[];visited=set();groups=[];deferred_groups=[]
        for rid in ordered:
            if rid in visited:continue
            group={rid};pending=[rid]
            while pending:
                for other in dependencies.get(pending.pop(),set()):
                    if other not in group:group.add(other);pending.append(other)
            visited.update(group)
            if len(selected)+len(group)>limit:
                if len(group)>1:
                    deferred_groups.append(sorted(group))
                    packet['gaps'].append({'reason':'related_evidence_group_exceeds_limit','source_count':len(group),'evidence_ids':sorted(group)})
                continue
            selected.extend(sorted(group,key=lambda value:(-totals.get(value,0),value)))
            if len(group)>1:groups.append(sorted(group))
        results=[]
        for rid in selected:
            source=by_id[rid]
            results.append({'record_id':rid,'evidence_id':rid,'title':source.get('title') or rid,
                'url':source.get('url') or source.get('source',{}).get('url'),
                'published_at':source.get('published_at'),'first_seen_at':source.get('acquired_at'),
                'retrieved_at':source.get('acquired_at'),'assets':source.get('assets') or ([source['asset']] if source.get('asset') else []),
                'source_kind':source.get('source_kind'),'excerpt':source['text'][:1200],
                'source_text_truncated':len(source['text'])>1200,'source_snapshot_sha256':_sha(source),
                'ranking':{**ranks.get(rid,{}),'rrf_score':totals.get(rid,0.0),'dependency_expansion':rid not in ranks},
                'scope':SCOPE})
            if rid in source_spans:
                results[-1]['semantic_spans']=source_spans[rid]
        return {**packet,'query':query,'results':results,'source_ids':selected,
            'dependencies':{rid:sorted(values) for rid,values in dependencies.items() if rid in selected},
            'protected_groups':groups,'deferred_groups':deferred_groups,'links':[link for link in links if link['subject'] in selected and link['object'] in selected],
            'retrieval_mode':'hybrid_rrf' if dense_status=='ok' else 'keyword_only',
            'keyword_candidates':len(keyword),'dense_candidates':len(dense),'dense_status':dense_status,
            'embedding':meta,'ranked_source_count':len(ordered),'event_equivalence_confirmed':False,
            'scope':SCOPE+' Similarity/rank is not evidential support or source independence.'}
