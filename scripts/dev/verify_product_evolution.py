"""One bounded live Pro research/advice report, then inspect without restarting.

Uses existing local configuration. Never writes holdings, starts experiments,
places orders or repeats a completed/failed run automatically.
"""
import argparse
import json
from pathlib import Path
import re
import httpx

ROOT = Path(__file__).resolve().parents[2]
RECEIPT = ROOT / 'reports/product_evolution/live_advice.json'
PROMPT = '''Create and publish one concise English ETH monitoring brief to verify
the evidence-linked suggestion workflow. Use the actual local Deep Agents tools.
Read applicable skills. Autonomously choose a small useful current market input
through execute/ResearchClient and, if useful, one saved/reproducible calculation.
Perform one live news search and inspect one relevant original source. Recall
earlier saved news/research when useful; read original saved evidence, not only
a prior report conclusion. Keep this bounded: no more than two market requests,
one search, two source fetches, three observations and one interpretation.

Include the optional advice object with a short summary, general scope, one
source-linked rationale, one applicable condition, one material counterpoint,
one invalidation/review condition, missing information and a review trigger.
If an actual earlier saved source provides a useful comparison, include one
historical_case with UTC case_end, material similarities/differences and transfer
limitations. A source captured after the historical event is observed_history,
not point_in_time. Do not claim an investment success rate or observed price
effect. If no appropriate analogy exists, state that gap and omit it.

We have not supplied personal holdings or a loss limit. Do not inspect private
portfolio data, train models or run strategy experiments. Do not invent numeric
trading thresholds, buy/sell certainty or a causal price claim. Numeric fields
must use saved metric/value/unit. Treat announcement plans and completed outcomes
separately. Ordinary monitoring actions can be conditional reasoning. Publish
through the normal source-review gate, retaining any rejected draft. This is a
bounded integration check, not evidence of profitable advice.'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', action='store_true')
    args = parser.parse_args()
    receipt = json.loads(RECEIPT.read_text()) if RECEIPT.exists() else {}
    with httpx.Client(base_url='http://127.0.0.1:8765', trust_env=False, timeout=20) as client:
        config = client.get('/api/config').raise_for_status().json()
        if args.start and not receipt:
            assert config['model_id'] == 'doubao-seed-2.1-pro'
            assert config['base_url'].endswith('/api/plan/v3')
            if client.get('/api/health').raise_for_status().json()['active_jobs']:
                raise ValueError('Existing research is active; do not start a duplicate')
            html = client.get('/').raise_for_status().text
            token = json.loads(re.search(r'const TOKEN=("[^"]+")', html).group(1))
            headers = {'X-Local-Token': token}
            session = client.post('/api/sessions', headers=headers,
                json={'title': 'Evidence-linked suggestion verification', 'research_mode': 'current'}).raise_for_status().json()
            sid = session['session_id']
            job = client.post('/api/sessions/' + sid + '/messages', headers=headers,
                json={'prompt': PROMPT, 'deliverable': 'report'}).raise_for_status().json()
            receipt = {'session_id': sid, 'job_id': job['job_id'], 'started_at': job['created_at'],
                'software_fingerprint': config['software_fingerprint'],
                'scope': 'Bounded real harness/data/search/advice/source-review integration, not investment quality'}
            RECEIPT.parent.mkdir(parents=True, exist_ok=True)
            RECEIPT.write_text(json.dumps(receipt, indent=2) + '\n')
        if not receipt:raise ValueError('Explicitly use --start once to create the verification')
        snapshot = client.get('/api/sessions/' + receipt['session_id']).raise_for_status().json()
        job = next(r for r in snapshot['jobs'] if r['job_id'] == receipt['job_id'])
        trace = ROOT / 'runtime/sessions' / receipt['session_id'] / 'jobs' / receipt['job_id'] / 'events.jsonl'
        events = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
        names = [r['name'] for r in events if r['kind'] == 'tool_started']
        receipt.update(state=job['state'], last_event=job.get('last_event'), tool_calls=names,
            actual_models=sorted({r['actual_model'] for r in events if r.get('actual_model')}),
            error=job.get('error'), live_server_matches=config['software_fingerprint'] == receipt['software_fingerprint'])
        model_events = [row for row in events if row['kind'] == 'model_finished']
        receipt['author_usage'] = {name: sum(row.get('usage', {}).get(name, 0) or 0 for row in model_events)
                                  for name in ('input_tokens', 'output_tokens', 'total_tokens')}
        receipt['author_calls'] = len(model_events)
        receipt['usage_scope'] = 'Measured author usage only; reviewer/search usage and currency cost are not inferred'
        receipt['finished_at'] = job.get('finished_at')
        report = None
        for meta in client.get('/api/reports').raise_for_status().json()['items']:
            if meta['session_id'] == receipt['session_id']:
                report = client.get('/api/sessions/' + receipt['session_id'] + '/artifact',
                    params={'path': '/reports/' + meta['report_id'] + '.json'}).raise_for_status().json()
                receipt['report_id'] = meta['report_id'];break
        if report:
            advice = report['report'].get('advice')
            passed = bool(advice) and report['review']['status'] == 'reviewed' and {'execute', 'search_web', 'fetch_webpage', 'publish_report'}.issubset(names)
            receipt.update(status='passed' if passed else 'incomplete', advice=advice,
                review=report['review'], validation=report['validation'], report=report)
        else:receipt['status'] = 'failed' if job['state'] in ('failed', 'cancelled', 'interrupted') else 'in_progress'
        RECEIPT.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + '\n')
        print(json.dumps({k: receipt.get(k) for k in ('status', 'state', 'session_id', 'job_id', 'last_event', 'tool_calls', 'actual_models', 'report_id', 'error')}, indent=2))


if __name__ == '__main__':main()
