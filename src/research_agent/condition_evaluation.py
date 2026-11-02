"""Frozen condition-change diagnostics; contract checks are not semantic truth.

The fixture labels are inspectable AI-authored references.  A provider's typed
label may be compared with them, but exact quotations/condition coverage only
establish transport fidelity.  No free-form response is graded by keywords.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from statistics import median

from research_data.library.time import timestamp

SCHEMA = 'aether-condition-preservation-v1'
LABELS = frozenset({'supported', 'contradicted', 'insufficient', 'conditional'})
KINDS = frozenset({'event_status', 'eligibility', 'horizon', 'correction',
                   'acquisition_time', 'user_context', 'uncertainty', 'scope'})


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _aware_time(value):
    _require(isinstance(value, str) and bool(value.strip()), 'Fixture clocks require explicit timezone-aware strings')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Fixture clocks require explicit timezone-aware strings') from None
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
             'Fixture clocks require explicit timezone-aware strings')
    return timestamp(parsed)


def _check_observation_clocks(source):
    """Check supplied observation metadata; do not infer dates from prose.

    event_at may be a future planned event, so it is deliberately not treated
    as a completed observation.  The fixture must label completed periods with
    observed_at/period_end to make this check applicable.
    """
    published = _aware_time(source['published_at']) if source.get('published_at') else None
    acquired = _aware_time(source['retrieved_at'])
    for key in ('observed_at', 'period_end'):
        if source.get(key) is None:
            continue
        observed = _aware_time(source[key])
        _require(observed <= acquired and (published is None or observed <= published),
                 'Source observation/period end is later than publication or acquisition')


def eligible_sources(case):
    """Acquisition/publication filters precede provider prompts or any scoring."""
    before = _aware_time(case['cutoff'])
    eligible = []
    for source in case['sources']:
        dates = [source.get('retrieved_at'), source.get('available_at'),
                 source.get('published_at'), source.get('observed_at'), source.get('period_end'),
                 (source.get('source') or {}).get('retrieved_at')]
        _require(bool(source.get('retrieved_at')), 'Every fixture source needs acquisition time')
        if any(_aware_time(value) > before for value in dates if value):
            continue
        eligible.append(deepcopy(source))
    return eligible


def condition_probe(case):
    """Only cutoff-eligible source bytes and task; no oracle/reference leakage."""
    return {'question': case['question'], 'evidence': eligible_sources(case)}


def load_dataset(path):
    data = json.loads(Path(path).read_text())
    _require(data.get('schema') == SCHEMA, 'Unknown condition diagnostic schema')
    _require(data.get('provenance') == 'synthetic_minimal_sources', 'Fixture provenance must be explicit')
    _require(data.get('held_out') is False, 'Development diagnostics are not held-out evidence')
    _require(isinstance(data.get('cases'), list) and 1 <= len(data['cases']) <= 100,
             'Use bounded predeclared cases')
    protocol = data['protocol']
    _require(protocol.get('task') == 'support', 'This diagnostic uses the existing support task')
    _require(protocol.get('maximum_provider_attempts_per_case') == 1, 'Preserve one first attempt')
    ids, pairs = set(), {}
    for case in data['cases']:
        cid = case['case_id']
        _require(isinstance(cid, str) and cid and cid not in ids, 'Case identities must be unique')
        ids.add(cid)
        _require(case['reference']['label'] in LABELS, 'Unknown reference support label')
        _require(1 <= len(case['sources']) <= protocol['maximum_sources'], 'Source count exceeds protocol')
        source_map = {s['record_id']: s for s in case['sources']}
        _require(len(source_map) == len(case['sources']), 'Duplicate source identity')
        for source in case['sources']:
            _require(source['dataset_id'] == 'synthetic.condition_preservation.v1', 'Never mix real/private data into synthetic fixtures')
            _require(source['source_kind'] == 'synthetic_fixture', 'Synthetic originals must stay marked')
            _require(isinstance(source['text'], str) and source['text'].startswith('SYNTHETIC'), 'Fixture must disclose synthetic text')
            _check_observation_clocks(source)
        eligible = eligible_sources(case)
        _require(sorted(s['record_id'] for s in eligible) == sorted(case['expected_eligible_ids']),
                 'Declared cutoff eligibility does not match source clocks')
        _require(len(canonical(condition_probe(case))) <= protocol['context_budget_characters'],
                 'Exact context exceeds common protocol budget; never truncate conditions')
        obligations = {item['obligation_id']: item for item in case['obligations']}
        _require(len(obligations) == len(case['obligations']), 'Duplicate obligation identity')
        for obligation in obligations.values():
            _require(obligation['kind'] in KINDS, 'Unknown typed condition kind')
            row = source_map.get(obligation['record_id'])
            _require(row is not None and obligation['exact_quote'] and obligation['exact_quote'] in row['text'],
                     'Required condition is not an exact source passage')
        for oid in case['reference']['required_obligation_ids']:
            _require(oid in obligations and obligations[oid]['record_id'] in case['expected_eligible_ids'],
                     'Reference requires an unavailable condition')
        pairs.setdefault(case['pair_id'], []).append(case)
    for cases in pairs.values():
        _require(len(cases) == 2, 'Every counterfactual pair needs exactly two cases')
        _require(cases[0]['question'] == cases[1]['question'], 'A source-change pair must keep its claim identical')
        _require(cases[0]['reference']['label'] != cases[1]['reference']['label'], 'Pair must exercise a declared label change')
        _require(digest(condition_probe(cases[0])) != digest(condition_probe(cases[1])),
                 'Counterfactual context did not actually change')
    return data


def score_prediction(case, prediction):
    """Check typed output, exact eligible quotes and obligation coverage separately.

    Provider support labels do not contain quotations; score their agreement
    without fabricating quote/coverage success.  A report adapter may supply
    quotes and obligation IDs for the distinct transport checks.
    """
    state = prediction.get('status', 'error')
    _require(state in ('completed', 'abstained', 'error', 'missing'), 'Unknown attempt status')
    label = prediction.get('label')
    _require(label is None or label in LABELS, 'Unknown predicted support label')
    _require(state == 'completed' or label is None, 'Unusable attempts cannot carry a scored label')
    _require(state != 'completed' or label in LABELS, 'Completed attempt requires a typed label')
    eligible = {s['record_id']: s for s in eligible_sources(case)}
    supplied_quotes = prediction.get('quotes')
    quote_checks = []
    for quote in supplied_quotes or []:
        row = eligible.get(quote.get('record_id'))
        text = quote.get('text')
        quote_checks.append({'record_id': quote.get('record_id'),
                             'eligible_source': row is not None,
                             'exact_quote': bool(row and isinstance(text, str) and text and text in row['text'])})
    supplied_ids = prediction.get('obligation_ids')
    obligations = {o['obligation_id']: o for o in case['obligations']}
    coverage = []
    for oid in case['reference']['required_obligation_ids']:
        obligation = obligations[oid]
        present = oid in (supplied_ids or []) and any(
            quote.get('record_id') == obligation['record_id'] and
            isinstance(quote.get('text'), str) and obligation['exact_quote'] in quote['text'] and
            quote['text'] in eligible.get(obligation['record_id'], {}).get('text', '')
            for quote in supplied_quotes or [])
        coverage.append({'obligation_id': oid, 'kind': obligation['kind'], 'exact_condition_retained': present})
    unknown_ids = sorted(set(supplied_ids or []) - set(obligations))
    return {'case_id': case['case_id'], 'status': state, 'label': label,
            'label_agrees_with_ai_reference': label == case['reference']['label'] if state == 'completed' else None,
            'quote_checks': quote_checks,
            'source_quote_integrity': all(c['exact_quote'] for c in quote_checks) if supplied_quotes is not None else None,
            'condition_coverage': coverage if supplied_ids is not None and supplied_quotes is not None else None,
            'required_condition_coverage': all(c['exact_condition_retained'] for c in coverage) and not unknown_ids
                if supplied_ids is not None and supplied_quotes is not None else None,
            'unknown_obligation_ids': unknown_ids, 'semantic_correctness': None,
            'independent_adjudication': 'pending',
            'scope': 'Typed agreement with AI-authored labels and literal source/condition transport; no free-form semantic grading.'}


def aggregate_attempts(dataset, attempts):
    """All planned cases remain in each arm's denominator, including failures."""
    cases = {c['case_id']: c for c in dataset['cases']}
    by_arm = {}
    for attempt in attempts:
        arm, cid = attempt['arm'], attempt['case_id']
        _require(cid in cases, 'Attempt references an unknown fixture case')
        rows = by_arm.setdefault(arm, {})
        _require(cid not in rows, 'Do not replace a first attempt with a retry or best output')
        _require(attempt.get('input_hash') == digest(condition_probe(cases[cid])),
                 'Attempt did not use the identical frozen source/claim context')
        rows[cid] = attempt
    results = []
    for arm, rows in sorted(by_arm.items()):
        scored = [score_prediction(case, rows.get(cid, {'status': 'missing'})) for cid, case in cases.items()]
        complete = [r for r in scored if r['status'] == 'completed']
        timings = [r.get('latency_seconds') for r in rows.values() if isinstance(r.get('latency_seconds'), (int, float))]
        costs = [r.get('estimated_cost') for r in rows.values()]
        results.append({'arm': arm, 'planned': len(cases), 'attempted': len(rows),
            'completed': len(complete), 'abstained': sum(r['status'] == 'abstained' for r in scored),
            'errors': sum(r['status'] == 'error' for r in scored), 'missing': sum(r['status'] == 'missing' for r in scored),
            'reference_agreement_count': sum(r['label_agrees_with_ai_reference'] is True for r in scored),
            'reference_agreement_over_planned': sum(r['label_agrees_with_ai_reference'] is True for r in scored)/len(cases),
            'reference_agreement_over_usable': sum(r['label_agrees_with_ai_reference'] is True for r in scored)/len(complete) if complete else None,
            'median_recorded_latency_seconds': median(timings) if timings else None,
            'latency_records': len(timings),
            'cost_records': sum(value is not None for value in costs),
            'cost_total': sum(costs) if len(costs) == len(cases) and all(isinstance(v, (int, float)) for v in costs) else None,
            'scores': scored})
    return {'schema': SCHEMA, 'dataset_hash': digest(dataset), 'protocol_hash': digest(dataset['protocol']),
            'arms': results, 'semantic_correctness': None, 'independent_adjudication': 'pending',
            'scope': 'First-attempt synthetic diagnostic only; estimated costs must share a declared unit per arm.'}


