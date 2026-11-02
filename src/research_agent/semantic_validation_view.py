"""Read-only projection of a saved semantic-validation summary.

Never imports a provider, reads credentials, launches evaluation, or exposes
requests, examples, source text or arbitrary artifact paths to the browser.
Unknown measurements stay unknown instead of being presented as zero.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

SCHEMA = 'semantic-validation-public-v1'
MAX_BYTES = 262144
PROVIDERS = ('jev', 'doubao', 'lexical', 'no_sidecar')
TASKS = {'classification': 'Source classification', 'relevance': 'Context relevance',
         'relation': 'Context relationships', 'support': 'Final source support'}
STAGES = {'classification': 'Source intake', 'relevance': 'Context selection',
          'relation': 'Context selection', 'support': 'Final review'}
PRICE_LINKS = [
    {'title': 'Jev model prices', 'url': 'https://docs.typesafe.ai/models'},
    {'title': 'Shared-state questions', 'url': 'https://docs.typesafe.ai/cookbooks/parallel_questions'},
    {'title': 'Agent Plan AFP rules', 'url': 'https://docs.volcengine.com/docs/ark/agent-plan-personal-afp-credits-billing-rules?lang=en'},
]
LIMITATIONS = [
    'Agreement is measured against source-grounded AI labels, not human ground truth or investment accuracy.',
    'All predeclared scorable dimensions retain failed, missing and abstained outcomes; ambiguous dimensions are declared before provider runs and reported separately.',
    'Classification has three dimensions; relevance has two; relationship and support each have one.',
    'Quoted usage-based costs are estimates. Missing usage and actual cash charges remain unknown.',
    'Shadow judgments do not silently remove evidence or replace publication review.',
    'Opening or refreshing this panel only reads saved results and never starts model requests.',
]


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10_000_000 else None


def _number(value, maximum=1_000_000_000):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str) and (len(value) > 60 or not re.fullmatch(r'\d+(?:\.\d+)?(?:[eE][+-]?\d+)?', value)):
        return None
    try:
        number = Decimal(str(value))
        return float(number) if number.is_finite() and 0 <= number <= maximum else None
    except (InvalidOperation, ValueError):
        return None


def _rate(value):
    return _number(value, 1)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _model(value, provider):
    pattern = r'jev-(?:\d+\.\d+\.\d+|latest|preview)' if provider == 'jev' else r'doubao-seed-2\.1-pro(?:-[A-Za-z0-9_.-]{1,50})?'
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else None


def _time(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.isoformat() if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _metrics(value):
    value = _mapping(value)
    return {**{key: _count(value.get(key)) for key in ('planned', 'attempted', 'ok', 'abstained', 'errors', 'planned_dimensions', 'scored_dimensions', 'ambiguous_dimensions', 'matching_dimensions', 'usable_dimensions')},
        'ai_label_agreement': _rate(value.get('ai_label_agreement')),
        'usable_coverage': _rate(value.get('usable_coverage'))}


def _provider(value):
    value = _mapping(value)
    latency = _mapping(value.get('latency_seconds'))
    usage = _mapping(value.get('usage'))
    cost = _mapping(value.get('cost'))
    return {**_metrics(value),
        'splits': {name: _metrics(_mapping(value.get('splits')).get(name)) for name in ('dev', 'confirmation')},
        'tasks': {name: _metrics(_mapping(value.get('tasks')).get(name)) for name in TASKS},
        'latency_seconds': {**{name: _number(latency.get(name)) for name in ('median', 'p90', 'total')},
                            'count': _count(latency.get('count'))},
        'usage': {name: _count(usage.get(name)) for name in ('input_tokens', 'output_tokens', 'unknown_responses')},
        'cost': {'estimated_usd': _number(cost.get('estimated_usd')),
                 'estimated_afp': _number(cost.get('estimated_afp')),
                 'actual_cash_charge': None,
                 'scope': 'Published-coefficient estimate from reported usage; actual account debit and unreported usage are not verified.'}}


def _repairs(value):
    value = _mapping(value)
    result = {}
    if 'jev_rounding' in value:
        item = _mapping(value['jev_rounding'])
        result['jev_rounding'] = {key: _count(item.get(key)) for key in ('reparsed_cases', 'changed_cases', 'new_provider_requests')}
    if 'doubao_nonstream' in value:
        item = _mapping(value['doubao_nonstream'])
        result['doubao_nonstream'] = {**{key: _count(item.get(key)) for key in ('tested_cases', 'usage_responses')},
            'estimated_afp': _number(item.get('estimated_afp'))}
    if 'memory_excerpt' in value:
        item = _mapping(value['memory_excerpt'])
        result['memory_excerpt'] = {**{key: _count(item.get(key)) for key in ('queries', 'lexical_span_hits', 'before_span_hits', 'after_span_hits')},
            'posthoc_replay': item.get('posthoc_replay') if isinstance(item.get('posthoc_replay'), bool) else None}
    if result:
        result['scope'] = 'Post-study diagnostics, not added to the first 48-case comparison.'
    return result or None


def _base(mode, provider, reason):
    return {'schema': SCHEMA, 'available': False, 'reason': reason,
        'current_policy': {'mode': mode if mode in ('off', 'shadow', 'assist') else 'unknown',
            'provider': provider if provider in PROVIDERS else 'unknown',
            'scope': 'Configured research policy; not a claim that a request has succeeded.'},
        'tasks': [{'task': task, 'name': name, 'stage': STAGES[task]} for task, name in TASKS.items()],
        'pricing_links': PRICE_LINKS, 'limitations': LIMITATIONS,
        'scope': 'Read-only saved validation summary. No provider call, data capture, or evaluation starts on GET.'}


def load_semantic_validation(state_dir, *, mode='unknown', provider='unknown'):
    """Project bounded local statistics only; failure never leaks file contents."""
    result = _base(mode, provider, 'not_run')
    state_dir = Path(state_dir).resolve()
    path = state_dir / 'semantic-validation' / 'latest.json'
    try:
        if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(state_dir):
            return result | {'reason': 'unsafe_artifact_path'}
        if not path.is_file():
            return result
        if path.stat().st_size > MAX_BYTES:
            return result | {'reason': 'artifact_size_limit'}
        raw = path.read_bytes()
        if len(raw) > MAX_BYTES:
            return result | {'reason': 'artifact_size_limit'}
        def invalid_constant(_):
            raise ValueError('Non-finite validation data')
        data = json.loads(raw, parse_constant=invalid_constant)
        if not isinstance(data, dict) or data.get('schema') != SCHEMA:
            return result | {'reason': 'invalid_artifact'}
        dataset = _mapping(data.get('dataset'))
        models = _mapping(data.get('models'))
        review = _mapping(data.get('review'))
        providers = _mapping(data.get('providers'))
        digest = dataset.get('manifest_sha256')
        status = review.get('status')
        result.update(available=True, reason=None, created_at=_time(data.get('created_at')),
            dataset={**{key: _count(dataset.get(key)) for key in ('cases', 'dev_cases', 'confirmation_cases', 'event_families', 'planned_dimensions', 'scored_dimensions', 'ambiguous_dimensions')},
                'manifest_sha256': digest if isinstance(digest, str) and re.fullmatch(r'[a-f0-9]{64}', digest) else None,
                'label_basis': 'Source-grounded AI-authored labels; independent AI review is separately attributed.'},
            models={name: _model(models.get(name), name) for name in ('jev', 'doubao')},
            review={'status': status if status in ('pending', 'completed', 'reviewed', 'needs_revision', 'ai_reviewed', 'independent_ai_reviewed', 'unavailable') else 'unspecified',
                'reviewer_kind': 'independent_ai', 'reviewed_cases': _count(review.get('reviewed_cases')),
                'human_labels': 0 if _count(review.get('human_labels')) == 0 else None,
                'scope': 'Independent model review is not human annotation or a source-truth certificate.'},
            providers={name: _provider(providers[name]) for name in PROVIDERS if name in providers},
            repairs=_repairs(data.get('repairs')))
        return result
    except (OSError, UnicodeError, ValueError, TypeError):
        return result | {'available': False, 'reason': 'invalid_artifact'}
