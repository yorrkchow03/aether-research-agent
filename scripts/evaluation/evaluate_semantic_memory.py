"""Offline memory navigation on the separately frozen semantic-study sources.

Uses the real EvidenceMemory/ResearchTools selectors. No provider sidecars,
generated answers, source downloads, or financial-quality claims.
"""
import argparse,hashlib,json,tempfile,time
from pathlib import Path
from threading import RLock
from datetime import timedelta
from research_agent.semantic_benchmark_dataset import load_dataset
from research_agent.knowledge import KnowledgeStore
from research_agent.event_memory_index import EventMemoryIndex
from research_agent.hybrid_memory import HybridMemoryRetriever
from research_agent.local_memory_encoder import LocalMemoryEncoder
from research_agent.evidence_memory import EvidenceMemory
from research_agent.tools import ResearchTools
from research_data.library.time import timestamp

def evaluate(dataset,encoder_path,output):
    data=load_dataset(dataset);output=Path(output)
    if output.exists():raise ValueError('Preserve prior memory diagnostics')
    output.mkdir(parents=True);rows=[];time_checks=[]
    encoder=LocalMemoryEncoder(encoder_path)
    with tempfile.TemporaryDirectory(prefix='aether-semantic-memory-') as directory:
        root=Path(directory);knowledge=KnowledgeStore(root/'knowledge')
        for original in data['originals'].values():knowledge.register(original)
        index=EventMemoryIndex(root/'index',source_lookup=knowledge.get,source_lookup_many=knowledge.get_many)
        for original in data['originals'].values():index.upsert_source(original['record_id'])
        retriever=HybridMemoryRetriever(index,encoder,encoder.model_id,cache_fingerprint=encoder.fingerprint+':'+encoder.preprocessing,max_document_chars=100000)
        services={}
        for policy in ('lexical','passage_hybrid'):
            service=ResearchTools.__new__(ResearchTools)
            service.knowledge=knowledge;service.mode='observed';service.cutoff='2026-10-28T23:59:59Z'
            service.workspace=root/policy;service.workspace.mkdir();service.evidence_memory=EvidenceMemory(knowledge)
            service.retrieval_judgments=None;service.report_judgments=None;service.judgment_mode='off'
            service.lock=RLock();service.used_records=set();service.library=None;service.allow_live=True
            service.memory_retriever=retriever if policy=='passage_hybrid' else None
            services[policy]=service
        for i,query in enumerate(data['memory_queries']):
            target=data['sources'][query['source_key']]['original_record_id'];quote=query['required_span']['text']
            for policy in (('lexical','passage_hybrid') if i%2==0 else ('passage_hybrid','lexical')):
                start=time.monotonic();packet=services[policy].news_pack(query=query['query'],asset='ALL',limit=4,budget=3600,related_queries=[])
                elapsed=time.monotonic()-start
                for selected in packet['results']:
                    text=knowledge.get(selected['record_id'])['text']
                    for span in selected['source_slices']:
                        assert hashlib.sha256(text[span['start']:span['end']].encode()).hexdigest()==span['sha256']
                rows.append({'query_id':query['query_id'],'split':query['split'],'event_family':query['event_family'],
                    'policy':policy,'seconds':elapsed,'target_source_selected':target in packet['source_ids'],
                    'target_exact_span_in_pack':any(row['record_id']==target and quote in row['excerpt'] for row in packet['results']),
                    'context_bytes':len(json.dumps(packet['results'],ensure_ascii=False).encode()),'packet':packet})
            original=knowledge.get(target);known=original.get('available_at') or original.get('retrieved_at')
            before=(timestamp(known)-timedelta(microseconds=1)).isoformat()
            p=retriever.query(query['query'],cutoff=before,mode='strict',asset='ALL',limit=4)
            time_checks.append({'query_id':query['query_id'],'target_excluded':target not in p['source_ids']})
        summary={p:{'planned_queries':len(data['memory_queries']),
            'source_hits':sum(row['target_source_selected'] for row in rows if row['policy']==p),
            'exact_span_hits':sum(row['target_exact_span_in_pack'] for row in rows if row['policy']==p),
            'context_bytes':sum(row['context_bytes'] for row in rows if row['policy']==p),
            'latency_seconds_sum':sum(row['seconds'] for row in rows if row['policy']==p)} for p in services}
    receipt={'status':'completed','dataset_manifest_sha256':hashlib.sha256((Path(dataset)/'MANIFEST.json').read_bytes()).hexdigest(),
        'original_sources':len(data['originals']),'models':{'local_encoder':encoder.model_id,'generator':None},
        'same_result_limit':4,'same_context_budget':3600,'policies':summary,'source_time_checks':time_checks,
        'per_query':rows,'provider_calls':0,'network_requests':0,'human_labels':0,
        'scope':'Fixed-source memory navigation and exact-span retention; no Jev reranker, generated-report quality or investment accuracy measured.'}
    load_dataset(dataset)
    (output/'RESULT.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    return receipt

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--encoder',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    r=evaluate(a.dataset,a.encoder,a.output);print(json.dumps({'policies':r['policies'],'strict_checks_passed':sum(c['target_excluded'] for c in r['source_time_checks']),'provider_calls':0}))
if __name__=='__main__':main()