def offline_checks(dataset):
    """Exercise fixture contracts and known-invalid transports with zero providers."""
    checks = []
    for case in dataset['cases']:
        source_map = {s['record_id']: s for s in eligible_sources(case)}
        obligations = {o['obligation_id']: o for o in case['obligations']}
        required = case['reference']['required_obligation_ids']
        reference = {'status': 'completed', 'label': case['reference']['label'], 'obligation_ids': required,
            'quotes': [{'record_id': obligations[oid]['record_id'], 'text': obligations[oid]['exact_quote']} for oid in required]}
        valid = score_prediction(case, reference)
        probes = []
        if required:
            omitted = deepcopy(reference); omitted['quotes'] = []
            probes.append({'mutation': 'drop_required_condition', 'rejected': score_prediction(case, omitted)['required_condition_coverage'] is False})
            changed = deepcopy(reference); changed['quotes'][0]['text'] += ' FABRICATED'
            probes.append({'mutation': 'alter_source_quote', 'rejected': score_prediction(case, changed)['source_quote_integrity'] is False})
            wrong = deepcopy(reference); wrong['quotes'][0]['record_id'] = 'condition-fixture-unrelated'
            probes.append({'mutation': 'unrelated_source_id', 'rejected': score_prediction(case, wrong)['source_quote_integrity'] is False})
        for source in case['sources']:
            if source['record_id'] not in source_map:
                future = {'status': 'completed', 'label': case['reference']['label'],
                          'quotes': [{'record_id': source['record_id'], 'text': source['text']}]}
                probes.append({'mutation': 'future_capture_quote', 'rejected': score_prediction(case, future)['source_quote_integrity'] is False})
        checks.append({'case_id': case['case_id'], 'eligible_source_ids': sorted(source_map),
            'fixture_reference_transport_passed': valid['source_quote_integrity'] is True and valid['required_condition_coverage'] is True,
            'mutant_checks': probes, 'all_deliberate_mutants_rejected': all(p['rejected'] for p in probes)})
    return {'schema': SCHEMA, 'dataset_hash': digest(dataset), 'protocol_hash': digest(dataset['protocol']),
            'fixture_cases': len(checks), 'pairs': len({c['pair_id'] for c in dataset['cases']}),
            'fixture_contracts_passed': all(c['fixture_reference_transport_passed'] and c['all_deliberate_mutants_rejected'] for c in checks),
            'checks': checks, 'provider_calls': 0, 'semantic_correctness': None, 'independent_adjudication': 'pending',
            'scope': 'Generated reference/mutant transport checks only; this is not a model accuracy result.'}
