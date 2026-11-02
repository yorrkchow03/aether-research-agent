"""Start once or inspect one real product monthly report; no automatic retries."""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / 'local/report-baseline/monthly-v1/product.json'


def inspect(output, start=False):
    output = Path(output)
    receipt = json.loads(output.read_text()) if output.exists() else {}
    protocol = json.loads((ROOT / 'examples/evaluation/monthly_report_cases_v1.json').read_text())
    with httpx.Client(base_url='http://127.0.0.1:8765', timeout=20, trust_env=False) as client:
        config = client.get('/api/config').raise_for_status().json()
        if config['model_id'] != 'doubao-seed-2.1-pro' or not config['base_url'].endswith('/api/plan/v3'):
            raise ValueError('Only the configured authorized Pro Plan is allowed')
        if start and not receipt:
            if client.get('/api/health').raise_for_status().json()['active_jobs']:
                raise ValueError('Active product research; do not start a duplicate')
            token = json.loads(re.search(r'const TOKEN=("[^"]+")', client.get('/').text).group(1))
            headers = {'X-Local-Token': token}
            session = client.post('/api/sessions', headers=headers,
                json={'title': 'Monthly library v4 / real report baseline'}).raise_for_status().json()
            receipt = {'session_id': session['session_id'], 'software_fingerprint': config['software_fingerprint'],
                'model_id': config['model_id'], 'created_at': session['created_at'],
                'prompt': protocol['product_prompt'], 'scope': 'Actual product workflow pilot; external access discouraged by prompt, not technically disabled. Independently audit cited timestamps and archive membership.'}
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(receipt, indent=2) + '\n')
            job = client.post(f"/api/sessions/{session['session_id']}/messages", headers=headers,
                json={'prompt': protocol['product_prompt'], 'deliverable': 'report'}).raise_for_status().json()
            receipt.update(job_id=job['job_id'], started_at=job['created_at'])
            output.write_text(json.dumps(receipt, indent=2) + '\n')
        if not receipt:
            raise ValueError('Use --start once to authorize a new model job')
        snapshot = client.get(f"/api/sessions/{receipt['session_id']}").raise_for_status().json()
        jobs = snapshot['jobs']
        if not receipt.get('job_id'):
            if len(jobs) != 1:
                raise ValueError('Start response unavailable; inspect saved session, never post a duplicate')
            receipt.update(job_id=jobs[0]['job_id'], started_at=jobs[0]['created_at'])
        job = next(j for j in jobs if j['job_id'] == receipt['job_id'])
        directory = ROOT / 'runtime/sessions' / receipt['session_id'] / 'jobs' / job['job_id']
        events = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()] if (directory / 'events.jsonl').exists() else []
        calls = [e for e in events if e['kind'] == 'model_finished']
        receipt.update(state=job['state'], last_event=job.get('last_event'), error=job.get('error'),
            author_calls=len(calls), actual_models=sorted({e['actual_model'] for e in calls if e.get('actual_model')}),
            author_requests_started=sum(e['kind'] == 'model_started' for e in events),
            responses_reporting_usage=sum(bool(e.get('usage')) for e in calls),
            author_usage={k: sum((e.get('usage') or {}).get(k, 0) or 0 for e in calls)
                          if any((e.get('usage') or {}).get(k) is not None for e in calls) else None
                          for k in ('input_tokens', 'output_tokens', 'total_tokens')},
            tool_calls=[e.get('name') for e in events if e['kind'] == 'tool_started'],
            startup_source_matches=config['software_fingerprint'] == receipt['software_fingerprint'],
            reports=job.get('published_reports', []), finished_at=job.get('finished_at'))
        # Review request receipts include formatting repairs, unlike final-only usage events.
        workspace = ROOT / 'runtime/sessions' / receipt['session_id'] / 'workspace'
        reviews = [json.loads(p.read_text()) for p in (workspace / 'reviews/attempts').glob('*.json')]
        started = datetime.fromisoformat(job['created_at'].replace('Z', '+00:00'))
        finished = datetime.fromisoformat(job['finished_at'].replace('Z', '+00:00')) if job.get('finished_at') else None
        reviews = [r for r in reviews if r.get('reviewed_at') and
                   datetime.fromisoformat(r['reviewed_at'].replace('Z', '+00:00')) >= started and
                   (finished is None or datetime.fromisoformat(r['reviewed_at'].replace('Z', '+00:00')) <= finished)]
        receipt['review_calls'] = len(reviews)
        receipt['review_requests_started'] = sum(e['kind'] == 'review_started' for e in events)
        receipt['review_responses_reporting_usage'] = sum(bool(r.get('usage')) for r in reviews)
        receipt['review_usage'] = {k: sum((r.get('usage') or {}).get(k, 0) or 0 for r in reviews)
                                   if any((r.get('usage') or {}).get(k) is not None for r in reviews)
                                   else (None if reviews else 0)
                                   for k in ('input_tokens', 'output_tokens', 'total_tokens')}
        receipt['dollar_cost'] = None
        output.write_text(json.dumps(receipt, indent=2) + '\n')
        return {k: receipt.get(k) for k in ('session_id', 'job_id', 'state', 'last_event', 'author_calls', 'review_calls', 'error', 'reports')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', action='store_true')
    parser.add_argument('--output', type=Path, default=DEFAULT)
    args = parser.parse_args()
    print(json.dumps(inspect(args.output, args.start), indent=2))
