"""Frozen retrieval benchmark with inspectable, separately authored labels.

This runner scores selected support passages, not semantic answer correctness.
No model, network, terminal, current knowledge store or future price is consulted.
"""
from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

from research_data.http import utcnow, dumps
from research_data.normalize import timestamp, iso
from .memory_selection import select_evidence

VARIANTS = {
    'recent': {'policy': 'recent'},
    'relevance': {'policy': 'relevance'},
    'mmr': {'policy': 'mmr'},
    'coverage': {'policy': 'coverage'},
    'coverage_no_origin_weighting': {'policy': 'coverage', 'group_duplicates': False},
    'coverage_no_update_dependencies': {'policy': 'coverage', 'dependencies': {}},
}
OFFLINE_FAMILIES = {
    'test_advice.py': 'conditional_advice_and_history',
    'test_memory_selection.py': 'cutoff_duplicates_and_dependency_selection',
    'test_review.py': 'semantic_review_transport_and_quote_contract',
    'test_knowledge.py': 'source_identity_versions_and_news_recall',
    'test_model_recovery.py': 'model_transport_recovery',
    'test_replay.py': 'frozen_replay_and_recipe_integrity',
    'test_data.py': 'data_availability_and_numeric_source_contract',
    'test_metrics.py': 'descriptive_metrics_and_unit_contract',
}


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def freeze_manifest(root, output):
    root, output = Path(root), Path(output)
    if output.exists():
        raise ValueError('Manifest already exists; use a new version, never overwrite')
    annotations = root / 'examples/evaluation/memory_annotations_v1.json'
    labels = json.loads(annotations.read_text())
    splits = {}
    for row in labels['groups']:
        old = splits.setdefault(row['event_group'], row['split'])
        if old != row['split']:
            raise ValueError('An event group cannot cross development/held-out splits')
    source_dir = root / 'data/validation/memory_v1'
    files = {str(annotations.relative_to(root)): digest(annotations)}
    for row in labels['groups']:
        path = source_dir / (row['source'] + '.json')
        raw = path.with_suffix('.raw')
        source = json.loads(path.read_text())
        if source['status'] != 'captured' or row['passage'] not in source['text']:
            raise ValueError('Expected support passage is absent: ' + row['source'])
        if digest(raw) != source['raw_sha256']:
            raise ValueError('Raw source changed: ' + row['source'])
        for p in (path, raw):
            files[str(p.relative_to(root))] = digest(p)
    suites = {}
    for pattern, family in OFFLINE_FAMILIES.items():
        path = root / 'tests' / pattern
        suites[str(path.relative_to(root))] = {'family': family, 'sha256': digest(path)}
    code = {}
    for name in ('memory_selection.py', 'memory_benchmark.py', 'knowledge.py', 'advice.py', 'claims.py'):
        path = root / 'src/research_agent' / name
        code[str(path.relative_to(root))] = digest(path)
    manifest = {'schema': 'aether-memory-evaluation-v1', 'created_at': utcnow(),
                'files': files, 'code': code, 'offline_suites': suites,
                'budget': 2400, 'limit': 8, 'chunk_characters': 1000, 'chunk_overlap': 150,
                'max_characters_per_source': 9000, 'variants': VARIANTS,
                'label_author': labels['label_author'],
                'availability': 'Captured now; source-snapshot support, not historical point-in-time reconstruction',
                'predeclared_metrics': ['required_passage_recall', 'source_id_recall', 'future_capture_leakage',
                    'estimated_context_cost', 'selection_latency_seconds', 'negative_question_context_coverage'],
                'answer_metrics': 'Not run by this retrieval-only runner. Abstention/unsupported-advice quality requires separate saved model answers and independent adjudication.',
                'limitations': ['Topic groups prevent companion-post split leakage, but are not independent publishers or market samples.',
                    'No multi-version outcomes for all topic groups; the twelve observed event-history target remains unmet.',
                    'All labels are AI authored and await human review.',
                    'No held-out parameter tuning; all six policies share candidates, chunking and estimated cost limits.',
                    'No explicit real correction links in this pilot: dependency ablation is unidentifiable here; synthetic tests check it.',
                    'Character TF-IDF is lexical, not a learned semantic embedding; context cost is estimated, not provider usage.',
                    'No statistical confidence or investment-return claim is justified by this small single-publisher pilot.']}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(dumps(manifest, indent=2) + '\n')
    return manifest


