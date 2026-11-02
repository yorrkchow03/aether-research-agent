"""One opt-in live plan-first check; later calls only inspect that same job.

Uses the already-configured local Pro/Plan service. No credentials are exported.
"""
import argparse
import json
from pathlib import Path
import re

import httpx

from verify_harness import PROMPT


ROOT = Path(__file__).resolve().parents[2]
RECEIPT = ROOT / 'reports/planning/live_verification.json'


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
            if config['model_id'] != 'doubao-seed-2.1-pro' or config['base_url'] != 'https://ark.cn-beijing.volces.com/api/plan/v3':
                raise RuntimeError('Expected the authorized Pro/Plan configuration')
            page = client.get('/').raise_for_status().text
            token = json.loads(re.search(r'const TOKEN=("[^"]+")', page).group(1))
            headers = {'X-Local-Token': token}
            session = client.post('/api/sessions', headers=headers, json={'title': 'Plan-first research verification'}).raise_for_status().json()
            job = client.post(f"/api/sessions/{session['session_id']}/messages", headers=headers, json={'prompt': PROMPT}).raise_for_status().json()
            receipt = {'session_id': session['session_id'], 'job_id': job['job_id'],
                       'started_at': job['created_at'], 'software_fingerprint': config['software_fingerprint'],
                       'scope': 'One real Pro plan-first research task, not a research-quality or investment benchmark'}
            RECEIPT.parent.mkdir(parents=True, exist_ok=True)
            RECEIPT.write_text(json.dumps(receipt, indent=2) + '\n')
        if not receipt:
            raise RuntimeError('No verification run; explicitly use --start once')
        sid = receipt['session_id']
        snapshot = client.get(f'/api/sessions/{sid}').raise_for_status().json()
        job = next(item for item in snapshot['jobs'] if item['job_id'] == receipt['job_id'])
        events = client.get(f'/api/sessions/{sid}/events').raise_for_status().json()
        # The session is created solely for this one task; events contain no other jobs.
        saved = [item for item in events if item['kind'] == 'plan_saved']
        names = [item['name'] for item in events if item['kind'] == 'tool_started']
        research = [item for item in events if item['kind'] == 'tool_started' and item['name'] in {
            'execute', 'request_public_api', 'search_web', 'fetch_webpage', 'recall_memory',
            'read_evidence', 'register_calculation', 'submit_research_answer', 'publish_report'}]
        ordered = bool(saved and research and saved[0]['time'] < research[0]['time'])
        receipt.update(state=job['state'], last_event=job.get('last_event'),
            initial_plan=saved[0] if saved else None,
            plan_updates=[item for item in events if item['kind'] == 'plan_updated'],
            blocked_calls=[item for item in events if item['kind'] == 'planning_required'],
            plan_precedes_research=ordered, tool_calls=names,
            actual_models=sorted({item['actual_model'] for item in events if item['kind'] == 'model_finished' and item.get('actual_model')}),
            error=job.get('error'), live_server_matches=config['software_fingerprint'] == receipt['software_fingerprint'])
        if job['state'] == 'completed':
            answer = job.get('verified_answer', {})
            passed = ordered and {'execute', 'sdk:fetch', 'register_calculation', 'submit_research_answer'}.issubset(names) and bool(answer.get('citations'))
            receipt.update(status='passed' if passed else 'failed', answer=job.get('answer'),
                validation=answer.get('validation'), review=answer.get('review'),
                citations=answer.get('citations'), finished_at=job.get('finished_at'))
        else:
            receipt['status'] = 'failed' if job['state'] in ('failed', 'cancelled', 'interrupted') else 'in_progress'
        RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({key: receipt.get(key) for key in ('status', 'state', 'session_id', 'job_id',
            'last_event', 'plan_precedes_research', 'tool_calls', 'actual_models', 'error')}, indent=2))


if __name__ == '__main__':
    main()
