"""Deterministic, training-free evidence packing over a frozen candidate pool.

The coverage objective is weighted facility location. Explicit dependency closure
adds constraints; no unconstrained approximation guarantee is claimed for it.
The cost is a labelled UTF-8 heuristic, not the provider's actual token count.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from research_data.http import dumps
from research_data.library.time import timestamp


POLICIES = ('coverage', 'relevance', 'mmr', 'recent', 'semantic_coverage','hybrid_relevance')


def context_cost(record):
    return max(1, math.ceil(len(dumps(record, sort_keys=True).encode('utf-8')) / 3))


def select_evidence(candidates, query='', *, budget=2400, limit=8,
                    policy='coverage', as_of=None, dependencies=None, group_duplicates=True,
                    semantic_scores=None, required_ids=None, retrieval_scores=None):
    if policy not in POLICIES:
        raise ValueError('Unknown memory selection policy')
    if not 1 <= budget <= 64000 or not 1 <= limit <= 30:
        raise ValueError('Memory budget must be 1..64000 and limit 1..30')
    if len(candidates) > 150:
        raise ValueError('Candidate pool exceeds the bounded local selection limit')
    cutoff = timestamp(as_of) if as_of else None
    excluded = []
    rows = []
    seen = {}
    for row in candidates:
        rid = row['record_id']
        if rid in seen:
            if seen[rid] != dumps(row, sort_keys=True):
                raise ValueError('A candidate evidence ID cannot be rebound to different content')
            continue
        seen[rid] = dumps(row, sort_keys=True)
        known = row.get('first_seen_at') or row.get('retrieved_at')
        dates = [value for value in (known, row.get('published_at')) if value]
        try:
            parsed = [timestamp(value) for value in dates]
        except (ValueError, TypeError, OverflowError):
            excluded.append({'record_id': rid, 'reason': 'invalid_timestamp'})
            continue
        if cutoff and (not known or any(value > cutoff for value in parsed)):
            excluded.append({'record_id': rid, 'reason': 'not_known_by_cutoff'})
            continue
        rows.append(dict(row))
    meta = {'policy': policy, 'algorithm': 'semantic-facility-coverage-greedy-v1' if policy == 'semantic_coverage' else 'facility-coverage-greedy-v1' if policy == 'coverage' else policy,
            'candidate_count': len(candidates), 'eligible_count': len(rows),
            'budget': budget, 'cost_estimate': 0, 'cost_unit': 'ceil(UTF8 serialized evidence bytes / 3); not provider tokens',
            'excluded': excluded, 'trace': [],
            'scope': 'Evidence selection only; similarity and repeated coverage do not prove truth or source independence.'}
    if not rows:
        return {'results': [], 'selection': meta}
    texts = [(row.get('title', '') + ' ' + (row.get('excerpt') or row.get('text') or '')).strip()
             for row in rows]
    # Corpus statistics only; no learned language model or hosted embedding API.
    try:
        vectorizer = TfidfVectorizer(analyzer='char_wb', ngram_range=(3, 5), max_features=12000)
        matrix = vectorizer.fit_transform([*texts, query or ' '])
        similarity = (matrix[:-1] @ matrix[:-1].T).toarray()
        relevance = (matrix[:-1] @ matrix[-1].T).toarray().ravel() if query.strip() else np.ones(len(rows))
    except ValueError:  # An empty textual pool cannot support a meaningful query.
        similarity = np.eye(len(rows))
        relevance = np.zeros(len(rows)) if query.strip() else np.ones(len(rows))
    similarity = np.clip(similarity, 0, 1)
    relevance = np.clip(relevance, 0, 1)
    if policy=='hybrid_relevance':
        scores=retrieval_scores or {}
        for row in rows:
            score=scores.get(row['record_id'],0)
            if not isinstance(score,(int,float)) or not math.isfinite(score) or not 0<=score<=1:
                raise ValueError('Host retrieval scores must be finite values in [0,1]')
        relevance=np.array([max(float(relevance[i])*.4,float(scores.get(row['record_id'],0))) for i,row in enumerate(rows)])
        meta['retrieval_score_scope']='Host reciprocal-rank navigation score, not truth/confidence or a learned policy.'
    semantic_scores=semantic_scores or {}
    for rid,score in semantic_scores.items():
        if not isinstance(score,(int,float)) or not math.isfinite(score) or not 0<=score<=1:
            raise ValueError('Semantic relevance scores must be finite values in [0,1]')
    if policy=='semantic_coverage':
        relevance=np.array([.5*float(relevance[i])+.5*semantic_scores[row['record_id']]
                            if row['record_id'] in semantic_scores else float(relevance[i])
                            for i,row in enumerate(rows)])
    meta['semantic_scores_used']=len([r for r in rows if r['record_id'] in semantic_scores]) if policy=='semantic_coverage' else 0
    if query.strip() and not np.any(relevance > 1e-12) and not required_ids:
        meta['reason'] = 'no_lexical_evidence_match'
        return {'results': [], 'selection': meta}

    # Known same-origin URLs and exact repeated text reduce duplicate demand.
    # This groups repetitions; it does not infer that other publishers corroborate.
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    keys = {}
    for i, (row, text) in enumerate(zip(rows, texts)):
        origin = row.get('origin_key') or row.get('url')
        digest = hashlib.sha256(' '.join(text.casefold().split()).encode()).hexdigest()
        for key in ([('origin', origin)] if origin else []) + [('text', digest)]:
            if key in keys:
                parent[find(i)] = find(keys[key])
            keys[key] = i
    counts = Counter(find(i) for i in range(len(rows)))
    weights = np.array([relevance[i] / (counts[find(i)] if group_duplicates else 1) for i in range(len(rows))])
    meta['duplicate_demand_weighting']=group_duplicates
    index = {row['record_id']: i for i, row in enumerate(rows)}
    costs = np.array([context_cost(row) for row in rows])
    links = dependencies or {}

    def closure(i):
        pending = [i]
        result = set()
        while pending:
            node = pending.pop()
            if node in result:
                continue
            result.add(node)
            for rid in links.get(rows[node]['record_id'], []):
                if rid not in index:
                    return None
                pending.append(index[rid])
        return result

    bundles = {i: closure(i) for i in range(len(rows))}
    selected = set()
    order = []
    coverage = np.zeros(len(rows))
    spent = 0
    protected=list(dict.fromkeys(required_ids or []))
    if protected:
        missing=[rid for rid in protected if rid not in index or bundles[index[rid]] is None]
        required=set().union(*(bundles[index[rid]] for rid in protected if rid in index and bundles[index[rid]] is not None))
        required_cost=int(sum(costs[i] for i in required))
        if missing or len(required)>limit or required_cost>budget:
            meta.update(reason='protected_evidence_bundle_does_not_fit',protected_ids=protected,
                        missing_protected_ids=missing,required_cost=required_cost,required_count=len(required),selected_count=0)
            return {'results':[],'selection':meta}
        order=[i for i in range(len(rows)) if i in required];selected=required;spent=required_cost
        coverage=similarity[:,sorted(required)].max(axis=1)
        meta['trace'].append({'anchor':'protected_evidence','added':[rows[i]['record_id'] for i in order],
                              'incremental_cost':required_cost,'coverage_gain':float(weights@coverage)})
        meta['protected_ids']=protected
    while len(selected) < limit:
        best = None
        for i in range(len(rows)):
            if i in selected or bundles[i] is None:
                continue
            new = bundles[i] - selected
            cost = int(sum(costs[j] for j in new))
            if len(selected | new) > limit or spent + cost > budget:
                continue
            covered = np.maximum(coverage, similarity[:, sorted(new)].max(axis=1))
            gain = float(weights @ (covered - coverage))
            if policy in ('coverage','semantic_coverage'):
                score = gain / max(cost, 1)
            elif policy in ('relevance','hybrid_relevance'):
                score = float(relevance[i])
            elif policy == 'mmr':
                redundancy = max((similarity[i, j] for j in selected), default=0)
                score = float(.7 * relevance[i] - .3 * redundancy)
            else:
                score = float(len(rows) - i)
            candidate = (score, float(relevance[i]), -i, i, new, cost, covered, gain)
            if best is None or candidate[:3] > best[:3]:
                best = candidate
        if best is None or (policy in ('coverage','semantic_coverage') and best[0] <= 1e-12):
            break
        _, _, _, anchor, new, cost, coverage, gain = best
        ordered = [anchor, *sorted(new - {anchor})]
        order.extend(j for j in ordered if j not in selected)
        selected.update(new)
        spent += cost
        meta['trace'].append({'anchor': rows[anchor]['record_id'],
                              'added': [rows[j]['record_id'] for j in ordered if j in new],
                              'incremental_cost': cost, 'coverage_gain': gain})
    meta.update(cost_estimate=spent, selected_count=len(selected),
                unavailable_dependency_count=sum(bundle is None for bundle in bundles.values()),
                estimated_coverage=float(weights @ coverage))
    if not order:
        meta['reason'] = 'no_complete_evidence_bundle_fits'
    return {'results': [rows[i] for i in order], 'selection': meta}
