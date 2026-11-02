"""Optional task-local Jev -> Pro routing over unchanged semantic inputs.

Services are supplied by the host; this module reads no configuration, creates
no provider clients, modifies no evidence and replaces no publication review.
Routing adds checks/cost unless it actually avoids another provider invocation.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from threading import RLock
from time import perf_counter

from .evidence_judgments import QUESTIONS
from .semantic_costs import estimate_cost, token_count

POLICY_VERSION = 'jev-pro-conservative-routing-v1'
PROVIDERS = ('jev', 'doubao')
SCOPE = ('Derived routing of optional source judgments; preserve original evidence, '
         'uncertainty and contrary information. Existing publication review remains required.')


def _number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _money(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _physical_count(result):
    observed = _count(result.get('physical_request_count'))
    # An observed HTTP request cannot disappear behind a contradictory cache
    # or billing flag. Explicit no-request metadata is used only without a
    # supplied physical count (as on a preflight unavailable result).
    if observed is not None:return observed
    if result.get('cache_hit') or result.get('billable_request_made') is False:return 0
    return None


def _submitted(result):
    count = _physical_count(result)
    return bool(count) or (count is None and result.get('billable_request_made') is not False and not result.get('cache_hit'))


class RoutedJudgmentService:
    """Bounded optional service compatible with ``EvidenceJudgmentService.assess``.

    Construct preconfigured single-attempt services, then pass them here:
    ``RoutedJudgmentService(jev_service, pro_service, primary_limit=12,
    fallback_limit=8)``. Results expose both original provider outcomes in
    ``routing.trace`` and separate provider totals; there is no blended score.
    """

    def __init__(self, primary, fallback, *, primary_limit=12, fallback_limit=8,
                 minimum_confidence=.8, record=None):
        for limit in (primary_limit, fallback_limit):
            if _count(limit) is None or limit > 128:
                raise ValueError('Use provider request budgets in 0..128')
        if not _number(minimum_confidence) or not 0 <= minimum_confidence <= 1:
            raise ValueError('Use a finite predeclared confidence threshold')
        for service, provider, model in ((primary, 'jev', 'jev-1.14.0'),
                (fallback, 'doubao', 'doubao-seed-2.1-pro')):
            if getattr(service, 'provider', None) != provider or getattr(service, 'requested_model', None) != model:
                raise ValueError('Supply the pinned Jev and authorized Pro services')
            if getattr(service, 'max_attempts', None) != 1:
                raise ValueError('Routed services require explicit single-attempt transport')
            if getattr(service, 'minimum_confidence', None) != minimum_confidence:
                raise ValueError('Provider and routing confidence policies must match')
        if record is not None and not callable(record):
            raise ValueError('record must be a host callback or None')
        self.primary, self.fallback = primary, fallback
        self.minimum_confidence, self.record = minimum_confidence, record
        self.provider = 'routed_jev_doubao'
        self.requested_model = 'jev-1.14.0->doubao-seed-2.1-pro'
        self._lock = RLock()
        self._cache = {}; self._receipts = {}
        self._logical_calls = 0; self._router_cache_hits = 0
        self._stats = {provider: {'limit': limit, 'budget_charged': 0,
            'invocations': 0, 'physical_requests': 0, 'unknown_physical_counts': 0,
            'cache_hits': 0, 'submitted_calls': 0, 'input_tokens': 0,
            'output_tokens': 0, 'input_usage_responses': 0,
            'output_usage_responses': 0, 'usage_unknown_responses': 0,
            'billing_unknown_responses': 0, 'known_estimated_usd': Decimal(0),
            'known_estimated_afp': Decimal(0), 'usd_responses': 0,
            'afp_responses': 0, 'wall_seconds': 0.0}
            for provider, limit in zip(PROVIDERS, (primary_limit, fallback_limit))}

    def _provider_totals(self, entries=None):
        stats = self._stats if entries is None else entries
        result = {}
        for provider, values in stats.items():
            row = deepcopy(values)
            for field in ('known_estimated_usd', 'known_estimated_afp'):
                row[field] = str(row[field])
            row['budget_remaining'] = max(0, self._stats[provider]['limit'] - self._stats[provider]['budget_charged'])
            row['observed_over_budget'] = self._stats[provider]['budget_charged'] > self._stats[provider]['limit']
            # Partial sums are explicitly separate from unknown responses.
            if row['submitted_calls'] and not row['input_usage_responses']:
                row['input_tokens'] = None
            if row['submitted_calls'] and not row['output_usage_responses']:
                row['output_tokens'] = None
            if row['submitted_calls'] and not row['usd_responses']:
                row['known_estimated_usd'] = None
            if row['submitted_calls'] and not row['afp_responses']:
                row['known_estimated_afp'] = None
            row['actual_account_debit'] = None
            row['physical_request_count'] = None if row['unknown_physical_counts'] else row['physical_requests']
            row['known_physical_requests'] = row['physical_requests']
            result[provider] = row
        return result

    def metrics(self):
        with self._lock:
            return {'policy_version': POLICY_VERSION, 'logical_calls': self._logical_calls,
                'router_cache_hits': self._router_cache_hits,
                'providers': self._provider_totals(),
                'scope': 'Cumulative task-local provider calls/partial usage. USD and AFP are separate units; no cash savings claim.'}

    def receipt(self, cache_key):
        with self._lock:
            return deepcopy(self._receipts.get(cache_key))

    @staticmethod
    def _bound_result(task, ids, criterion, provider):
        return {'status': 'unavailable', 'task': task, 'label': None,
            'labels': [], 'scores': {}, 'confidence': None, 'provider': provider,
            'evidence_ids': ids, 'criterion_version': criterion,
            'reason': 'Provider routing budget exhausted; keep evidence and uncertainty',
            'billable_request_made': False, 'physical_request_count': 0,
            'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0},
            'cache_hit': False, 'scope': SCOPE}

    def _invoke(self, service, task, arguments, trace):
        provider = service.provider; stats = self._stats[provider]
        if stats['budget_charged'] >= stats['limit']:
            return self._bound_result(task, [r.get('record_id') for r in arguments['evidence']],
                arguments['criterion_version'], provider), False
        start = perf_counter(); stats['invocations'] += 1
        try:
            result = service.assess(task, **deepcopy(arguments))
            if not isinstance(result, dict):
                raise TypeError('Provider service did not return a mapping')
            result = deepcopy(result)
        except Exception as exc:
            # Input/privacy rejection must not be retried through another
            # provider. Only the exception type is retained; never raw text.
            result = {'status': 'error', 'task': task, 'provider': provider,
                'label': None, 'labels': [], 'scores': {}, 'confidence': None,
                'criterion_version': arguments['criterion_version'],
                'evidence_ids': [r.get('record_id') for r in arguments['evidence']],
                'error_type': type(exc).__name__, 'service_exception': True,
                'physical_request_count': None, 'billable_request_made': True,
                'usage': None, 'cache_hit': False, 'scope': SCOPE}
        wall = perf_counter() - start; stats['wall_seconds'] += wall
        count = _physical_count(result)
        sent = _submitted(result)
        if count is None:
            stats['unknown_physical_counts'] += 1; stats['budget_charged'] += 1
        else:
            stats['physical_requests'] += count; stats['budget_charged'] += count
        if result.get('cache_hit'):stats['cache_hits'] += 1
        if sent or count:
            stats['submitted_calls'] += 1
            usage = result.get('usage')
            input_count, output_count = token_count(usage, 'input_tokens'), token_count(usage, 'output_tokens')
            for name, value in (('input', input_count), ('output', output_count)):
                if value is not None:
                    stats[name+'_tokens'] += value; stats[name+'_usage_responses'] += 1
            if input_count is None or output_count is None:
                stats['usage_unknown_responses'] += 1
            bill = result.get('billing') or estimate_cost(result)
            for name in ('usd', 'afp'):
                amount = _money(bill.get('estimated_'+name))
                if amount is not None:
                    stats['known_estimated_'+name] += amount; stats[name+'_responses'] += 1
            if not bill.get('complete_usage'):
                stats['billing_unknown_responses'] += 1
        trace.append({'role': 'primary' if provider == 'jev' else 'fallback',
            'provider': provider, 'wall_seconds': wall, 'result': result})
        return result, True

    def _unresolved(self, task, result):
        unresolved = []
        for name in QUESTIONS[task]:
            answer = (result.get('dimensions') or {}).get(name) or {}
            confidence = answer.get('confidence')
            if (not _number(confidence) or not 0 <= confidence <= 1 or confidence < self.minimum_confidence or
                    answer.get('choice') not in QUESTIONS[task][name]['criteria'] or answer.get('choice') == 'uncertain'):
                unresolved.append(name)
        return unresolved

    def _fallback_reasons(self, task, result):
        reasons = []
        if result.get('status') != 'ok':reasons.append('primary_'+str(result.get('status', 'unknown')))
        reasons.extend('unresolved_'+name for name in self._unresolved(task, result))
        dimensions = result.get('dimensions') or {}
        if task == 'relevance' and (dimensions.get('instruction_risk') or {}).get('choice') == 'present':
            reasons.append('primary_instruction_risk_present')
        if task == 'relation' and (dimensions.get('relation') or {}).get('choice') == 'insufficient':
            reasons.append('primary_relation_insufficient')
        if task == 'support' and (dimensions.get('support') or {}).get('choice') in ('insufficient', 'conditional'):
            reasons.append('primary_support_requires_review')
        return reasons

    @staticmethod
    def _identity_valid(result, task, arguments, provider):
        return (result.get('provider') == provider and result.get('task') == task and
            not (provider == 'jev' and (result.get('model') not in (None,'jev-1.14.0') or
                result.get('status') == 'ok' and result.get('model') != 'jev-1.14.0')) and
            result.get('evidence_ids') == [r.get('record_id') for r in arguments['evidence']] and
            result.get('criterion_version') == arguments['criterion_version'] and
            isinstance(result.get('input_hash'), str) and bool(re.fullmatch(r'[a-f0-9]{64}', result['input_hash'])))

    def assess(self, task, *, question='', evidence, criterion_version='v1'):
        if task not in QUESTIONS:raise ValueError('Unknown routing task')
        if not isinstance(question, str) or len(question)>8000 or not isinstance(criterion_version,str) or not 1<=len(criterion_version)<=100:
            raise ValueError('Use a bounded question and explicit criterion version')
        if not isinstance(evidence, (list,tuple)) or len(evidence)>8 or any(not isinstance(row,dict) or not isinstance(row.get('record_id'),str) for row in evidence):
            raise ValueError('Supply bounded saved evidence records')
        assess_started=perf_counter()
        arguments = {'question': question, 'evidence': deepcopy(evidence), 'criterion_version': criterion_version}
        key = _hash({'policy': POLICY_VERSION, 'threshold': self.minimum_confidence,
            'task': task, 'arguments': arguments})
        with self._lock:
            self._logical_calls += 1
            if key in self._cache:
                self._router_cache_hits += 1; cached = deepcopy(self._cache[key])
                cached['original_usage'] = cached.get('usage'); cached['original_billing'] = cached.get('billing')
                cached.update(cache_hit=True, billable_request_made=False, physical_request_count=0,
                    request_count=0, usage={'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}, latency_seconds=perf_counter()-assess_started)
                cached['routing']['historical_trace'] = cached['routing']['trace']; cached['routing']['trace'] = []
                cached['routing']['historical_provider_totals'] = cached['routing']['provider_totals']
                zero={p:{k:(v if k=='limit' else Decimal(0) if isinstance(v,Decimal) else 0) for k,v in stats.items()} for p,stats in self._stats.items()}
                cached['routing']['provider_totals'] = self._provider_totals(zero)
                cached['routing']['budgets_after'] = {p:self._provider_totals()[p]['budget_remaining'] for p in PROVIDERS}
                cached['routing']['router_cache_replay'] = True
                cached['billing'] = {'estimated_usd': '0', 'estimated_afp': '0', 'actual_account_debit': None,
                    'scope': 'No new provider calls; historical route costs remain in original_billing/historical_trace.'}
                return cached
            start = perf_counter(); trace = []; before = deepcopy(self._stats)
            primary, primary_called = self._invoke(self.primary, task, arguments, trace)
            reasons = self._fallback_reasons(task, primary)
            if not primary_called:reasons.insert(0, 'primary_budget_exhausted')
            invalid_primary = primary_called and not primary.get('service_exception') and not self._identity_valid(primary, task, arguments, 'jev')
            if invalid_primary:reasons.append('primary_identity_invalid')
            fallback = None; fallback_called = False; selected = primary
            # Services own source/privacy validation. A pre-provider exception
            # or wrong semantic identity cannot be bypassed via fallback.
            if reasons and primary_called and not primary.get('service_exception') and not invalid_primary:
                fallback, fallback_called = self._invoke(self.fallback, task, arguments, trace)
                if fallback_called and self._identity_valid(fallback, task, arguments, 'doubao'):
                    if primary_called and fallback['input_hash'] != primary.get('input_hash'):
                        reasons.append('fallback_input_hash_mismatch')
                    elif fallback.get('status') in ('ok', 'abstained'):
                        selected = fallback
                elif fallback_called:reasons.append('fallback_identity_invalid')
                else:reasons.append('fallback_budget_exhausted')
            invalid_identity = invalid_primary or any(reason in reasons for reason in ('fallback_input_hash_mismatch', 'fallback_identity_invalid'))
            disagreements = []; alerts = []
            for entry in trace:
                dims = entry['result'].get('dimensions') or {}
                if task == 'relevance' and (dims.get('instruction_risk') or {}).get('choice') == 'present':alerts.append('instruction_risk_present')
                if task == 'relation' and (dims.get('relation') or {}).get('choice') in ('contradicts','supersedes','update'):
                    alerts.append('possible_conflict_or_update_keep_both_sources')
            if primary_called and fallback_called and fallback and not invalid_identity:
                for name in QUESTIONS[task]:
                    a = (primary.get('dimensions') or {}).get(name) or {}; b = (fallback.get('dimensions') or {}).get(name) or {}
                    if (a.get('choice') != b.get('choice') and _number(a.get('confidence')) and _number(b.get('confidence')) and
                            a['confidence'] >= self.minimum_confidence and b['confidence'] >= self.minimum_confidence):
                        disagreements.append(name)
            result = deepcopy(selected); unresolved = self._unresolved(task, selected)
            unresolved = sorted(set(unresolved+disagreements+(['instruction_risk'] if 'instruction_risk_present' in alerts else [])))
            accepted = {name: deepcopy(answer) for name, answer in (selected.get('dimensions') or {}).items() if name in QUESTIONS[task] and name not in unresolved}
            force_abstain = bool(unresolved or invalid_identity or disagreements or 'instruction_risk_present' in alerts)
            if force_abstain or result.get('status') not in ('ok','abstained'):
                result.update(status='abstained' if result.get('status') in ('ok','abstained') else result.get('status','unavailable'), label=None, labels=[])
            else:result['labels'] = [name+':'+answer['choice'] for name, answer in accepted.items()]
            if invalid_identity:result.update(status='error', label=None, labels=[], error='Routed provider semantic identity mismatch')
            delta = {provider: {name: self._stats[provider][name] - before[provider][name]
                if name != 'limit' else self._stats[provider]['limit'] for name in self._stats[provider]} for provider in PROVIDERS}
            totals = self._provider_totals(delta)
            counts = [_physical_count(entry['result']) for entry in trace]
            sent = any(_submitted(entry['result']) for entry in trace)
            result.update(provider=self.provider, selected_provider=selected.get('provider'), cache_key=key,
                task=task, criterion_version=criterion_version, scope=SCOPE,
                billable_request_made=sent, physical_request_count=sum(counts) if all(c is not None for c in counts) else None,
                request_count=sum(counts) if all(c is not None for c in counts) else None,
                cache_hit=bool(trace) and not sent and all(entry['result'].get('cache_hit') for entry in trace),
                usage=selected.get('usage') if len(trace) == 1 else None, latency_seconds=perf_counter()-start)
            result['billing'] = {'estimated_usd': totals['jev']['known_estimated_usd'] if not totals['doubao']['submitted_calls'] else None,
                'known_jev_estimated_usd': totals['jev']['known_estimated_usd'], 'estimated_afp': totals['doubao']['known_estimated_afp'],
                'provider_totals': totals, 'actual_account_debit': None,
                'scope': 'Composite route: add Jev and fallback work, never compare USD with AFP or claim a saved request without the baseline.'}
            result['routing'] = {'policy_version': POLICY_VERSION, 'minimum_confidence': self.minimum_confidence,
                'fallback_reasons': list(dict.fromkeys(reasons)), 'fallback_called': fallback_called,
                'selected_provider': selected.get('provider'), 'trace': trace, 'provider_totals': totals,
                'accepted_dimensions': accepted if not invalid_identity else {}, 'unresolved_dimensions': unresolved,
                'disagreements': disagreements, 'alerts': sorted(set(alerts)), 'retain_all_source_evidence': True,
                'publication_review_required': True, 'needs_source_review': bool(reasons or alerts or disagreements),
                'budgets_after': {p: self._provider_totals()[p]['budget_remaining'] for p in PROVIDERS}}
            if self.record:
                try:self.record(deepcopy(result))
                except Exception as exc:result['routing']['recording_error_type'] = type(exc).__name__
            self._receipts[key] = deepcopy(result)
            if result.get('status') in ('ok','abstained'):self._cache[key] = deepcopy(result)
            return result
