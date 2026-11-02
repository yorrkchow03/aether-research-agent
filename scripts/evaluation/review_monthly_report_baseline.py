"""Build a reproducible review packet; semantic judgments remain human-pending."""
import argparse
import hashlib
import json
import re
from pathlib import Path

from research_agent.monthly_report_benchmark import load
from research_agent.live import atomic

ROOT = Path(__file__).resolve().parents[2]
ISO_DATE = re.compile(r'(?<!\d)20\d{2}-\d{2}-\d{2}(?!\d)')


def date_flags(report, input_packet):
    """Review candidates only: a proposed future date need not be a factual error."""
    mentioned = set(ISO_DATE.findall(json.dumps(report, ensure_ascii=False)))
    supplied = set(ISO_DATE.findall(json.dumps(input_packet, ensure_ascii=False)))
    return sorted(mentioned - supplied)


def submission(directory):
    file = directory / 'report.json'
    if file.exists():
        return json.loads(file.read_text()), True
    messages = directory / 'messages.json'
    if not messages.exists():
        return None, False
    for message in reversed(json.loads(messages.read_text())):
        for call in message.get('data', {}).get('tool_calls', []):
            if call.get('name') == 'submit_benchmark_report':
                arguments = call.get('args', {})
                if isinstance(arguments, dict) and isinstance(arguments.get('draft'), dict):
                    return arguments['draft'], False
    return None, False


def review(output):
    output = Path(output).resolve()
    frozen = output / 'frozen'
    config, _, cases = load(frozen)
    summary = json.loads((frozen / 'summary.json').read_text())
    automatic, human = [], []
    for case in cases:
        directory = frozen / case['case_id']
        result = json.loads((directory / 'result.json').read_text())
        raw, schema_valid = submission(directory)
        packet = json.loads((directory / 'input.json').read_text())
        item = {'case_id': case['case_id'], 'state': result['state'], 'schema_valid': schema_valid,
            'iso_dates_absent_from_supplied_context': date_flags(raw or {}, packet),
            'date_flag_scope': 'Mechanical review candidates; not automatic semantic errors.',
            'structured_numeric_contract_failures': [c for c in result.get('score', {}).get('claim_checks', [])
                if c['numeric'] and not c['exact_value_and_unit']],
            'semantic_correctness': None, 'human_adjudication': 'pending'}
        automatic.append(item)
        human.append({'case_id': case['case_id'], 'question': case['question'],
            'report': raw, 'schema_valid': schema_valid,
            'selected_evidence_file': case['case_id'] + '/selected_evidence.json',
            'automatic_review_flags': item,
            'human_review': {'reviewer': None, 'factual_accuracy': None, 'source_support': None,
                'time_and_scope': None, 'question_completeness': None, 'advice_qualification': None,
                'contradicted_or_unsupported_claims': [], 'comments': '', 'status': 'pending'}})
    # Refresh derived flags while preserving every human annotation verbatim.
    template = output / 'human_review.jsonl'
    if template.exists():
        previous = {row['case_id']: row for row in
                    (json.loads(line) for line in template.read_text().splitlines() if line.strip())}
        human = [{**previous.get(row['case_id'], {}), **row,
                  'human_review': previous.get(row['case_id'], {}).get('human_review', row['human_review'])}
                 for row in human]
    template.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in human))
    corrections = [
        'The market prompt allowed any risk metric, but the original numeric rubric demanded ETH max_drawdown_30d. The answer supplied exact ETH/BTC volatility instead. Retain the original 5/6 raw target coverage but do not label this a factual error or an omission of the requested risk metric.',
        'No-answer responses to unsupported premises used response_mode=answer while explicitly rejecting the premise in prose. Mode agreement is not semantic correctness.',
        'Three metadata claims in missing_stake use invented structured metric/unit keys against a text-only catalog receipt. Their count/status may exist in source text; the failure is of the structured numeric contract, not proof that all three facts are false.',
        'The stock_flow report wrote 2025-10-25 in summary/limitations although supplied observed_at is 2026-10-25. The original date is preserved; this is an AI-assisted source audit requiring human confirmation.',
        'Two first submissions failed schema validation. Their raw tool arguments are retained for content review. Follow-up schema-only continuations are a separate post-pilot phase; never replace first-pass denominators.'
    ]
    repairs = []
    for file in sorted((output / 'format-repairs').glob('*/result.json')):
        repairs.append(json.loads(file.read_text()))
    packet = {'schema': 'monthly-report-review-packet-v1',
        'original_manifest_sha256': hashlib.sha256((frozen / 'manifest.json').read_bytes()).hexdigest(),
        'original_summary_sha256': hashlib.sha256((frozen / 'summary.json').read_bytes()).hexdigest(),
        'review_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'automatic_cases': automatic, 'rubric_and_audit_notes': corrections,
        'format_repairs_separate_phase': repairs, 'semantic_correctness': None,
        'human_adjudication': 'pending', 'first_pass_denominator': len(cases)}
    atomic(output / 'review_packet.json', packet)
    lines = ['# Monthly report baseline review', '',
        'Fixed-context first-pass pilot. No data network or terminal; same configured Doubao Pro and local Deep Agents.', '',
        f"- First-pass valid structured reports: {summary['completed_cases']}/{summary['planned_cases']}.",
        f"- Structured numeric fields exactly matching eligible saved values and units: {summary['totals_on_completed_cases']['exact_numeric_claims']}/{summary['totals_on_completed_cases']['numeric_claims']}.",
        f"- Explicit claims with eligible citation IDs: {summary['totals_on_completed_cases']['claims_with_eligible_citations']}/{summary['totals_on_completed_cases']['explicit_claims']}. ID eligibility is not semantic support.",
        f"- Author calls: {summary['model_calls_all_attempts']}; provider-reported usage: {summary['usage_all_attempts']}.",
        f"- Total measured per-case wall time: {summary['latency_seconds_all_attempts']:.2f} seconds. Subscription dollar cost unknown.",
        '- Semantic correctness and human adjudication remain pending.', '',
        '| Case | First-pass state | ISO dates absent from supplied context (review flags) |',
        '|---|---|---|']
    for row in automatic:
        lines.append(f"| {row['case_id']} | {row['state']} | {', '.join(row['iso_dates_absent_from_supplied_context']) or 'None'} |")
    lines += ['', '## Rubric issues and source-audit findings', '']
    lines += ['- ' + note for note in corrections]
    lines += ['', '## Separate schema-only continuations', '',
        'These are post-pilot debugging runs with original sources/questions and schema-error feedback, not a replacement baseline.']
    for row in repairs:
        lines.append(f"- {row['case_id']}: {row['state']}, {row.get('author_calls')} calls; result retained separately.")
    lines += ['', '## Human review', '',
        'Fill human_review.jsonl against each selected_evidence.json and original source before reporting semantic accuracy. Templates preserve edits on rerun. review_packet.json retains machine flags and protocol hashes.',
        '', 'These reports are evaluation drafts, not accepted product publications. No training, Jev call, investment-performance conclusion or statistical significance is claimed.']
    (output / 'REVIEW.md').write_text('\n'.join(lines) + '\n')
    return {k: summary[k] for k in ('planned_cases', 'completed_cases', 'failed_cases')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'local/report-baseline/monthly-v1')
    print(json.dumps(review(parser.parse_args().output), indent=2))
