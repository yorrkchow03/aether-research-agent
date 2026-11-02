"""Bounded live harness smoke check. Uses the already-configured local product.

--start creates one research job; later invocations only inspect its saved state.
No keys, private portfolio data, transactions or automatic model retries are used.
"""
import argparse
import json
import re
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]
RECEIPT = ROOT / 'reports/harness_refactor_verification.json'
PROMPT = '''Verify the terminal-based research workflow with one concise sourced answer.
Read the applicable research/quantitative skill instructions. Through execute and
ResearchClient fetch the real current ETH completed daily candles. Write a small
Python recipe under notes which accepts the saved record JSON path in sys.argv[1],
takes the last eight completed daily closes, and prints one JSON values/units/meta
with the seven-period close-to-close return. Register the recipe on its actual
input ID using register_calculation, then submit_research_answer with one numeric
fact and the exact UTC window. Use exact stored units/value. No forecasts, news
search, portfolio access, strategy training or full report are needed. This is a
bounded integration verification, not evidence of investment quality.'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', action='store_true')
    args = parser.parse_args()
    receipt = json.loads(RECEIPT.read_text()) if RECEIPT.exists() else {}
    with httpx.Client(base_url='http://127.0.0.1:8765', timeout=15, trust_env=False) as client:
        config = client.get('/api/config').raise_for_status().json()
        if args.start and not receipt:
            health = client.get('/api/health').raise_for_status().json()
            if health['active_jobs']:
                raise RuntimeError('Research is already active; do not start a duplicate')
            page = client.get('/').raise_for_status().text
            token = json.loads(re.search(r'const TOKEN=("[^"]+")', page).group(1))
            headers = {'X-Local-Token': token}
            session = client.post('/api/sessions', headers=headers,
                json={'title': 'Terminal SDK / source-gate verification'}).raise_for_status().json()
            sid = session['session_id']
            job = client.post(f'/api/sessions/{sid}/messages', headers=headers,
                json={'prompt': PROMPT}).raise_for_status().json()
            receipt = {'session_id': sid, 'job_id': job['job_id'],
                'started_at': job['created_at'], 'software_fingerprint': config['software_fingerprint'],
                'scope': 'Bounded terminal/data/recipe/source-review smoke check, not an investment-quality benchmark'}
            RECEIPT.parent.mkdir(exist_ok=True)
            RECEIPT.write_text(json.dumps(receipt, indent=2) + '\n')
        if not receipt:
            raise RuntimeError('No verification run; explicitly use --start once')
        sid = receipt['session_id']
        snapshot = client.get(f'/api/sessions/{sid}').raise_for_status().json()
        job = next(item for item in snapshot['jobs'] if item['job_id'] == receipt['job_id'])
        events = client.get(f'/api/sessions/{sid}/events').raise_for_status().json()
        names = [item['name'] for item in events if item['kind'] == 'tool_started']
        skills = [item.get('arguments_preview', '') for item in events
                  if item['kind'] == 'tool_started' and item.get('name') == 'read_file'
                  and 'SKILL.md' in item.get('arguments_preview', '')]
        receipt.update(state=job['state'], last_event=job.get('last_event'),
            tool_calls=names, skill_reads=skills,
            actual_models=sorted({item['actual_model'] for item in events
                if item['kind'] == 'model_finished' and item.get('actual_model')}),
            error=job.get('error'), live_server_matches=config['software_fingerprint'] == receipt['software_fingerprint'])
        if job['state'] == 'completed':
            answer = job.get('verified_answer', {})
            required = {'execute', 'sdk:fetch', 'register_calculation', 'submit_research_answer'}
            passed = required.issubset(names) and bool(answer.get('citations'))
            receipt.update(status='passed' if passed else 'failed', answer=job.get('answer'),
                validation=answer.get('validation'), review=answer.get('review'),
                citations=answer.get('citations'), finished_at=job.get('finished_at'))
        else:
            receipt['status'] = 'failed' if job['state'] in ('failed', 'cancelled', 'interrupted') else 'in_progress'
        RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({key: receipt.get(key) for key in
            ('status', 'state', 'session_id', 'job_id', 'last_event', 'tool_calls', 'skill_reads', 'actual_models', 'error')}, indent=2))


if __name__ == '__main__':
    main()
