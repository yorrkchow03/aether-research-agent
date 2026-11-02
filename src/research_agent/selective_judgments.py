"""Task-selective optional judgments, with source-bound classification reuse.

Provider services and storage policy are supplied by the host. This module does
not load credentials, build clients, relabel evidence, or replace publication
review. Only classification may use separately supplied persistent services;
question-dependent judgments always use the task-local services.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from time import perf_counter

from .evidence_judgments import QUESTIONS
from .semantic_routing import (
    RoutedJudgmentService, PROVIDERS, SCOPE, _hash, _physical_count, _submitted,
)

POLICY_VERSION = 'selective-jev-classification-relevance-pro-relation-support-v1'
JEV_TASKS = frozenset({'classification', 'relevance'})
DIRECT_PRO_TASKS = frozenset({'relation', 'support'})


class _ClassificationDispatch:
    """Keep persistent source annotations separate from dynamic query caches."""
    def __init__(self, local, classification=None):
        for field in ('provider', 'requested_model', 'minimum_confidence', 'max_attempts'):
            value = getattr(local, field, None)
            if classification is not None and getattr(classification, field, None) != value:
                raise ValueError('Classification and local services must share the pinned model and policy')
            setattr(self, field, value)
        if classification is not None and getattr(local, 'data_dir', None) is not None and getattr(classification, 'data_dir', None) is not None:
            if local.data_dir == classification.data_dir:
                raise ValueError('Use a distinct store for reusable classification judgments')
        self.local, self.classification = local, classification

    def assess(self, task, **arguments):
        service = self.classification if task == 'classification' and self.classification is not None else self.local
        # Preserve question, source content/clocks and criterion verbatim. The
        # underlying service's complete semantic input hash is the cache key.
        return service.assess(task, **arguments)


class SelectiveJudgmentService(RoutedJudgmentService):
    """Use Jev for reusable classification/relevance; Pro for complex checks.

