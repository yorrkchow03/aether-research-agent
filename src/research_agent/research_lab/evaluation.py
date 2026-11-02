"""Prespecified signal comparisons on one immutable corpus; no network or model API."""
from __future__ import annotations
import json
import math
from pathlib import Path
import time
import numpy as np
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from research_agent.investment.strategies import FEATURES, samples, training_before, estimator, scores, model_json
from research_agent.simulation import simulate
from research_agent.live import atomic
from research_data.http import utcnow
from research_data.normalize import timestamp
from .corpus import load_corpus, digest
from .signals import signal_catalog

VERSION = 'purged-signal-comparison-v1'
LIMITATIONS = [
    'Single-venue retrospective OHLCV, not authenticated historical public availability.',
    'Windows, correlated features and two correlated assets are not independent experiments.',
    'Models are frozen at each window start with strictly earlier available labels; no within-window refitting.',
    'Feature inclusion/ablation measures this model and protocol, not causality or intrinsic signal quality.',
    'Fixed long/cash thresholds and 50% cap are research assumptions, not personalized advice.',
    'Daily-open fills and fixed per-side costs omit variable spread, latency and market impact.',
    'Block-bootstrap intervals are descriptive paired uncertainty, without multiple-testing significance claims.',
    'All prespecified confirmation variants are reported; selecting the best after seeing confirmation is invalid.',
]


def variants():
    return [('full_logistic', list(range(10)), 'logistic'), ('full_random_forest', list(range(10)), 'random_forest'),
            ('constant_prior', [], 'constant')] + [
        (f'single:{name}', [i], 'logistic') for i, name in enumerate(FEATURES)] + [
        (f'drop:{name}', [j for j in range(10) if j != i], 'logistic') for i, name in enumerate(FEATURES)]


def window_rows(history, protocol, window):
    rows = samples(history['series'], protocol['evaluation']['execution_delay_bars'])
    first, last = timestamp(window['start']), timestamp(window['end'])
    test = [r for r in rows if first <= timestamp(r['execute_at']) < last and timestamp(r['label_available_at']) <= last]
    if len(test) < 3: raise ValueError('Window has fewer than three common target rows')
    training = training_before(rows, test[0]['decision_at'])
    if len(training) < protocol['evaluation']['minimum_training_samples']: raise ValueError('Insufficient purged training labels')
    if len({r['label'] for r in training}) != 2: raise ValueError('Training needs both target classes')
    if max(timestamp(r['label_available_at']) for r in training) >= timestamp(test[0]['decision_at']):
        raise ValueError('Training-label cutoff violation')
    return training, test


def paired_interval(values, config):
    """Circular time blocks, never shuffle individual dependent daily observations."""
    a = np.asarray(values, dtype=float); block = config['bootstrap_block_days']; n = len(a)
    if not np.isfinite(a).all(): raise ValueError('Nonfinite paired observations')
    if n < 2*block: return {'mean': float(a.mean()) if n else None, 'ci95': None, 'n': n, 'reason': 'Fewer than two time blocks'}
    rng = np.random.default_rng(config['seed']); means = []
    for _ in range(config['bootstrap_draws']):
        starts = rng.integers(0, n, size=math.ceil(n/block))
        indices = ((starts[:, None]+np.arange(block)) % n).ravel()[:n]
        means.append(float(a[indices].mean()))
    return {'mean': float(a.mean()), 'ci95': np.quantile(means, [.025, .975]).tolist(), 'n': n,
            'block_days': block, 'draws': config['bootstrap_draws'], 'method': 'paired circular moving blocks; descriptive, not multiplicity-adjusted'}


def probability_weights(rows, probabilities, series, config):
    held = 0.; weights = {}; audit = []
    for r, p in zip(rows, probabilities):
        if not math.isfinite(p) or not 0 <= p <= 1: raise ValueError('Invalid probability')
        if p >= config['long_probability']: held = config['allocation_cap']; state = 'long'
        elif p <= config['cash_probability']: held = 0.; state = 'cash'
        else: state = 'abstain_keep_previous'
        weights[series[r['execute_index']-1]['period_end']] = held
        audit.append({'decision_at': r['decision_at'], 'execute_at': r['execute_at'], 'weight': held, 'state': state})
    return weights, audit


