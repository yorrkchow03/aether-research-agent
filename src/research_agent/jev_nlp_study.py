"""Frozen stage-wise NLP experiments, separate from the production agent.

Only explicit collect/timing commands make provider requests. Development-only
gates are sealed before test collection; scoring and context replay are offline.
Original first attempts, errors, receipts and counterfactual cost distinctions
remain available. Credentials are loaded from a local config directory only.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random
import runpy
import statistics
import time

from research_data.http import utcnow
from .evidence_judgments import EvidenceJudgmentService, QUESTIONS, canonical
from .jev_nlp_baselines import rule_judgment, propose_relation_pairs
from .jev_nlp_metrics import compare_providers, operational_acceptance
from .jev_nlp_policy import pack_evidence
from .semantic_costs import PRICE_SNAPSHOT, estimate_cost, token_count


MODELS = {'jev': 'jev-1.14.0', 'doubao': 'doubao-seed-2.1-pro'}
CRITERION = 'msbd5018-neutral-stage-study-v2'
IMPLEMENTATION = (
    'src/research_agent/jev_nlp_study.py', 'src/research_agent/jev_nlp_policy.py',
    'src/research_agent/jev_nlp_metrics.py', 'src/research_agent/jev_nlp_baselines.py',
    'src/research_agent/jev_nlp_generation.py',
    'src/research_agent/jev_nlp_revision.py',
    'src/research_agent/evidence_judgments.py', 'src/research_agent/model.py',
    'src/research_agent/semantic_probabilities.py', 'src/research_agent/semantic_costs.py',
    'src/research_agent/settings.py', 'src/research_agent/investment/jev.py',
    'scripts/evaluation/prepare_jev_nlp_course.py',
    'scripts/evaluation/run_jev_nlp_course.py')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value, *, once=False):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x' if once else 'w') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def payload(case, data):
    # Never include expected labels, rationales, split or retention annotations.
    return {'task': case['task'], 'question': case['question'],
            'evidence': [deepcopy(data['sources'][key]['record']) for key in case['source_keys']]}


def _key(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def make_jobs(data):
    jobs = {}; aliases = {}; context_jobs = {}
    def add(name, value, split):
        key = _key(value)
        if key in jobs and jobs[key]['split'] != split:
            raise ValueError('A semantic input crosses development and test')
        jobs.setdefault(key, {'job_id': key, 'payload': value, 'split': split})
        aliases[name] = key
        return key
    for case in data['cases']:
        add('case:' + case['case_id'], payload(case, data), case['split'])
    for context in data.get('context_sets', []):
        cid = context['context_id']; keys = context['source_keys']
        records = [data['sources'][key]['record'] for key in keys]
        item = {'relevance': {}, 'relations': []}
        for key, record in zip(keys, records):
            item['relevance'][key] = add('context:' + cid + ':relevance:' + key,
                {'task': 'relevance', 'question': context['question'], 'evidence': [record]}, context['split'])
        # Pair scheduling sees inputs only. The labeled relation list is not read.
        for pair in propose_relation_pairs(records, query=context['question'], limit=3):
            i, j = pair['indexes']
            job = add('context:' + cid + ':pair:' + str(i) + ':' + str(j),
                {'task': 'relation', 'question': context['question'], 'evidence': [records[i], records[j]]}, context['split'])
            item['relations'].append({'source_key': keys[i], 'target_key': keys[j], 'job_id': job,
                                      'scheduler': pair.get('rationale')})
        context_jobs[cid] = item
    return jobs, aliases, context_jobs


def prepare(root, dataset, output, review):
    root = Path(root); output = Path(output)
    if output.exists():
        raise ValueError('Choose a new output directory; preserve previous runs')
    data = runpy.run_path(str(root / 'scripts/evaluation/prepare_jev_nlp_course.py'))['load_dataset'](dataset)
    draft_dataset = deepcopy(data)
    reviewed = json.loads(Path(review).read_text())
    if any(case['split'] not in ('development', 'test', 'confirmation') for case in data['cases']):
        raise ValueError('Unknown dataset split')
    if reviewed.get('provider_outputs_seen') is not False:
        raise ValueError('A blind reference review completed before provider outputs is required')
    if reviewed.get('dataset_sha256') != digest(dataset):
        raise ValueError('Blind review must identify the exact dataset bytes')
    if any(not isinstance(item['record'].get('text'), str) or not 0 < len(item['record']['text']) <= 12000
           for item in data['sources'].values()):
        raise ValueError('Frozen source excerpts must fit the adapter without truncation')
    reviewed_cases = {case['case_id']: case for case in reviewed['cases']}
    if set(reviewed_cases) != {case['case_id'] for case in data['cases']}:
        raise ValueError('Blind review and dataset case sets differ')
    originals = deepcopy(data['cases']); disagreements = []
    for case in data['cases']:
        labels = reviewed_cases[case['case_id']].get('labels') or reviewed_cases[case['case_id']].get('expected')
        if set(labels) != set(case['expected']):
            raise ValueError('Blind review dimensions differ')
        for dimension, proposed in labels.items():
            if not proposed or not set(proposed) <= set(QUESTIONS[case['task']][dimension]['criteria']):
                raise ValueError('Invalid independent reference label')
            draft = case['expected'][dimension]
            if set(draft) != set(proposed):
                disagreements.append({'case_id': case['case_id'], 'dimension': dimension,
                                      'draft': draft, 'blind': proposed})
            # Unresolved pre-provider disagreements stay explicit acceptable sets;
            # singleton F1 excludes them rather than adjudicating after results.
            case['expected'][dimension] = sorted(set(draft) | set(proposed))
    jobs, aliases, contexts = make_jobs(data)
    order = []
    rng = random.Random(20261105)
    for split in ('development', 'test', 'confirmation'):
        ids = sorted(key for key, value in jobs.items() if value['split'] == split); rng.shuffle(ids)
        for index, key in enumerate(ids):
            arms = ['jev', 'doubao'] if index % 2 == 0 else ['doubao', 'jev']
            order.extend({'job_id': key, 'provider': arm, 'split': split} for arm in arms)
    timing_ids = []
    for task in QUESTIONS:
        choices = sorted(case['case_id'] for case in data['cases'] if case['task'] == task and case['split'] != 'development')
        rng.shuffle(choices); timing_ids.extend(choices[:2])
    maximum = len(jobs) + len(timing_ids) * 2
    generation_ids = [context['context_id'] for context in data.get('context_sets', []) if context['split'] != 'development']
    if maximum > 160 or maximum + 4 * len(generation_ids) > 160 or len(generation_ids) > 6:
        raise ValueError('The frozen semantic plus generator plan exceeds the bounded study')
    protocol = {'schema': 'jev-nlp-stage-study-v3', 'prepared_at': utcnow(),
        'seed': 20261105, 'models': MODELS, 'criterion': CRITERION,
        'dataset_input_sha256': digest(dataset), 'blind_review_input_sha256': digest(review),
        'jobs': jobs, 'aliases': aliases, 'context_jobs': contexts, 'order': order,
        'timing_case_ids': timing_ids, 'additional_cold_repetitions': 2,
        'max_requests_per_provider': 160, 'planned_requests_per_provider_with_timing': maximum,
        'max_consecutive_provider_errors': 3, 'timeout_seconds': 45, 'max_attempts': 1,
        'jev_estimated_usd_stop': '0.25', 'threshold_grid': [.8, .9, .95, .99],
        'minimum_development_accepted_cases': 2, 'maximum_development_observed_error': .05,
        'primary_context_budget_chars': 12000, 'secondary_context_budget_chars': 6400,
        'maximum_context_records': 3,
        'context_acceptance_policy': 'whole_case_before_semantic_hint_v3',
        'fixed_generator_context_ids': generation_ids,
        'fixed_generator_arms': ['lexical', 'doubao', 'jev', 'task_selective'],
        'fixed_generator_model': 'doubao-seed-2.1-pro',
        'maximum_generator_requests': 24,
        'implementation': {name: digest(root / name) for name in IMPLEMENTATION},
        'pricing_snapshot': PRICE_SNAPSHOT, 'pricing_rechecked': '2026-11-05',
        'reference_disagreements': disagreements, 'human_labels': 0,
        'scope': 'Fresh event/time separated AI-reviewed diagnostics. Acceptable-set references retain pre-run disagreements. No human truth, calibrated safety or financial benefit is established.'}
    output.mkdir(parents=True)
    for name, value in (('DATASET.json', draft_dataset), ('EFFECTIVE_CASES.json', data['cases']), ('DRAFT_CASES.json', originals),
                        ('REFERENCE_REVIEW.json', reviewed), ('PROTOCOL.json', protocol)):
        write_json(output / name, value, once=True)
    for name in IMPLEMENTATION:
        target = output / 'implementation' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / name).read_bytes())
    write_json(output / 'FREEZE.json', {'files': {name: digest(output / name) for name in
        ('DATASET.json', 'EFFECTIVE_CASES.json', 'DRAFT_CASES.json', 'REFERENCE_REVIEW.json', 'PROTOCOL.json')}}, once=True)
    return {'status': 'frozen', 'cases': len(data['cases']), 'unique_jobs': len(jobs),
            'planned_requests_per_provider': maximum, 'pre_run_disagreements': len(disagreements)}


def load(root, output):
    root = Path(root); output = Path(output)
    for name, expected in json.loads((output / 'FREEZE.json').read_text())['files'].items():
        if digest(output / name) != expected:
            raise ValueError('Frozen study file changed: ' + name)
    protocol = json.loads((output / 'PROTOCOL.json').read_text())
    if any(digest(root / name) != value for name, value in protocol['implementation'].items()):
        raise ValueError('Frozen implementation changed; create a separately versioned run')
    data = json.loads((output / 'DATASET.json').read_text())
    if (output / 'EFFECTIVE_CASES.json').is_file():
        data['cases'] = json.loads((output / 'EFFECTIVE_CASES.json').read_text())
    return protocol, data


def _rows(output):
    return {arm: {path.stem: json.loads(path.read_text())['result']
                  for path in (Path(output) / 'first' / arm).glob('*.json')}
            for arm in MODELS}


def _case_results(protocol, data, rows):
    return {case['case_id']: rows.get(protocol['aliases']['case:' + case['case_id']],
                {'status': 'not_attempted', 'dimensions': {}}) for case in data['cases']}


def calibrate(root, output):
    protocol, data = load(root, output); rows = _rows(output)
    if any(slot['job_id'] not in rows[slot['provider']] for slot in protocol['order'] if slot['split'] == 'development'):
        raise ValueError('Finish every planned development attempt before calibration')
    gates = {}; diagnostics = {}
    for task in QUESTIONS:
        cases = [case for case in data['cases'] if case['split'] == 'development' and case['task'] == task]
        results = _case_results(protocol, data, rows['jev']); candidates = []
        for threshold in protocol['threshold_grid']:
            accepted = [case for case in cases if operational_acceptance(case, results[case['case_id']], threshold=threshold)['accepted']]
            correct = sum(all(results[case['case_id']]['dimensions'][name]['choice'] in values
                              for name, values in case['expected'].items()) for case in accepted)
            risk = 1 - correct / len(accepted) if accepted else None
            candidates.append({'threshold': threshold, 'accepted_cases': len(accepted), 'correct_cases': correct, 'risk': risk})
        allowed = [row for row in candidates if row['accepted_cases'] >= protocol['minimum_development_accepted_cases']
                   and row['risk'] is not None and row['risk'] <= protocol['maximum_development_observed_error']]
        gates[task] = min((row['threshold'] for row in allowed), default=None)
        diagnostics[task] = candidates
    value = {'schema': 'jev-nlp-dev-gates-v2', 'frozen_at': utcnow(), 'thresholds': gates,
             'development_diagnostics': diagnostics,
             'task_selective_direct_pro': ['relation', 'support'],
             'scope': 'Observed development-only routing; tiny samples do not establish calibrated error guarantees.'}
    value['development_receipts'] = {str(path.relative_to(output)): digest(path)
        for arm in MODELS for key, job in protocol['jobs'].items() if job['split'] == 'development'
        for path in [Path(output) / 'first' / arm / (key + '.json')]}
    write_json(Path(output) / 'CALIBRATION.json', value, once=True)
    write_json(Path(output) / 'CALIBRATION.sha256.json', {'sha256': digest(Path(output) / 'CALIBRATION.json')}, once=True)
    return value


def _calibration(output):
    path = Path(output) / 'CALIBRATION.json'
    expected = json.loads((Path(output) / 'CALIBRATION.sha256.json').read_text())['sha256']
    if digest(path) != expected:
        raise ValueError('Frozen development gates changed')
    result = json.loads(path.read_text())
    for name, sha in result['development_receipts'].items():
        if digest(Path(output) / name) != sha:
            raise ValueError('A sealed development receipt changed')
    return result


def _not_attempted(arm, reason):
    return {'status': 'not_attempted', 'provider': arm, 'dimensions': {}, 'reason': reason,
            'billable_request_made': False, 'physical_request_count': 0, 'latency_seconds': 0, 'usage': None}


def _submit(service, value, marker):
    """An interrupted submission is uncertain, never silently sent again."""
    marker = Path(marker)
    if marker.exists():
        original_cache_only = service.cache_only
        try:
            service.cache_only = True
            cached = service.assess(value['task'], question=value['question'], evidence=value['evidence'], criterion_version=CRITERION)
            if cached.get('cache_hit'):
                # Restore the original request accounting, not free-cache usage.
                original = service.receipt(cached['cache_key'])['result']
                return original | {'recovered_from_local_receipt': True}
        finally:
            service.cache_only = original_cache_only
        return {'status': 'error', 'provider': service.provider, 'dimensions': {},
                'reason': 'interrupted_submission_outcome_unknown_no_resubmission',
                'billable_request_made': True, 'physical_request_count': None,
                'latency_seconds': 0, 'usage': None, 'interrupted_outcome': True}
    write_json(marker, {'started_at': utcnow(), 'payload_sha256': _key(value)}, once=True)
    return service.assess(value['task'], question=value['question'], evidence=value['evidence'], criterion_version=CRITERION)


def accounting(results):
    submitted = [row for row in results if row.get('billable_request_made') and not row.get('cache_hit')]
    costs = [row.get('billing') or estimate_cost(row) for row in submitted]
    return {'submitted_responses': len(submitted),
        'physical_requests': sum(row.get('physical_request_count') or 0 for row in submitted),
        'unknown_physical_counts': sum(row.get('physical_request_count') is None for row in submitted),
        'input_tokens': sum(token_count(row.get('usage'), 'input_tokens') or 0 for row in submitted) if any(token_count(row.get('usage'), 'input_tokens') is not None for row in submitted) else None,
        'output_tokens': sum(token_count(row.get('usage'), 'output_tokens') or 0 for row in submitted) if any(token_count(row.get('usage'), 'output_tokens') is not None for row in submitted) else None,
        'unknown_input_usage_responses': sum(token_count(row.get('usage'), 'input_tokens') is None for row in submitted),
        'unknown_output_usage_responses': sum(token_count(row.get('usage'), 'output_tokens') is None for row in submitted),
        'estimated_usd_known_portion': str(sum(Decimal(row['estimated_usd']) for row in costs if row.get('estimated_usd') is not None)),
        'estimated_afp_known_portion': str(sum(Decimal(row['estimated_afp']) for row in costs if row.get('estimated_afp') is not None)),
        'incomplete_cost_responses': sum(not row.get('complete_usage') for row in costs),
        'actual_cash_debit': None, 'scope': 'Known reported-usage portions only; unknowns are counted, not imputed as zero. AFP is subscription quota, not USD.'}


def _circuit_state(protocol, rows):
    consecutive = Counter(); stopped = {}
    for slot in protocol['order']:
        arm = slot['provider']; result = rows[arm].get(slot['job_id'])
        if not result:continue
        if result['status'] in ('error', 'unavailable'):
            consecutive[arm] += 1
        elif result['status'] == 'not_attempted':
            if result.get('reason') in ('consecutive_provider_errors', 'estimated_cost_stop', 'request_bound'):
                stopped[arm] = result['reason']
        else:
            consecutive[arm] = 0
        if consecutive[arm] >= protocol['max_consecutive_provider_errors']:
            stopped[arm] = 'consecutive_provider_errors'
    return consecutive, stopped


def collect(root, config_root, output, *, phase, progress=None):
    protocol, data = load(root, output); output = Path(output)
    if phase not in ('development', 'test'):
        raise ValueError('Use development or test phase')
    if phase == 'test':
        _calibration(output)
    rows = _rows(output); consecutive, stops = _circuit_state(protocol, rows)
    services = {arm: EvidenceJudgmentService(config_root, output / 'cache' / arm, arm,
        requested_model=model, timeout_seconds=45, max_attempts=1, minimum_confidence=.8)
        for arm, model in MODELS.items()}
    for slot in protocol['order']:
        if (slot['split'] == 'development') != (phase == 'development'):
            continue
        arm = slot['provider']; key = slot['job_id']
        if key in rows[arm]:
            continue
        if len(rows[arm]) >= protocol['max_requests_per_provider']:
            stops[arm] = 'request_bound'
        if arm == 'jev' and Decimal(accounting(rows[arm].values())['estimated_usd_known_portion']) >= Decimal(protocol['jev_estimated_usd_stop']):
            stops[arm] = 'estimated_cost_stop'
        value = protocol['jobs'][key]['payload']
        result = (_not_attempted(arm, stops[arm]) if arm in stops else
                  _submit(services[arm], value, output / 'started' / arm / (key + '.json')))
        row = {'job_id': key, 'provider': arm, 'payload_sha256': _key(value), 'result': result}
        write_json(output / 'first' / arm / (key + '.json'), row, once=True); rows[arm][key] = result
        failure = result['status'] in ('error', 'unavailable')
        consecutive[arm] = consecutive[arm] + 1 if failure else consecutive[arm] if result['status'] == 'not_attempted' else 0
        if consecutive[arm] >= protocol['max_consecutive_provider_errors']:
            stops[arm] = 'consecutive_provider_errors'
        checkpoint = {'phase': phase, 'completed_jobs': {p: len(r) for p, r in rows.items()}, 'stopped': stops,
                      'accounting': {p: accounting(r.values()) for p, r in rows.items()}, 'updated_at': utcnow()}
        write_json(output / 'STATE.json', checkpoint)
        if progress:
            progress({'phase': phase, 'provider': arm, 'task': value['task'], 'status': result['status'],
                      'seconds': round(result['latency_seconds'], 3), 'completed': len(rows[arm])})
    return json.loads((output / 'STATE.json').read_text()) if (output / 'STATE.json').exists() else {'stopped': stops}


def routed(task, jev, pro, calibration, *, selective=True):
    threshold = calibration['thresholds'].get(task)
    eligible = task not in calibration['task_selective_direct_pro'] if selective else True
    requested = {'task': task, 'expected': {name: [] for name in QUESTIONS[task]}}
    accepted = threshold is not None and operational_acceptance(requested, jev, threshold=threshold)['accepted']
    if eligible and accepted:
        return jev, 'jev'
    return pro, 'doubao'


def _stats(values):
    values = sorted(values)
    return {'n': len(values), 'median': statistics.median(values) if values else None,
            'p90': values[max(0, (9 * len(values) + 9) // 10 - 1)] if values else None,
            'sum': sum(values)}


def semantic_relevance_hint(result, *, threshold=.8):
    """Both relevance and instruction-risk fields must pass before ranking."""
    requested = {'task': 'relevance', 'expected': {name: [] for name in QUESTIONS['relevance']}}
    accepted = operational_acceptance(requested, result, threshold=threshold)['accepted']
    answer = result.get('dimensions', {}).get('relevance', {})
    return {'status': 'ok' if accepted else 'abstained', 'confidence': answer.get('confidence'),
            'score': sum(answer.get('probabilities', {}).get(label, 0) * weight
                         for label, weight in (('relevant', 1), ('background', .35)))}


def timing(root, config_root, output, *, progress=None):
    protocol, data = load(root, output); output = Path(output); _calibration(output)
    rows = _rows(output); by_case = {case['case_id']: case for case in data['cases']}; collected = []
    if any(slot['job_id'] not in rows[slot['provider']] for slot in protocol['order']):
        raise ValueError('Timing starts only after every primary slot is terminal')
    consecutive, prior_stops = _circuit_state(protocol, rows); stopped = set(prior_stops)
    for index, cid in enumerate(protocol['timing_case_ids']):
        case = by_case[cid]; job = protocol['aliases']['case:' + cid]; value = payload(case, data)
        for repeat in range(2):
            arms = ('jev', 'doubao') if (index + repeat) % 2 == 0 else ('doubao', 'jev')
            for arm in arms:
                path = output / 'timing' / (cid + '-' + arm + '-' + str(repeat) + '.json')
                if path.exists():
                    saved = json.loads(path.read_text()); collected.append(saved)
                    if saved['cold'].get('status') in ('error', 'unavailable'):
                        consecutive[arm] += 1
                    elif saved['cold'].get('status') != 'not_attempted':
                        consecutive[arm] = 0
                    if consecutive[arm] >= 3:stopped.add(arm)
                    continue
                if len(rows[arm]) + sum(r['provider'] == arm for r in collected) >= protocol['max_requests_per_provider']:
                    raise ValueError('Timing request bound reached')
                known_spend = accounting([*rows[arm].values(), *[r['cold'] for r in collected if r['provider'] == arm]])
                if arm == 'jev' and Decimal(known_spend['estimated_usd_known_portion']) >= Decimal(protocol['jev_estimated_usd_stop']):
                    stopped.add(arm)
                service = EvidenceJudgmentService(config_root, output / 'timing_cache' / cid / arm / str(repeat), arm,
                    requested_model=MODELS[arm], timeout_seconds=45, max_attempts=1, minimum_confidence=.8)
                cold = (_not_attempted(arm, 'provider_or_cost_stop') if arm in stopped else
                        _submit(service, value, output / 'timing_started' / (cid + '-' + arm + '-' + str(repeat) + '.json')))
                if cold.get('status') in ('error', 'unavailable'):
                    consecutive[arm] += 1
                elif cold.get('status') != 'not_attempted':
                    consecutive[arm] = 0
                if consecutive[arm] >= 3:stopped.add(arm)
                if cold.get('status') in ('ok', 'abstained') and cold.get('cache_key'):
                    service.cache_only = True
                    warm = service.assess(case['task'], question=value['question'], evidence=value['evidence'], criterion_version=CRITERION)
                else:
                    warm = _not_attempted(arm, 'cold_response_not_cached') | {'cache_hit': False}
                row = {'case_id': cid, 'provider': arm, 'repeat': repeat, 'payload_sha256': _key(value),
                       'cold': cold, 'warm': warm}
                write_json(path, row, once=True); collected.append(row)
                if progress:
                    progress({'timing': cid, 'provider': arm, 'repeat': repeat, 'cold_status': cold['status'], 'warm_cache_hit': warm['cache_hit']})
    summary = {}
    for arm in MODELS:
        selected = [r for r in collected if r['provider'] == arm]
        summary[arm] = {'cold_seconds': _stats([r['cold']['latency_seconds'] for r in selected if r['cold'].get('billable_request_made')]),
            'warm_cache_seconds': _stats([r['warm']['latency_seconds'] for r in selected if r['warm'].get('cache_hit')]),
            'warm_cache_misses': sum(not r['warm'].get('cache_hit') for r in selected),
            'accounting_cold': accounting([r['cold'] for r in selected]),
            'accounting_warm': accounting([r['warm'] for r in selected])}
    write_json(output / 'TIMING_SUMMARY.json', {'providers': summary,
        'scope': 'Two additional repeated fresh client-cache requests plus the original first trial. Warm uses cache_only and cannot make a provider call. Warm means client cache, not a warm model server.'})
    return summary


def analyze(root, output):
    protocol, data = load(root, output); output = Path(output); calibration = _calibration(output); rows = _rows(output)
    results = {arm: _case_results(protocol, data, r) for arm, r in rows.items()}
    results['rules'] = {case['case_id']: rule_judgment(**payload(case, data)) for case in data['cases']}
    policy_routes = {}
    for policy in ('classification_only', 'context_only', 'verification_only', 'all_jev', 'confidence_cascade', 'task_selective'):
        selected = {}; routes = Counter()
        for case in data['cases']:
            cid = case['case_id']; task = case['task']; j = results['jev'][cid]; p = results['doubao'][cid]
            use = (policy == 'all_jev' or policy == 'classification_only' and task == 'classification'
                   or policy == 'context_only' and task in ('relevance', 'relation')
                   or policy == 'verification_only' and task == 'support')
            if policy in ('confidence_cascade', 'task_selective'):
                result, route = routed(task, j, p, calibration, selective=policy == 'task_selective')
            else:
                result, route = (j, 'jev') if use else (p, 'doubao')
            selected[cid] = result; routes[route] += 1
        results[policy] = selected; policy_routes[policy] = dict(routes)
    labels = {task + '.' + name: tuple(spec['criteria']) for task, questions in QUESTIONS.items() for name, spec in questions.items()}
    scored = {}; primary_comparisons = {}
    for split in ('development', 'test', 'confirmation'):
        cases = [c for c in data['cases'] if c['split'] == split]
        if cases:
            scored[split] = compare_providers(cases, results, label_options=labels, bootstrap_samples=1000)
            primary_comparisons[split] = compare_providers(cases, {arm: results[arm] for arm in MODELS}, label_options=labels, bootstrap_samples=1000)
    context_rows = []
    for context in data.get('context_sets', []):
        jobs = protocol['context_jobs'][context['context_id']]
        for arm in ('rules', 'doubao', 'jev', 'task_selective'):
            candidates = []
            for key in context['source_keys']:
                record = deepcopy(data['sources'][key]['record']); job = jobs['relevance'][key]
                if arm == 'rules':
                    result = rule_judgment(**protocol['jobs'][job]['payload'])
                elif arm == 'task_selective':
                    result, _ = routed('relevance', rows['jev'].get(job, {}), rows['doubao'].get(job, {}), calibration)
                else:
                    result = rows[arm].get(job, {})
                record['relevance'] = semantic_relevance_hint(result)
                candidates.append(record)
            edges = []
            for pair in jobs['relations']:
                job = pair['job_id']
                if arm == 'rules':
                    result = rule_judgment(**protocol['jobs'][job]['payload'])
                elif arm == 'task_selective':
                    result, _ = routed('relation', rows['jev'].get(job, {}), rows['doubao'].get(job, {}), calibration)
                else:
                    result = rows[arm].get(job, {})
                answer = result.get('dimensions', {}).get('relation', {})
                edges.append({'source': data['sources'][pair['source_key']]['record']['record_id'],
                    'target': data['sources'][pair['target_key']]['record']['record_id'],
                    'label': answer.get('choice', 'insufficient'), 'confidence': answer.get('confidence'), 'status': result.get('status')})
            for budget in (protocol['primary_context_budget_chars'], protocol['secondary_context_budget_chars']):
                for variant in ('lexical', 'semantic_no_graph', 'semantic_graph', 'graph_no_protection'):
                    if variant == 'lexical' and arm != 'rules':
                        continue
                    packed = pack_evidence(candidates, edges, query=context['question'], budget_chars=budget,
                                           variant=variant, minimum_confidence=.8, limit=protocol['maximum_context_records'])
                    text_records = packed['selected_records']; ids = set(packed['selected_ids'])
                    required = context.get('required_retention', [])
                    spans = sum(any(item['quote'] in r['text'] for r in text_records) for item in required)
                    sources = sum(data['sources'][item['source_key']]['record']['record_id'] in ids for item in required)
                    bound_spans = sum(any(item['quote'] in r['text'] and r['record_id'] == data['sources'][item['source_key']]['record']['record_id']
                                          for r in text_records) for item in required)
                    duplicate_excess = sum(max(0, sum(data['sources'][key]['record']['record_id'] in ids for key in group) - 1)
                                           for group in context.get('duplicate_groups', []))
                    row = {'context_id': context['context_id'], 'split': context['split'], 'arm': arm,
                        'variant': variant, 'budget_chars': budget, 'required_spans': len(required),
                        'retained_spans': spans, 'source_bound_retained_spans': bound_spans,
                        'retained_original_ids': sources, 'duplicate_excess': duplicate_excess,
                        'selection': packed}
                    context_rows.append(row)
    paired = []; unverified_pairs = []
    for key in protocol['jobs']:
        pair = [rows[arm].get(key) for arm in MODELS]
        if all(pair):
            if not all(row.get('input_hash') for row in pair):
                unverified_pairs.append(key);continue
            if pair[0]['input_hash'] != pair[1]['input_hash']:
                raise ValueError('Actual provider semantic inputs differ: ' + key)
            paired.append(key)
    summary = {'schema': 'jev-nlp-stage-results-v3', 'created_at': utcnow(), 'case_count': len(data['cases']),
        'unique_semantic_jobs': len(protocol['jobs']), 'paired_input_hashes_verified': len(paired),
        'scores': scored, 'primary_provider_comparisons': primary_comparisons,
        'unverified_provider_input_pairs': unverified_pairs, 'policy_routes_all_cases': policy_routes,
        'accounting_first_attempts': {arm: accounting(r.values()) for arm, r in rows.items()},
        'latency_first_attempts_seconds': {arm: _stats([row['latency_seconds'] for row in r.values()
            if row.get('billable_request_made') and not row.get('interrupted_outcome')]) for arm, r in rows.items()},
        'calibration': calibration, 'context_selections': context_rows, 'human_labels': 0,
        'scope': 'Routing/context ablations replay cached paired decisions. They are counterfactual policies, not additional independently timed end-to-end agent runs. All provider collection charges remain counted separately.'}
    write_json(output / 'ANALYSIS.json', summary)
    return summary
