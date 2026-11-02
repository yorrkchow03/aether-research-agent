"""One saved-experiment advice check; no new quotes, search, training or ledger reads."""
import argparse
import json
from pathlib import Path
import re
import httpx

ROOT = Path(__file__).resolve().parents[2]
RECEIPT = ROOT / 'reports/product_evolution/live_experience.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--start', action='store_true')
    args = parser.parse_args()
    receipt = json.loads(RECEIPT.read_text()) if RECEIPT.exists() else {}
    with httpx.Client(base_url='http://127.0.0.1:8765', trust_env=False, timeout=20) as client:
        if args.start and not receipt:
            prior = json.loads((RECEIPT.parent / 'live_advice.json').read_text())
            if prior['status'] != 'passed':raise ValueError('Complete the initial advice check first')
            if client.get('/api/health').raise_for_status().json()['active_jobs']:raise ValueError('Research is already active')
            runs = client.get('/api/strategies').raise_for_status().json()['items']
            run = next((r for r in runs if r['state'] == 'completed' and r['config']['asset'] == 'ETH'), None)
            if not run:raise ValueError('No saved ETH experiment; do not invent or train one for this check')
            prompt = f'''Use only the earlier saved report evidence and the already
completed ETH strategy experiment {run['run_id']}. Inspect its saved status/result
through execute/ResearchClient strategy-status, then read its canonical evidence.
Do not fetch new prices, search, train or read private holdings. Submit a short
English source-checked answer with advice, using one historical_case for this
actual saved experiment. Set case_end from the actual completed outcome window,
availability observed_history, and explain similarities, material differences
and limits of transferring the result to today's monitoring question. Include
one rationale, one condition, one counterpoint, one invalidation and a review
trigger, all with real evidence. A weak retrospective model result is evidence
for caution, not a recommendation to deploy it. Do not invent personal context,
profitability, win rates, thresholds or causal links. Use at most one numeric
fact copied exactly from its saved metric/value/unit. This is one bounded source
and experience integration check, not a new strategy study or investment order.'''
            html = client.get('/').raise_for_status().text
            token = json.loads(re.search(r'const TOKEN=("[^"]+")', html).group(1))
            job = client.post('/api/sessions/' + prior['session_id'] + '/messages',
                headers={'X-Local-Token': token}, json={'prompt': prompt}).raise_for_status().json()
            receipt = {'session_id': prior['session_id'], 'job_id': job['job_id'],
                       'strategy_run_id': run['run_id'], 'started_at': job['created_at']}
            RECEIPT.write_text(json.dumps(receipt, indent=2) + '\n')
        if not receipt:raise ValueError('Use --start once')
        snap = client.get('/api/sessions/' + receipt['session_id']).raise_for_status().json()
        job = next(r for r in snap['jobs'] if r['job_id'] == receipt['job_id'])
        trace = ROOT / 'runtime/sessions' / receipt['session_id'] / 'jobs' / receipt['job_id'] / 'events.jsonl'
        events = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
        answer = job.get('verified_answer')
        receipt.update(state=job['state'], last_event=job.get('last_event'), error=job.get('error'),
            tool_calls=[e['name'] for e in events if e['kind'] == 'tool_started'],
            actual_models=sorted({e['actual_model'] for e in events if e.get('actual_model')}))
        if answer:
            cases = answer['draft'].get('advice', {}).get('historical_cases', [])
            receipt.update(answer=answer, status='passed' if cases and answer['review']['status'] == 'reviewed' else 'incomplete')
        else:receipt['status'] = 'failed' if job['state'] in ('failed', 'cancelled', 'interrupted') else 'in_progress'
        RECEIPT.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + '\n')
        print(json.dumps({k: receipt.get(k) for k in ('status', 'state', 'last_event', 'tool_calls', 'error')}, indent=2))


if __name__ == '__main__':main()
