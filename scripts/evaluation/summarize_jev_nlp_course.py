"""Build report data from immutable judgments without making provider calls."""
import argparse
from collections import Counter
import json
from pathlib import Path

from research_agent.jev_nlp_study import load, _rows, routed, accounting, _calibration, write_json


def summarize(root, output):
    output = Path(output); protocol, data = load(root, output); rows = _rows(output)
    analysis = json.loads((output / 'ANALYSIS.json').read_text()); calibration = _calibration(output)
    cases = [case for case in data['cases'] if case['split'] != 'development']; resources = {}
    policies = ('rules', 'doubao', 'jev', 'classification_only', 'context_only', 'verification_only',
                'confidence_cascade', 'task_selective')
    for policy in policies:
        consumed = []; routes = Counter()
        for case in cases:
            task = case['task']; job = protocol['aliases']['case:' + case['case_id']]
            j = rows['jev'].get(job, {}); p = rows['doubao'].get(job, {})
            if policy == 'rules':
                route = 'rules'; calls = []
            elif policy in ('confidence_cascade', 'task_selective'):
                _, route = routed(task, j, p, calibration, selective=policy == 'task_selective')
                direct_pro = policy == 'task_selective' and task in ('relation', 'support')
                calls = [p] if direct_pro else [j] if route == 'jev' else [j, p]
            else:
                use_jev = (policy == 'jev' or policy == 'classification_only' and task == 'classification'
                    or policy == 'context_only' and task in ('relevance', 'relation')
                    or policy == 'verification_only' and task == 'support')
                route = 'jev' if use_jev else 'doubao'; calls = [j if use_jev else p]
            routes[route] += 1; consumed.extend(calls)
        submitted = [row for row in consumed if row.get('billable_request_made')]
        resources[policy] = {'accounting': accounting(consumed), 'routes': dict(routes),
            'modeled_serial_seconds': sum(row['latency_seconds'] for row in submitted)
                if all(row.get('latency_seconds') is not None and not row.get('interrupted_outcome') for row in submitted) else None,
            'scope': 'Counterfactual cold decision routing on these confirmation cases, without cross-case cache reuse; not an independently timed agent workflow or an additional cash charge.'}
    generation = [json.loads(path.read_text()) for path in (output / 'generation').glob('*.json')]
    timing = [json.loads(path.read_text()) for path in (output / 'timing').glob('*.json')]
    total = {}
    for arm in ('jev', 'doubao'):
        results = [*rows[arm].values(), *[row['cold'] for row in timing if row['provider'] == arm],
                   *[row['warm'] for row in timing if row['provider'] == arm],
                   *[row['result'] for row in generation if arm == 'doubao']]
        total[arm] = accounting(results)
        assert total[arm]['physical_requests'] <= protocol['max_requests_per_provider']
    result = {'schema': 'jev-nlp-report-data-v1', 'analysis': analysis, 'counterfactual_confirmation_resources': resources,
        'total_actual_collection_accounting': total,
        'timing': json.loads((output / 'TIMING_SUMMARY.json').read_text()) if (output / 'TIMING_SUMMARY.json').exists() else None,
        'generation_summary': json.loads((output / 'GENERATION_SUMMARY.json').read_text()) if (output / 'GENERATION_SUMMARY.json').exists() else None,
        'dataset_counts': {'cases': len(data['cases']), 'dimensions': sum(len(case['expected']) for case in data['cases']),
            'singletons': sum(len(labels) == 1 for case in data['cases'] for labels in case['expected'].values()),
            'splits': dict(Counter(case['split'] for case in data['cases']))}, 'provider_calls_from_summarizer': 0}
    write_json(output / 'REPORT_DATA.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); result = summarize(Path(__file__).resolve().parents[2], args.output)
    print(json.dumps({'dataset': result['dataset_counts'], 'actual_accounting': result['total_actual_collection_accounting']}, indent=2))


if __name__ == '__main__':
    main()