def load_frozen(root, manifest_path):
    root = Path(root).resolve()
    manifest = json.loads(Path(manifest_path).read_text())
    for relative, expected in {**manifest['files'], **manifest['code']}.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or digest(path) != expected:
            raise ValueError('Frozen evaluation input/code changed: ' + relative)
    labels = json.loads((root / 'examples/evaluation/memory_annotations_v1.json').read_text())
    candidates, sources = [], {}
    for label in labels['groups']:
        source = json.loads((root / 'data/validation/memory_v1' / (label['source'] + '.json')).read_text())
        sources[label['source']] = source
        text = source['text'][:manifest['max_characters_per_source']]
        step = manifest['chunk_characters'] - manifest['chunk_overlap']
        for start in range(0, len(text), step):
            candidates.append({'record_id': source['record_id'] + '-chunk-' + str(start),
                'source_record_id': source['record_id'], 'title': source['title'],
                'url': source['url'], 'origin_key': source['url'],
                'excerpt': text[start:start + manifest['chunk_characters']],
                'first_seen_at': source['retrieved_at'], 'published_at': source.get('published_at')})
    if len(candidates) > 150:
        raise ValueError('Frozen candidate pool exceeds the selector limit')
    # Date baseline is defined by receipt time; input file order is irrelevant.
    candidates.sort(key=lambda row: (row['first_seen_at'], row['record_id']), reverse=True)
    return manifest, labels, sources, candidates


def run_retrieval(root, manifest_path):
    manifest, labels, sources, candidates = load_frozen(root, manifest_path)
    cutoff = max(source['retrieved_at'] for source in sources.values())
    results = []
    for label in labels['groups']:
        source = sources[label['source']]
        for suffix, question in [('fact', label['question']), ('scope', label['unsafe_question']),
                                 ('before_capture', label['question'])]:
            case_cutoff = iso(timestamp(source['retrieved_at']) - timedelta(microseconds=1)) if suffix == 'before_capture' else cutoff
            for variant, options in manifest['variants'].items():
                start = perf_counter()
                result = select_evidence(candidates, question, budget=manifest['budget'],
                    limit=manifest['limit'], as_of=case_cutoff, **options)
                elapsed = perf_counter() - start
                rows = result['results']
                results.append({'case_id': label['source'] + '-' + suffix,
                    'event_group': label['event_group'], 'split': label['split'],
                    'question': question, 'answer_expected': suffix == 'fact',
                    'case_kind': suffix, 'cutoff': case_cutoff,
                    'expected_behavior': label['expected'] if suffix == 'fact' else label['abstention_reason'],
                    'variant': variant, 'selected_ids': [r['record_id'] for r in rows],
                    'required_source_recalled': any(r['source_record_id'] == source['record_id'] for r in rows),
                    'required_passage_recalled': any(label['passage'] in r['excerpt'] for r in rows),
                    'future_captures_selected': sum(timestamp(r['first_seen_at']) > timestamp(case_cutoff) for r in rows),
                    'target_capture_blocked': not any(r['source_record_id'] == source['record_id'] for r in rows) if suffix == 'before_capture' else None,
                    'cost_estimate': result['selection']['cost_estimate'],
                    'selection_latency_seconds': elapsed, 'selection': result['selection']})
    summaries = []
    for split in ('development', 'held_out'):
        for variant in manifest['variants']:
            rows = [r for r in results if r['split'] == split and r['variant'] == variant]
            facts = [r for r in rows if r['answer_expected']]
            scopes = [r for r in rows if r['case_kind'] == 'scope']
            probes = [r for r in rows if r['case_kind'] == 'before_capture']
            summaries.append({'split': split, 'variant': variant, 'questions': len(rows),
                'topic_groups': len({r['event_group'] for r in rows}),
                'required_passage_recall': sum(r['required_passage_recalled'] for r in facts) / len(facts) if facts else None,
                'source_id_recall': sum(r['required_source_recalled'] for r in facts) / len(facts) if facts else None,
                'negative_question_context_coverage': sum(r['required_passage_recalled'] for r in scopes) / len(scopes) if scopes else None,
                'future_capture_leakage': sum(r['future_captures_selected'] for r in rows),
                'temporal_probes': len(probes), 'target_capture_blocked': sum(r['target_capture_blocked'] for r in probes),
                'mean_estimated_context_cost': sum(r['cost_estimate'] for r in rows) / len(rows) if rows else None,
                'mean_selection_latency_seconds': sum(r['selection_latency_seconds'] for r in rows) / len(rows) if rows else None})
    return {'manifest_hash': digest(manifest_path), 'created_at': utcnow(),
            'source_documents': len(sources), 'topic_groups': len({r['event_group'] for r in results}),
            'unique_questions': len({r['case_id'] for r in results}), 'candidate_chunks': len(candidates),
            'model_calls': 0, 'provider_tokens': None, 'source_refreshes_during_run': 0,
            'answer_correctness': None, 'appropriate_abstention_rate': None,
            'summaries': summaries, 'cases': results, 'limitations': manifest['limitations']}
