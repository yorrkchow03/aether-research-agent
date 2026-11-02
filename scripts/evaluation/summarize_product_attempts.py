"""Recount preserved product attempts without network or model requests."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path


def usage(items):
    result={}
    for key in ('input_tokens','output_tokens','total_tokens'):
        values=[u[key] for u in items if isinstance(u,dict) and isinstance(u.get(key),(int,float))]
        result[key]={'known_items':len(values),'unknown_items':len(items)-len(values),
                     'known_sum':sum(values) if values else None}
    return result


def summarize(study):
    run=json.loads((study/'RUN.json').read_text());base=study/'runtime/sessions'/run['session_id'];rows=[]
    for path in sorted((base/'jobs').glob('*/status.json'),key=lambda p:json.loads(p.read_text())['created_at']):
        status=json.loads(path.read_text());events=[json.loads(line) for line in path.with_name('events.jsonl').read_text().splitlines()]
        if not status.get('finished_at'):raise ValueError('Do not finalize metrics for an active attempt')
        author=[e for e in events if e['kind']=='model_finished']
        summaries=[e for e in events if e['kind']=='context_summary_finished']
        semantic=[e for e in events if e['kind']=='evidence_judgment']
        reviews=[]
        for diagnostic in (base/'workspace/reviews/attempts').glob('*.json'):
            item=json.loads(diagnostic.read_text())
            if status['created_at']<=item.get('reviewed_at','')<=status['finished_at']:reviews.append(item)
        elapsed=(datetime.fromisoformat(status['finished_at'].replace('Z','+00:00'))-
                 datetime.fromisoformat(status['created_at'].replace('Z','+00:00'))).total_seconds()
        rows.append({'job_id':status['job_id'],'state':status['state'],'error':status.get('error'),
            'started_at':status['created_at'],'finished_at':status['finished_at'],'elapsed_seconds':elapsed,
            'author_started_requests':sum(e['kind']=='model_started' for e in events),
            'author_returned_responses':len(author),'author_usage':usage([e.get('usage') for e in author]),
            'context_summary_requests':sum(e['kind']=='context_summary_started' for e in events),
            'context_summary_returned':len(summaries),'context_summary_usage':usage([e.get('usage') for e in summaries]),
            'review_started_requests':sum(e['kind']=='review_started' for e in events),
            'review_saved_responses':len(reviews),'review_usage':usage([e.get('usage') for e in reviews]),
            'semantic_events':len(semantic),'semantic_statuses':dict(Counter(e['status'] for e in semantic)),
            'semantic_tasks':dict(Counter(e['task'] for e in semantic)),
            'semantic_cache_hits':sum(e.get('cache_hit') is True for e in semantic),
            'semantic_reported_usage':usage([e.get('usage') for e in semantic]),
            'actual_author_models':dict(Counter(e.get('actual_model','unknown') for e in author))})
    return {'schema':'halfyear-product-attempts-v1','at':datetime.now(timezone.utc).isoformat(),'attempts':rows,
        'author_started_requests':sum(x['author_started_requests'] for x in rows),
        'author_returned_responses':sum(x['author_returned_responses'] for x in rows),
        'summary_started_requests':sum(x['context_summary_requests'] for x in rows),
        'review_started_requests':sum(x['review_started_requests'] for x in rows),
        'semantic_events':sum(x['semantic_events'] for x in rows),'all_failed_attempts_preserved':True,
        'dollar_cost':None,'scope':'Actual debugged product flow with failed24-call bounds,context setting/source-lineage corrections and subsequent publication. No first-pass success,accuracy,controlled Pro-vs-Jev speedup or unreported-usage-as-zero claim. Offline current publication replay has0newprovider requests and is separate.'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,default=Path('local/halfyear/product'))
    p.add_argument('--output',type=Path);p.add_argument('--compare',type=Path);args=p.parse_args()
    result=summarize(args.study)
    if args.compare:
        expected=json.loads(args.compare.read_text())
        if {k:v for k,v in result.items() if k!='at'}!={k:v for k,v in expected.items() if k!='at'}:
            raise ValueError('Recount differs from the preserved attempt metrics')
        result['reproduces_existing']=True
    if args.output:
        if args.output.exists():raise ValueError('Preserve the existing metrics output')
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('attempts','scope')},ensure_ascii=False))
