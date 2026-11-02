"""Report real requests separately from cache replay and prepare human labels."""
import argparse
import json
from pathlib import Path

from research_agent.live import atomic
from research_agent.evidence_judgments import EvidenceJudgmentService
from run_jev_evidence_pilot import load

ROOT = Path(__file__).resolve().parents[2]


def review(original, followup):
    original, followup = Path(original), Path(followup)
    protocol, records, cases = load(original)
    _, newer_records, newer_cases = load(followup)
    if records != newer_records or cases != newer_cases:
        raise ValueError('Different source/question bytes cannot be paired')
    pairs=[]; human=[]
    for case in cases:
        jev=json.loads((original/'results/jev'/(case['case_id']+'.json')).read_text())['result']
        pro=json.loads((followup/'results/doubao'/(case['case_id']+'.json')).read_text())['result']
        if jev['input_hash'] != pro['input_hash']:
            raise ValueError('Semantic inputs differ: '+case['case_id'])
        prior=original/'results/doubao'/(case['case_id']+'.json')
        if prior.exists():
            raw_pro=json.loads(prior.read_text())['result']
            pro_latency=raw_pro['latency_seconds'];pro_usage=raw_pro.get('usage')
            normalization='Original root-map Pro response deterministically normalized; first format failure remains recorded.'
        else:
            pro_latency=pro['latency_seconds'];pro_usage=pro.get('usage');normalization=None
        pair={'case_id':case['case_id'],'task':case['task'],'input_hash':jev['input_hash'],
            'jev':{'status':jev['status'],'label':jev['label'],'model':jev['model'],
                'confidence':jev['confidence'],'latency_seconds':jev['latency_seconds'],'usage':jev.get('usage')},
            'doubao':{'status':pro['status'],'label':pro['label'],'model':pro['model'],
                'confidence':pro['confidence'],'latency_seconds':pro_latency,'usage':pro_usage,
                'transport_adaptation':normalization,'error':pro.get('error')},
            'semantic_accuracy':None,'human_adjudication':'pending'}
        pairs.append(pair)
        human.append({'case_id':case['case_id'],'task':case['task'],'question':case['question'],
            'sources':[{k:row.get(k) for k in ['record_id','title','text','published_at','retrieved_at','source']}
                       for row in [records[key] for key in case['sources']]],
            'human_label':None,'acceptable_labels':[],'reviewer':None,'rationale':'','status':'pending',
            'instructions':'Judge supplied sources directly before looking at provider predictions or provisional labels. Include abstention/insufficient when warranted.'})
    result={'schema':'jev-controlled-source-comparison-v1','paired_cases':len(pairs),
        'first_provider_requests':{'jev':12,'doubao':12},'new_jev_requests_in_followup':0,
        'jev_real_request_wall_seconds':sum(p['jev']['latency_seconds'] for p in pairs),
        'doubao_real_request_wall_seconds':sum(p['doubao']['latency_seconds'] for p in pairs),
        'jev_reported_input_tokens':sum((p['jev']['usage'] or {}).get('input_tokens',0) for p in pairs),
        'jev_reported_output_tokens':sum((p['jev']['usage'] or {}).get('output_tokens',0) for p in pairs),
        'doubao_usage':None,'doubao_usage_explanation':'All12 actual responses omitted token usage, including explicit stream_usage requests. Unknown is not zero.',
        'semantic_accuracy':None,'human_adjudication':'pending','dollar_costs':None,'pairs':pairs,
        'limitations':['Small sequential diagnostic, not statistical latency/significance or investment performance evidence.',
            'Original Pro first3 format failures retained; later format prompt differs and3 outputs are wrapped deterministically without semantic edits.',
            'Jev raw distributions and LLM self-reported distributions are not comparable calibrated correctness scores.',
            'Provisional labels are assistant-authored and ambiguous topic/update cases are explicitly retained.',
            'One Pro conditional choice conflicts with its own maximum probability; it remains an error, never repaired by guessing.',
            'The lexical overlap service is a diagnostic only, not the existing stronger memory-selection baseline.']}
    atomic(followup/'CONTROLLED_COMPARISON.json',result)
    template=followup/'human_labels.jsonl'
    if not template.exists():template.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in human))
    lines=['# Jev evidence pilot review','','Real frozen publisher material;24 first provider requests total. No human semantic accuracy has been assigned.','',
        f"- Jev:12 typed responses,10 usable judgments and2 low-confidence abstentions; actual model jev-1.14.0. Sequential request wall time {result['jev_real_request_wall_seconds']:.2f}s.",
        f"- Pro:three original shape failures normalized offline,then nine remaining requests;11 valid judgments and one self-inconsistent probability/choice error retained. Actual model doubao-seed-2-1-pro-260915; real request wall time {result['doubao_real_request_wall_seconds']:.2f}s.",
        f"- Jev provider-reported input/output tokens:{result['jev_reported_input_tokens']}/{result['jev_reported_output_tokens']}. Pro usage is unknown. No dollar-cost or accuracy comparison is claimed.",
        '- All12 normalized source/question input hashes match across the two providers. Follow-up reused Jev cache; its near-zero replay latency must not be used as model-inference latency.','',
        '| Case | Jev | Pro |','|---|---|---|']
    for p in pairs:lines.append(f"| {p['case_id']} | {p['jev']['status']} / {p['jev']['label']} | {p['doubao']['status']} / {p['doubao']['label']} |")
    lines+=['','## Human labels','','Fill human_labels.jsonl directly against the supplied originals before reading model predictions. A source can be relevant because it contradicts a question premise. Event updates and different time/scope are not automatically factual contradictions. Probabilities/label agreement are not truth.',
        '', '## Open issues','','Security-vs-upgrade dominant-topic labeling is ambiguous; single-topic classification should not silently discard a security article. Future monitoring criteria produced Jev abstention and a Pro inconsistent choice/probability response. These are preserved cases for human adjudication and later criteria revision,not reasons to remove evidence or bypass publication review.']
    (followup/'REVIEW.md').write_text('\n'.join(lines)+'\n')
    return {k:result[k] for k in ['paired_cases','first_provider_requests','new_jev_requests_in_followup','jev_real_request_wall_seconds','doubao_real_request_wall_seconds','jev_reported_input_tokens','jev_reported_output_tokens','doubao_usage']}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--original',type=Path,default=ROOT/'local/halfyear/jev/pilot-v1');p.add_argument('--followup',type=Path,default=ROOT/'local/halfyear/jev/pilot-v2');a=p.parse_args();print(json.dumps(review(a.original,a.followup),indent=2))
