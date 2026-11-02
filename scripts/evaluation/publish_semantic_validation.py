"""Publish reviewed numeric study statistics to the local read-only product view."""
import argparse,hashlib,json
from pathlib import Path
from research_agent.semantic_benchmark_dataset import load_dataset

ROOT=Path(__file__).resolve().parents[2]
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--study',type=Path,required=True)
    parser.add_argument('--state-dir',type=Path,default=ROOT/'runtime');parser.add_argument('--diagnostics',type=Path)
    a=parser.parse_args();study=a.study.resolve();protocol=json.loads((study/'PROTOCOL.json').read_text());seal=json.loads((study/'SEALED.json').read_text())
    for name,want in seal['files'].items():
        p=(study/name).resolve()
        if not p.is_relative_to(study) or hashlib.sha256(p.read_bytes()).hexdigest()!=want:raise ValueError('First results changed')
    data=load_dataset(protocol['dataset']);summary=json.loads((study/'SUMMARY.json').read_text())
    providers={}
    for name,value in summary['providers'].items():
        # Lexical overlap only implements relevance, not classification,
        # relationships, instruction detection or support. Its all-task score
        # would be an unfair headline comparison; retain it in offline results.
        if name not in ('jev','doubao'):continue
        providers[name]={k:value[k] for k in ['planned','attempted','ok','abstained','errors','planned_dimensions','scored_dimensions','ambiguous_dimensions',
            'matching_dimensions','usable_dimensions','ai_label_agreement','usable_coverage','latency_seconds','usage','cost','tasks']}
        providers[name]['splits']={'dev':value['splits']['development'],'confirmation':value['splits']['confirmation']}
    case_counts=data['protocol']['splits'];representative=summary['providers']['jev']
    public={'schema':'semantic-validation-public-v1','created_at':summary['created_at'],
        'dataset':{'cases':len(data['cases']),'dev_cases':case_counts['development'],'confirmation_cases':case_counts['confirmation'],
            'event_families':len({c['event_family'] for c in data['cases']}),'manifest_sha256':protocol['dataset_manifest_sha256'],
            **{k:representative[k] for k in ['planned_dimensions','scored_dimensions','ambiguous_dimensions']}},
        'models':{'jev':protocol['models']['jev'],'doubao':protocol['models']['doubao']},
        'review':{'status':'independent_ai_reviewed','reviewer_kind':'independent_ai','reviewed_cases':len(data['cases']),'human_labels':0},
        'providers':providers}
    if a.diagnostics:
        diagnostics=json.loads(a.diagnostics.read_text())
        public['repairs']=diagnostics.get('repairs',{})
    output=a.state_dir/'semantic-validation/latest.json';output.parent.mkdir(parents=True,exist_ok=True)
    tmp=output.with_suffix('.json.tmp');tmp.write_text(json.dumps(public,indent=2)+'\n');tmp.replace(output)
    print(json.dumps({'status':'published_local_statistics','cases':len(data['cases']),'provider_requests':0,'path':str(output)}))
if __name__=='__main__':main()
