"""Predeclare a small real-source Jev/Pro evidence study, then run or replay it.

Provisional assistant-authored labels are not human semantic ground truth.
All first attempts, abstentions and provider failures remain visible.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from research_agent.evidence_judgments import EvidenceJudgmentService, QUESTIONS, canonical, normalize_pro_answers, validate_answers
from research_agent.live import atomic
from research_data.http import utcnow

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / 'local/halfyear/jev/pilot-v1'
SNAPSHOT = ROOT / 'data/datasets/aether-month-20260926-20261026-v4'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_record(record_id):
    connection = sqlite3.connect('file:'+str(SNAPSHOT/'research.sqlite')+'?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute('SELECT * FROM document_versions WHERE record_id=?', (record_id,)).fetchone()
        if row is None:
            raise ValueError('Frozen source not found: '+record_id)
        capture = connection.execute('SELECT * FROM captures WHERE capture_id=?', (row['capture_id'],)).fetchone()
        body = json.loads(row['body'])
        raw = SNAPSHOT / 'objects' / capture['sha256'][:2] / (capture['sha256']+'.bin')
        if digest(raw) != capture['sha256']:
            raise ValueError('Original source hash changed')
        body.update(record_id=record_id, source={'name': 'Captured publisher original',
            'url': capture['url'], 'sha256': capture['sha256'], 'retrieved_at': capture['retrieved_at']})
        body['published_at'] = row['published_at']; body['retrieved_at'] = row['known_at']
        return body
    finally:
        connection.close()


def prepare(output):
    output = Path(output)
    if output.exists():
        raise ValueError('Preserve previous study: use a new output directory')
    records = {
        'upgrade_new': source_record('page-546a7ebd8e4cad2fd9923418'),
        'upgrade_old': source_record('page-9794078ccdabb208adf9ff50'),
        'fed_release': source_record('page-797f9989a5d3cf326d2fe503'),
        'security': source_record('page-aa462b13efa46cad1dc70adf'),
    }
    cases = [
        {'case_id': 'class-upgrade', 'task': 'classification', 'question': 'Classify this original announcement.', 'sources': ['upgrade_new'], 'provisional_expected': ['network_upgrade']},
        {'case_id': 'class-macro', 'task': 'classification', 'question': 'Classify the original public release.', 'sources': ['fed_release'], 'provisional_expected': ['macro']},
        {'case_id': 'class-security', 'task': 'classification', 'question': 'Classify the original article.', 'sources': ['security'], 'provisional_expected': ['security']},
        {'case_id': 'relevant-upgrade', 'task': 'relevance', 'question': 'What is the current status of the previously scheduled Glamsterdam activation on Hoodi according to this captured material?', 'sources': ['upgrade_new'], 'provisional_expected': ['relevant']},
        {'case_id': 'irrelevant-macro', 'task': 'relevance', 'question': 'Does this source establish whether Glamsterdam activated on the Hoodi testnet?', 'sources': ['fed_release'], 'provisional_expected': ['irrelevant']},
        {'case_id': 'contrary-still-relevant', 'task': 'relevance', 'question': 'Since the October 26 Hoodi activation was confirmed, which client versions should operators use?', 'sources': ['upgrade_new'], 'provisional_expected': ['relevant']},
        {'case_id': 'relation-schedule', 'task': 'relation', 'question': 'How does the postponement update relate to the original Hoodi announcement? The captured original announcement already has a later update banner; do not pretend it is an authenticated old pre-correction version.', 'sources': ['upgrade_old', 'upgrade_new'], 'provisional_expected': ['supersedes', 'update', 'duplicate'], 'rubric_note': 'Ambiguous after-update original capture; preserve labels for manual review, not a decisive single-label accuracy item.'},
        {'case_id': 'relation-unrelated', 'task': 'relation', 'question': 'Do these two documents describe the same event, or a correction to it?', 'sources': ['upgrade_new', 'fed_release'], 'provisional_expected': ['unrelated', 'different_scope']},
        {'case_id': 'support-postponed', 'task': 'support', 'question': 'The official Hoodi update states that the previously scheduled upgrade was postponed and no replacement activation date had been set.', 'sources': ['upgrade_new'], 'provisional_expected': ['supported']},
        {'case_id': 'support-chain-history', 'task': 'support', 'question': 'The postponement update reverses Hoodi chain history.', 'sources': ['upgrade_new'], 'provisional_expected': ['contradicted']},
        {'case_id': 'support-price-causation', 'task': 'support', 'question': 'This Hoodi postponement caused the observed monthly decline in ETH price.', 'sources': ['upgrade_new'], 'provisional_expected': ['insufficient']},
        {'case_id': 'support-conditional', 'task': 'support', 'question': 'Prospective research criterion only, not an occurred fact: if Ethereum later announces a replacement Hoodi activation date, revisit the current no-replacement-schedule assumption.', 'sources': ['upgrade_new'], 'provisional_expected': ['conditional', 'supported'], 'rubric_note': 'Check treatment of explicitly prospective monitoring; semantic adjudication pending.'},
    ]
    output.mkdir(parents=True)
    atomic(output/'evidence.json', records); atomic(output/'cases.json', cases)
    protocol = {'schema': 'jev-real-evidence-pilot-v1', 'created_at': utcnow(), 'cases': len(cases),
        'providers': ['jev', 'doubao', 'lexical'], 'maximum_first_provider_requests': 24,
        'criterion_version': 'halfyear-evidence-pilot-v1', 'questions': QUESTIONS,
        'files': {n: digest(output/n) for n in ['evidence.json', 'cases.json']},
        'source_dataset_manifest_sha256': digest(SNAPSHOT/'DATASET_MANIFEST.json'),
        'implementation_sha256': digest(ROOT/'src/research_agent/evidence_judgments.py'),
        'label_status': 'Provisional assistant-authored expectations, not independently human-adjudicated gold.',
        'policy': ['Same serialized normalized input_hash across Jev/Pro, same question criteria and source bytes.',
            'No new data network, future outcome tools, private holdings or user facts are supplied.',
            'Original context limits and post-correction capture caveats are explicit.',
            'First attempts retained; only bounded transport retry, no correctness-driven repair.',
            'Provider probabilities/LLM self-reported scores are not project accuracy.',
            'Classification, conflict and source-support components remain optional shadow services.',
            'Latency and known token usage include failed attempts; subscription dollar cost unknown.']}
    atomic(output/'protocol.json', protocol)
    (output/'implementation.py.txt').write_bytes((ROOT/'src/research_agent/evidence_judgments.py').read_bytes())
    return protocol


def load(output):
    output = Path(output); protocol = json.loads((output/'protocol.json').read_text())
    for name, expected in protocol['files'].items():
        if digest(output/name) != expected:
            raise ValueError('Frozen input file changed: '+name)
    if protocol['questions'] != QUESTIONS:
        raise ValueError('Question criteria changed after preparation')
    return protocol, json.loads((output/'evidence.json').read_text()), json.loads((output/'cases.json').read_text())


def summary(output):
    output = Path(output); protocol, _, cases = load(output)
    results = []
    for provider in protocol['providers']:
        for case in cases:
            p = output/'results'/provider/(case['case_id']+'.json')
            if p.exists():
                results.append(json.loads(p.read_text()))
    groups = {}
    for provider in protocol['providers']:
        rows = [r for r in results if r['provider'] == provider]
        actual_calls = [r for r in rows if r['result']['billable_request_made']]
        groups[provider] = {'planned': len(cases), 'attempted': len(rows),
            'ok': sum(r['result']['status']=='ok' for r in rows),
            'abstained': sum(r['result']['status']=='abstained' for r in rows),
            'errors_or_unavailable': sum(r['result']['status'] in ('error','unavailable') for r in rows),
            'actual_models': sorted({r['result']['model'] for r in rows if r['result'].get('model')}),
            'cache_hits': sum(r['result']['cache_hit'] for r in rows),
            'provider_requests_attempted_this_phase': len(actual_calls),
            'latency_seconds': sum(r['result']['latency_seconds'] for r in rows),
            'reported_usage': {k: sum((r['result'].get('usage') or {}).get(k, 0) or 0 for r in actual_calls)
                if any((r['result'].get('usage') or {}).get(k) is not None for r in actual_calls) else (None if actual_calls else 0)
                for k in ('input_tokens','output_tokens','total_tokens')},
            'usage_reporting_provider_responses': sum(bool(r['result'].get('usage')) for r in actual_calls),
            'provider_requests_with_unreported_usage': sum(not bool(r['result'].get('usage')) for r in actual_calls),
            'provisional_label_matches': sum(r['result'].get('label') in r['provisional_expected'] for r in rows),
            'semantic_accuracy': None, 'human_adjudication': 'pending', 'dollar_cost': None}
    paired = []
    for case in cases:
        pair = [r for r in results if r['case_id']==case['case_id'] and r['provider'] in ('jev','doubao')]
        if len(pair)==2:
            same = pair[0]['result']['input_hash']==pair[1]['result']['input_hash']
            if not same:
                raise ValueError('Provider input mismatch: '+case['case_id'])
            paired.append(case['case_id'])
    result = {'schema': 'jev-evidence-pilot-summary-v1', 'providers': groups,
        'paired_same_input_case_ids': paired, 'common_denominator': len(paired),
        'both_ok_case_ids': [case for case in paired if all(r['result']['status']=='ok' for r in results if r['case_id']==case and r['provider'] in ('jev','doubao'))],
        'semantic_accuracy': None, 'human_adjudication': 'pending', 'results': results,
        'scope': 'Small predeclared same-input diagnostic, not a statistically powered or human-gold accuracy benchmark. Provisional label agreement is not accuracy.'}
    atomic(output/'summary.json', result)
    return result


def run(output):
    output = Path(output); protocol, records, cases = load(output)
    if digest(ROOT/'src/research_agent/evidence_judgments.py') != protocol['implementation_sha256']:
        raise ValueError('Implementation changed; prepare a new study')
    for provider in protocol['providers']:
        service = EvidenceJudgmentService(ROOT, output/'cache'/provider, provider)
        provider_errors = 0
        for case in cases:
            file = output/'results'/provider/(case['case_id']+'.json')
            if file.exists():
                continue
            if provider_errors >= 3:
                break
            result = service.assess(case['task'], question=case['question'],
                evidence=[records[k] for k in case['sources']], criterion_version=protocol['criterion_version'])
            record = {'case_id': case['case_id'], 'task': case['task'], 'provider': provider,
                'provisional_expected': case['provisional_expected'], 'result': result,
                'semantic_correctness': None, 'human_adjudication': 'pending'}
            atomic(file, record)
            if result['status'] in ('error','unavailable'):
                provider_errors += 1
            summary(output)
            print(canonical({'provider':provider,'case_id':case['case_id'],'status':result['status'],
                             'label':result['label'],'model':result['model']}), flush=True)
    return summary(output)


def replay(output):
    output = Path(output); protocol, records, cases = load(output)
    checked = []
    for provider in protocol['providers']:
        service = EvidenceJudgmentService(ROOT, output/'cache'/provider, provider, cache_only=True)
        for case in cases:
            file = output/'results'/provider/(case['case_id']+'.json')
            if not file.exists():
                continue
            old = json.loads(file.read_text())['result']
            if old['status'] not in ('ok','abstained') or not service.receipt(old['cache_key']):
                continue
            result = service.assess(case['task'], question=case['question'], evidence=[records[k] for k in case['sources']], criterion_version=protocol['criterion_version'])
            for key in ['status','label','labels','scores','model','input_hash','source_hashes']:
                if result[key] != old[key]:
                    raise ValueError('Offline replay changed '+provider+'/'+case['case_id']+'/'+key)
            if not result['cache_hit'] or result['billable_request_made']:
                raise ValueError('Replay was not a local cached result')
            checked.append(provider+'/'+case['case_id'])
    receipt = {'passed':True,'cached_judgments_compared':checked,'provider_requests':0,
               'semantic_accuracy':None,'scope':'Offline comparison of cached normalized original judgments, not independent semantic labeling.'}
    atomic(output/'REPLAY.json', receipt)
    return receipt


def continue_format(previous, output):
    """Reuse Jev cache and map existing Pro JSON; never rewrite first failures."""
    previous, output = Path(previous), Path(output)
    old_protocol, _, cases = load(previous)
    protocol = prepare(output)
    if protocol['files'] != old_protocol['files']:
        raise ValueError('The follow-up must use identical source/question bytes')
    for provider in protocol['providers']:
        old = previous/'cache'/provider/'judgments.sqlite'
        if old.exists():
            new = output/'cache'/provider/'judgments.sqlite'; new.parent.mkdir(parents=True,exist_ok=True)
            a=sqlite3.connect('file:'+str(old.resolve())+'?mode=ro',uri=True); b=sqlite3.connect(new)
            a.backup(b); a.close(); b.close()
    service = EvidenceJudgmentService(ROOT,output/'cache/doubao','doubao')
    mappings=[]
    for case in cases:
        file=previous/'results/doubao'/(case['case_id']+'.json')
        if not file.exists():continue
        original=json.loads(file.read_text()); old=original['result']
        if old['status']!='error' or old.get('error')!='Missing typed answer map':continue
        receipt=service.receipt(old['cache_key']); response=receipt['response']
        raw={k:v for k,v in response.items() if k not in ('model','usage')}
        normalized,mapping=normalize_pro_answers(raw,QUESTIONS[case['task']])
        normalized.update(model=response['model'],usage=response.get('usage'))
        checked=validate_answers(normalized,QUESTIONS[case['task']],'doubao')
        main=checked['topic' if case['task']=='classification' else case['task']]
        low=main['confidence']<service.minimum_confidence or main['choice']=='uncertain'
        result={**old,'status':'abstained' if low else 'ok','label':None if low else main['choice'],
            'labels':[key+':'+answer['choice'] for key,answer in checked.items() if answer['confidence']>=service.minimum_confidence and answer['choice']!='uncertain'],
            'scores':main['probabilities'],'confidence':main['confidence'],'model':response['model'],
            'dimensions':checked,'usage':response.get('usage'),'original_created_at':old['created_at'],'created_at':utcnow(),
            'response_adaptation':mapping,'posthoc_format_normalization':True}
        result.pop('error',None)
        fixed={'result':result,'judged_input':receipt['judged_input'],'request':receipt['request'],
            'response':normalized,'raw_pro_response':raw,'transport_attempts':receipt['transport_attempts'],
            'follow_up':'Deterministic shape adapter only; no new model output or selected probability was generated.'}
        service._save(old['cache_key'],result,fixed,cache=True)
        mappings.append({'case_id':case['case_id'],'original_failure_preserved':str(file),
                         'mapping':mapping,'provider_requests':0,'original_unknown_usage_preserved':response.get('usage')})
    atomic(output/'FORMAT_FOLLOWUP.json',{'original_study':str(previous),'same_source_and_case_hashes':True,
        'mappings':mappings,'new_provider_requests':0,'scope':'Post-pilot adapter repair, not a change to original first-pass success rates.'})
    return {'prepared':str(output),'offline_pro_responses_adapted':len(mappings),'new_provider_requests':0}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('action', choices=['prepare','run','summary','replay','continue-format']); p.add_argument('--output', type=Path, default=DEFAULT);p.add_argument('--previous',type=Path)
    args = p.parse_args()
    result = continue_format(args.previous,args.output) if args.action=='continue-format' else {'prepare': prepare, 'run': run, 'summary': summary, 'replay': replay}[args.action](args.output)
    print(json.dumps({k:v for k,v in result.items() if k not in ('results','policy','questions')},indent=2))
