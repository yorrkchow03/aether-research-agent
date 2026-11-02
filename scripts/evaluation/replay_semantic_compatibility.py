"""Reparse saved responses after a format repair; never reissue provider requests."""
import argparse,hashlib,json,sqlite3
from pathlib import Path
from research_agent.semantic_benchmark_dataset import load_dataset
from research_agent.semantic_benchmark import apply_blind_reference,build_summary
from research_agent.evidence_judgments import validate_answers
from research_agent.semantic_probabilities import VERSION

def replay(study,output):
    study=Path(study);output=Path(output)
    if output.exists():raise ValueError('Preserve prior counterfactual replays')
    seal=json.loads((study/'SEALED.json').read_text())
    for name,want in seal['files'].items():
        if hashlib.sha256((study/name).read_bytes()).hexdigest()!=want:raise ValueError('Sealed first result changed')
    protocol=json.loads((study/'PROTOCOL.json').read_text());data=load_dataset(protocol['dataset'])
    data=apply_blind_reference(data,json.loads(Path(protocol['pre_run_review']).read_text()))
    cases={c['case_id']:c for c in data['cases']};rows=[];changes=[]
    for provider in ('jev','doubao','lexical'):
        for path in sorted((study/'first'/provider).glob('*.json')):
            row=json.loads(path.read_text());old=row['result'];new=dict(old)
            if provider!='lexical':
                with sqlite3.connect((study/('RECEIPTS-'+provider+'.sqlite')).resolve().as_uri()+'?mode=ro',uri=True) as db:
                    found=db.execute('SELECT body FROM attempts WHERE cache_key=? ORDER BY created_at DESC LIMIT 1',(old['cache_key'],)).fetchone()
                receipt=json.loads(found[0]);payload=receipt.get('response')
                if isinstance(payload,dict) and isinstance(payload.get('answers'),dict):
                    try:
                        checked=validate_answers(payload,receipt['judged_input']['questions'],provider)
                        task=cases[row['case_id']]['task'];main=checked['topic' if task=='classification' else task]
                        abstain=main['confidence']<protocol['minimum_confidence'] or main['choice']=='uncertain'
                        new.update(status='abstained' if abstain else 'ok',dimensions=checked,
                            label=None if abstain else main['choice'],confidence=main['confidence'])
                    except ValueError as exc:new.update(status='error',compatibility_error=str(exc))
            if new.get('status')!=old['status'] or {k:v.get('choice') for k,v in (new.get('dimensions') or {}).items()}!={k:v.get('choice') for k,v in (old.get('dimensions') or {}).items()}:
                changes.append({'provider':provider,'case_id':row['case_id'],'before_status':old['status'],'after_status':new['status'],
                    'raw_response_historical':True,'new_provider_requests':0})
            rows.append(row|{'result':new})
    summary=build_summary(protocol,data,rows)
    output.mkdir(parents=True)
    result={'schema':'semantic-format-counterfactual-replay-v1','validation_version':VERSION,
        'changed_cases':changes,'summary':summary,'raw_first_results_changed':False,'new_provider_requests':0,
        'scope':'Post-study format-only reparse. No fresh model decisions or costs; historical usage remains referenced in summary and must not be charged twice. Does not replace first48 metrics.'}
    (output/'RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'changed_cases':changes,'new_provider_requests':0}));return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--study',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);a=parser.parse_args();replay(a.study,a.output)
