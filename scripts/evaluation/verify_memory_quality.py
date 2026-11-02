"""Freeze/replay real-source evidence packs, task artifacts and correction receipts.

No model, terminal command within an agent, or data network request is made.
Labels are a small previously inspected diagnostic, not human-adjudicated truth.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import shutil
from pathlib import Path
from time import perf_counter

from research_data.library.store import ResearchLibrary
from research_agent.evidence_memory import EvidenceMemory
from research_agent.experience_memory import ExperienceMemory, seed_project_corrections
from research_agent.working_memory import WorkingMemory
from research_agent.live import atomic

ROOT = Path(__file__).resolve().parents[2]
CODE = ['src/research_agent/evidence_memory.py', 'src/research_agent/memory_selection.py',
        'src/research_agent/knowledge.py', 'src/research_agent/working_memory.py',
        'src/research_agent/experience_memory.py']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(output):
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a new diagnostic output; do not overwrite frozen inputs')
    library = ResearchLibrary(ROOT/'data/datasets/aether-month-20260926-20261026-v4', create=False)
    known = 'page-546a7ebd8e4cad2fd9923418'
    cases = [
        {'case_id': 'hoodi_status', 'query': 'Glamsterdam Hoodi upgrade postponed replacement activation date',
         'required_id': known, 'required_passage': 'A new activation date has not been set.'},
        {'case_id': 'hoodi_mainnet_scope', 'query': 'Glamsterdam Hoodi Ethereum mainnet activation date action ETH holders',
         'required_id': known, 'required_passage': 'It does not set a Glamsterdam mainnet activation date'},
        {'case_id': 'fomc_release_meeting', 'query': 'FOMC minutes October 7 September 15 16 meeting release',
         'required_id': 'page-797f9989a5d3cf326d2fe503', 'required_passage': 'meeting that was held on September 15–16, 2026.'},
    ]
    output.mkdir(parents=True)
    frozen = []
    for case in cases:
        packet = library.search_documents(case['query'], mode='observed', limit=60)
        candidates = [library.evidence(row['record_id']) for row in packet['results']]
        required = library.evidence(case['required_id'])
        if case['required_passage'] not in required['text']:
            raise ValueError('Required literal passage changed: ' + case['case_id'])
        frozen.append({**case, 'candidates': candidates,
                       'required_source_in_recalled_pool': any(r['record_id']==case['required_id'] for r in candidates)})
    atomic(output/'cases.json', frozen)
    manifest = {'schema': 'evidence-memory-diagnostic-v1', 'window_end': library.window_end,
        'cases_sha256': sha(output/'cases.json'), 'source_code': {n: sha(ROOT/n) for n in CODE},
        'source_dataset_manifest_sha256': sha(library.root/'DATASET_MANIFEST.json'),
        'budget': 3200, 'limit': 5,
        'scope': 'Previously inspected real monthly sources; literal source/passage recall and resource diagnostics, not human semantic labels or a held-out generalization study.',
        'variants': ['relevance', 'mmr', 'coverage', 'prefix_mmr'],
        'model_calls': 0, 'data_network_calls': 0, 'Jev_effect': 'Not measured by this deterministic run.'}
    atomic(output/'manifest.json', manifest)
    for name in CODE:
        target=output/'source_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/name,target)
    return manifest


def evaluate(output, replay=False):
    output = Path(output)
    manifest = json.loads((output/'manifest.json').read_text())
    if sha(output/'cases.json') != manifest['cases_sha256']:
        raise ValueError('Frozen candidate pool changed')
    for name, expected in manifest['source_code'].items():
        if sha(ROOT/name) != expected:
            raise ValueError('Memory source changed; freeze a new version: ' + name)
    cases = json.loads((output/'cases.json').read_text())
    rows = []
    for case in cases:
        for mode in ('observed', 'strict'):
            for variant in manifest['variants']:
                candidates = deepcopy(case['candidates'])
                if variant == 'prefix_mmr':
                    for candidate in candidates:
                        candidate['text'] = candidate.get('text', '')[:1600]
                before = perf_counter()
                pack = EvidenceMemory(None).pack(case['query'], candidates=candidates,
                    mode=mode, cutoff=manifest['window_end'], budget=manifest['budget'], limit=manifest['limit'],
                    policy='mmr' if variant=='prefix_mmr' else variant, semantic_mode='off')
                rows.append({'case_id': case['case_id'], 'mode': mode, 'variant': variant,
                    'required_source_in_recalled_pool': case['required_source_in_recalled_pool'],
                    'required_source_selected': case['required_id'] in pack['source_ids'],
                    'required_passage_selected': any(r['record_id']==case['required_id'] and case['required_passage'] in r['excerpt'] for r in pack['results']),
                    'selected_ids': pack['source_ids'], 'context_characters': len(pack['prompt_context']),
                    'estimated_tokens': pack['selection']['cost_estimate'],
                    'future_captures_selected': sum(r.get('first_seen_at','')>manifest['window_end'] for r in pack['results']) if mode=='strict' else None,
                    'selection_latency_seconds': perf_counter()-before, 'gaps': pack['gaps']})
    summary = {'cases': rows, 'model_calls': 0, 'data_network_calls': 0,
        'semantic_correctness': None, 'human_adjudication': 'pending', 'variants': {}}
    for variant in manifest['variants']:
        observed=[r for r in rows if r['mode']=='observed' and r['variant']==variant]
        strict=[r for r in rows if r['mode']=='strict' and r['variant']==variant]
        summary['variants'][variant]={'observed_questions':len(observed),
            'required_source_recall':sum(r['required_source_selected'] for r in observed),
            'required_passage_recall':sum(r['required_passage_selected'] for r in observed),
            'strict_future_capture_leaks':sum(r['future_captures_selected'] for r in strict),
            'mean_context_characters':sum(r['context_characters'] for r in observed)/len(observed),
            'mean_estimated_tokens':sum(r['estimated_tokens'] for r in observed)/len(observed),
            'mean_selection_latency_seconds':sum(r['selection_latency_seconds'] for r in observed)/len(observed)}
    if replay:
        original=json.loads((output/'results.json').read_text())
        stable=lambda result:[{k:v for k,v in row.items() if k!='selection_latency_seconds'} for row in result['cases']]
        if stable(summary)!=stable(original):
            raise ValueError('Offline memory replay changed selection/resource results')
        receipt={'passed':True,'case_variant_mode_comparisons':len(rows),'model_calls':0,'data_network_calls':0,
                 'scope':'Exact selected IDs,passage flags,context size,estimated cost,gaps and strict-time replay; measured timing excluded.'}
        atomic(output/'REPLAY.json',receipt)
        return receipt
    atomic(output/'results.json',summary)
    experience=ExperienceMemory(output/'experience')
    seeded=seed_project_corrections(ROOT,experience)
    recall=experience.recall('return calculation report date',limit=5,budget_chars=5000)
    working=WorkingMemory(output/'working')
    working.begin('real-source-diagnostic','Research ETH evidence without dumping the full archive.',manifest['window_end'])
    tool=working.record_tool('real-source-diagnostic','library candidate response',cases)
    view=working.context('real-source-diagnostic',budget_chars=2000)
    atomic(output/'STATE_RECEIPT.json',{'seeded_real_process_corrections':seeded,'recalled_corrections':len(recall['results']),
        'correction_prompt_characters':recall['characters'],'saved_tool_output_bytes':tool['bytes'],
        'working_context_characters':view['characters'],'full_tool_output_hash_verified':working.read_artifact('real-source-diagnostic',tool['artifact_id'])['sha256']==tool['sha256'],
        'no_tool_output_automatically_promoted_to_fact':working.state('real-source-diagnostic')['facts']==[],
        'model_calls':0,'data_network_calls':0})
    return {k:summary[k] for k in ('model_calls','data_network_calls','variants')}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','run','replay'])
    parser.add_argument('--output',type=Path,default=ROOT/'local/halfyear/memory/pilot-v1')
    args=parser.parse_args()
    print(json.dumps(prepare(args.output) if args.action=='prepare' else evaluate(args.output,args.action=='replay'),indent=2))