def paper_case(history, rows, weights, config, cost, *, strategy='model_signals', keep_curve=False):
    data = history['series'][:rows[-1]['execute_index']+1]
    profile = {'budget_usd': 1000, 'fee_bps': cost['fee_bps'], 'slippage_bps': cost['slippage_bps'], 'allocation_fraction': config['allocation_cap']}
    result = simulate(data, profile, strategy, 7, weights, start_index=rows[0]['execute_index'])
    index = {r['period_end']: r for r in data}
    result['mean_marked_exposure_fraction'] = float(np.mean([
        p['asset_quantity']*float(index[p['time']]['close_usd'])/p['equity_usd'] for p in result['curve']]))
    result['trade_count'] = len(result.pop('trades'))
    if not keep_curve: result.pop('curve')
    result['execution_convention'] = 'Completed decision bar, one declared full-bar delay, then daily open; see decision audit'
    result['cost_id'] = cost['id']
    return result


def fit_case(history, protocol, window):
    config = protocol['evaluation']; training, test = window_rows(history, protocol, window)
    train_x = np.array([r['x'] for r in training]); test_x = np.array([r['x'] for r in test]); y = np.array([r['label'] for r in training])
    target = np.array([r['label'] for r in test]); prior = float(y.mean()); results = {}; predictions = {}
    for name, columns, family in variants():
        if family == 'constant': probs = np.repeat(prior, len(test)); model = {'family': 'constant', 'training_prior': prior}
        else:
            fitted = estimator('random_forest') if family == 'random_forest' else make_pipeline(StandardScaler(), LogisticRegression(C=config['logistic_C'], max_iter=500, random_state=42))
            fitted.fit(train_x[:, columns], y)
            probs = fitted.predict_proba(test_x[:, columns])[:, list(fitted.classes_).index(1)]
            model = model_json(fitted, family)
        predictions[name] = probs
        weights, audit = probability_weights(test, probs, history['series'], config)
        scored = scores([{'label': r['label'], 'probability_up': float(p)} for r, p in zip(test, probs)])
        results[name] = {'columns': [FEATURES[i] for i in columns], 'model': model, 'scores': scored,
            'abstention_rows': sum(a['state'].startswith('abstain') for a in audit),
            'paper': [paper_case(history, test, weights, config, c) for c in config['cost_scenarios']]}
    constant_error = (predictions['constant_prior']-target)**2
    full_error = (predictions['full_logistic']-target)**2
    effects = {}
    for i, feature in enumerate(FEATURES):
        single = (predictions['single:'+feature]-target)**2; drop = (predictions['drop:'+feature]-target)**2
        # Rank association is descriptive; tied/constant vectors have no defined association.
        from scipy.stats import spearmanr
        x = test_x[:, i]; returns = [r['target_return_fraction'] for r in test]
        association = float(spearmanr(x, returns).statistic) if len(set(x)) > 1 and len(set(returns)) > 1 else None
        effects[feature] = {'single_brier_improvement_over_prior': paired_interval(constant_error-single, config),
            'full_brier_improvement_over_drop': paired_interval(drop-full_error, config),
            'raw_rank_correlation_with_delayed_return': association}
    series = history['series']; momentum = {series[r['execute_index']-1]['period_end']: config['allocation_cap'] if r['x'][2] > 0 else 0. for r in test}
    baselines = {name: [paper_case(history, test, momentum if name == 'delayed_7d_momentum' else {}, config, cost,
                    strategy='model_signals' if name in ('cash', 'delayed_7d_momentum') else name) for cost in config['cost_scenarios']]
                 for name in ('cash', 'buy_hold', 'dca', 'delayed_7d_momentum')}
    return {'asset': history['asset'], 'window': window, 'source_record_id': history['record_id'],
        'training_rows': len(training), 'last_training_label_available_at': training[-1]['label_available_at'],
        'first_decision_at': test[0]['decision_at'], 'common_target_rows': len(test), 'errors': [],
        'targets': [{k: r[k] for k in ('decision_at', 'execute_at', 'label_available_at', 'label', 'target_return_fraction')} for r in test],
        'probabilities': {name: p.tolist() for name, p in predictions.items()},
        'variants': results, 'signals': effects, 'baselines': baselines}