``primary`` and ``pro`` must be task-local EvidenceJudgmentService-compatible
objects. Optional ``classification_primary`` / ``classification_pro`` are host
services backed by separate persistent annotation stores. They are never used
for relevance, relationships, or generated claims. ``pro_limit`` is a single
physical-request budget shared by direct Pro checks and uncertain-Jev fallback.
All uncertainty, injection and disagreement behavior on Jev tasks comes from
the established conservative router.
    """
    def __init__(self, primary, pro, *, classification_primary=None,
                 classification_pro=None, primary_limit=12, pro_limit=16,
                 minimum_confidence=.8, record=None):
        super().__init__(_ClassificationDispatch(primary, classification_primary),
            _ClassificationDispatch(pro, classification_pro), primary_limit=primary_limit,
            fallback_limit=pro_limit, minimum_confidence=minimum_confidence)
        if record is not None and not callable(record):
            raise ValueError('record must be a host callback or None')
        self.provider = 'selective_jev_doubao'
        self.requested_model = 'jev-1.14.0|doubao-seed-2.1-pro'
        self.selection_record = record
        self.shared_classification = classification_primary is not None or classification_pro is not None
        self._selection_counts = {task: 0 for task in QUESTIONS}

    def metrics(self):
        with self._lock:
            result = super().metrics()
            result.update(policy_version=POLICY_VERSION,
                selection_counts=deepcopy(self._selection_counts),
                task_routes={task: 'jev_then_pro_if_uncertain' if task in JEV_TASKS else 'direct_pro'
                             for task in QUESTIONS},
                shared_classification=self.shared_classification)
            return result

    def _record_selection(self, result, task):
        result['routing']['engine_policy_version'] = result['routing']['policy_version']
        result['routing'].update(policy_version=POLICY_VERSION,
            selection='jev_then_pro_if_uncertain' if task in JEV_TASKS else 'direct_pro',
            classification_cache='host_supplied_source_bound_store' if self.shared_classification else 'task_local',
            question_dependent_cache='task_local',
            retain_all_source_evidence=True, publication_review_required=True)
        if self.selection_record is not None:
            try:self.selection_record(deepcopy(result))
            except Exception as exc:result['routing']['recording_error_type'] = type(exc).__name__
        self._receipts[result['cache_key']] = deepcopy(result)
        return result

    def assess(self, task, *, question='', evidence, criterion_version='v1'):
        # Match the established router's host-side input contract before any
        # service invocation, without attempting to bypass its privacy checks.
        if task not in QUESTIONS:
            raise ValueError('Unknown selection task')
        if not isinstance(question, str) or len(question) > 8000 or not isinstance(criterion_version, str) or not 1 <= len(criterion_version) <= 100:
            raise ValueError('Use a bounded question and explicit criterion version')
        if not isinstance(evidence, (list, tuple)) or len(evidence) > 8 or any(not isinstance(row, dict) or not isinstance(row.get('record_id'), str) for row in evidence):
            raise ValueError('Supply bounded saved evidence records')
        with self._lock:
            self._selection_counts[task] += 1
            if task in JEV_TASKS:
                result = super().assess(task, question=question, evidence=evidence,
                    criterion_version=criterion_version)
                return self._record_selection(result, task)
            return self._direct_pro(task, question, evidence, criterion_version)

    def _direct_pro(self, task, question, evidence, criterion):
        start = perf_counter()
        arguments = {'question': question, 'evidence': deepcopy(evidence), 'criterion_version': criterion}
        key = _hash({'policy': POLICY_VERSION, 'threshold': self.minimum_confidence,
            'task': task, 'arguments': arguments})
        self._logical_calls += 1
        if key in self._cache:
            self._router_cache_hits += 1
            result = deepcopy(self._cache[key])
            result['original_usage'], result['original_billing'] = result.get('usage'), result.get('billing')
            result.update(cache_hit=True, billable_request_made=False,
                physical_request_count=0, request_count=0,
                usage={'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0},
                latency_seconds=perf_counter()-start)
            routing = result['routing']
            routing['historical_trace'] = routing['trace']; routing['trace'] = []
            routing['historical_provider_totals'] = routing['provider_totals']
            zero = {provider: {name: value if name == 'limit' else Decimal(0) if isinstance(value, Decimal) else 0
                     for name, value in stats.items()} for provider, stats in self._stats.items()}
            routing['provider_totals'] = self._provider_totals(zero)
            routing['budgets_after'] = {p:self._provider_totals()[p]['budget_remaining'] for p in PROVIDERS}
            routing['router_cache_replay'] = True
            result['billing'] = {'estimated_usd': '0', 'estimated_afp': '0',
                'actual_account_debit': None,
                'scope': 'No new provider request; historical direct-check costs remain separately retained.'}
            return self._record_selection(result, task)
        before = deepcopy(self._stats); trace = []
        raw, called = self._invoke(self.fallback, task, arguments, trace)
        if trace:trace[0]['role'] = 'direct_pro'
        result = deepcopy(raw)
        identity_invalid = called and not raw.get('service_exception') and not self._identity_valid(raw, task, arguments, 'doubao')
        unresolved = self._unresolved(task, raw)
        if identity_invalid:
            result.update(status='error', label=None, labels=[], error='Direct Pro semantic identity mismatch')
        elif unresolved or result.get('status') not in ('ok', 'abstained'):
            result.update(status='abstained' if result.get('status') in ('ok', 'abstained') else result.get('status', 'unavailable'),
                label=None, labels=[])
        accepted = {name: deepcopy(answer) for name, answer in (raw.get('dimensions') or {}).items()
                    if name in QUESTIONS[task] and name not in unresolved and not identity_invalid
                    and raw.get('status') in ('ok', 'abstained')}
        alerts = []
        if task == 'relation' and (raw.get('dimensions') or {}).get('relation', {}).get('choice') in ('contradicts', 'supersedes', 'update'):
            alerts.append('possible_conflict_or_update_keep_both_sources')
        delta = {provider: {name: self._stats[provider][name]-before[provider][name]
                 if name != 'limit' else self._stats[provider][name] for name in self._stats[provider]}
                 for provider in PROVIDERS}
        totals = self._provider_totals(delta)
        count = _physical_count(raw) if called else 0
        result.update(provider=self.provider, selected_provider='doubao', cache_key=key,
            task=task, criterion_version=criterion, evidence_ids=[r['record_id'] for r in evidence],
            billable_request_made=called and _submitted(raw), physical_request_count=count,
            request_count=count, latency_seconds=perf_counter()-start, scope=SCOPE)
        result['billing'] = {'estimated_usd': None if _submitted(raw) else '0',
            'known_jev_estimated_usd': '0', 'estimated_afp': totals['doubao']['known_estimated_afp'],
            'provider_totals': totals, 'actual_account_debit': None,
            'scope': 'Direct Pro optional check; separate AFP estimate, not cash cost or a saved-request claim.'}
        result['routing'] = {'policy_version': POLICY_VERSION, 'minimum_confidence': self.minimum_confidence,
            'fallback_reasons': ['pro_budget_exhausted'] if not called else [], 'fallback_called': False,
            'selected_provider': 'doubao', 'trace': trace, 'provider_totals': totals,
            'accepted_dimensions': accepted, 'unresolved_dimensions': unresolved,
            'disagreements': [], 'alerts': alerts, 'retain_all_source_evidence': True,
            'publication_review_required': True,
            'needs_source_review': bool(alerts or unresolved or identity_invalid or result.get('status') != 'ok'
                                       or result.get('label') in ('insufficient', 'conditional', 'contradicted')),
            'budgets_after': {p:self._provider_totals()[p]['budget_remaining'] for p in PROVIDERS}}
        result = self._record_selection(result, task)
        if result.get('status') in ('ok', 'abstained'):self._cache[key] = deepcopy(result)
        return result
