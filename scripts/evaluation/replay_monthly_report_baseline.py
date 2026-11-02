"""Recompute frozen first-pass source/number scores without model/network calls."""
import argparse
import json
from pathlib import Path

from research_agent.monthly_report_benchmark import DiagnosticReport, digest, load, score
from research_agent.live import atomic

ROOT = Path(__file__).resolve().parents[2]


def replay(output):
    output = Path(output)
    config, records, cases = load(output)
    original_code = output / 'software' / config['software_fingerprint'][:24] / 'src/research_agent/monthly_report_benchmark.py'
    if digest(original_code) != digest(ROOT / 'src/research_agent/monthly_report_benchmark.py'):
        raise ValueError('Scorer changed; use the original pinned scorer for an exact replay')
    compared, failures = [], []
    for case in cases:
        directory = output / case['case_id']
        result = json.loads((directory / 'result.json').read_text())
        selected = {records[key]['record_id']: records[key] for key in case['sources']
                    if records[key]['record_id'] in case['eligible_record_ids']}
        if selected != json.loads((directory / 'selected_evidence.json').read_text()):
            raise ValueError('Selected source bytes changed: ' + case['case_id'])
        if result['state'] != 'completed':
            failures.append({'case_id': case['case_id'], 'state': result['state'], 'error': result.get('error')})
            continue
        report = DiagnosticReport.model_validate_json((directory / 'report.json').read_text())
        if score(report, case, selected) != result['score']:
            raise ValueError('Score replay differs: ' + case['case_id'])
        compared.append(case['case_id'])
    receipt = {'passed': True, 'model_calls': 0, 'data_network_calls': 0,
        'planned_cases': len(cases), 'completed_scores_compared': compared,
        'original_failures_preserved': failures, 'scorer_source_unchanged': True,
        'semantic_correctness': None, 'scope': 'Exact replay of original frozen mechanical scores; no new generation or human semantic label.'}
    atomic(output.parent / 'REPLAY.json', receipt)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'local/report-baseline/monthly-v1/frozen')
    print(json.dumps(replay(parser.parse_args().output), indent=2))