def summarize(cases, failures):
    output = {}
    for split in ('development', 'validation', 'confirmation'):
        subset = [c for c in cases if c['window']['split'] == split]; n = sum(c['common_target_rows'] for c in subset)
        variants_ = {}
        for name, _, _ in variants():
            if not n: continue
            variants_[name] = {'brier_score': sum(c['variants'][name]['scores']['brier_score']*c['common_target_rows'] for c in subset)/n,
                'base_net_log_growth_sum': sum(math.log1p(next(p['net_return_fraction'] for p in c['variants'][name]['paper'] if p['cost_id'] == 'base')) for c in subset),
                'abstention_rows': sum(c['variants'][name]['abstention_rows'] for c in subset)}
        output[split] = {'successful_cases': len(subset), 'failed_cases': sum(f['window']['split'] == split for f in failures),
            'common_asset_target_rows': n, 'variants': variants_, 'aggregate_scope': 'Same rows within each case; log growth sum is not a single investable portfolio'}
    return output


def run_evaluation(corpus_root, output, *, expected_manifest_sha256=None, splits=None):
    """Refuse overwrite. Snapshot implementation before observing results. Offline only."""
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()): raise ValueError('Evaluation output must be empty; retain previous runs')
    corpus = load_corpus(corpus_root, expected_manifest_sha256=expected_manifest_sha256)
    protocol = corpus['protocol']; selected = set(splits or ('development', 'validation', 'confirmation'))
    if not selected <= {'development', 'validation', 'confirmation'}: raise ValueError('Unknown evaluation split')
    files = sorted(Path(__file__).parents[2].rglob('*.py'))
    software = {}
    for file in files:
        relative = str(file.relative_to(Path(__file__).parents[2])); target = output/'software'/relative
        target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(file.read_bytes()); software[relative] = digest(file)
    atomic(output/'input_lock.json', {'version': VERSION, 'locked_at': utcnow(), 'corpus_manifest_sha256': corpus['manifest_sha256'],
        'software': software, 'splits': sorted(selected), 'protocol': protocol, 'environment': {'numpy': np.__version__, 'sklearn': sklearn.__version__}})
    start = time.monotonic(); cases = []; failures = []
    for asset, history in corpus['histories'].items():
        for window in protocol['windows']:
            if window['split'] not in selected: continue
            try:
                case = fit_case(history, protocol, window); cases.append(case)
                atomic(output/'cases'/f'{asset}-{window["id"]}.json', case)
            except (ValueError, RuntimeError, FloatingPointError) as exc:
                failures.append({'asset': asset, 'window': window, 'error': str(exc), 'scope': 'Whole case excluded from every variant, not a favorable subset'})
            atomic(output/'progress.json', {'finished_cases': len(cases), 'failures': failures, 'elapsed_seconds': time.monotonic()-start})
    result = {'version': VERSION, 'corpus_manifest_sha256': corpus['manifest_sha256'], 'catalog': signal_catalog(),
        'summary': summarize(cases, failures), 'failures': failures, 'planned_cases': sum(w['split'] in selected for w in protocol['windows'])*len(protocol['assets']),
        'completed_cases': len(cases), 'elapsed_seconds': time.monotonic()-start, 'network_calls': 0, 'model_api_calls': 0,
        'limitations': LIMITATIONS, 'input_lock_sha256': digest(output/'input_lock.json')}
    atomic(output/'result.json', result)
    atomic(output/'artifacts.json', {str(p.relative_to(output)): digest(p) for p in sorted(output.rglob('*')) if p.is_file() and p.name != 'artifacts.json'})
    return result
