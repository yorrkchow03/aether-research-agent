"""Audit a saved monthly product report against v4 source membership/lineage."""
import argparse
import json
from pathlib import Path

from research_agent.advice import advice_evidence_ids
from research_agent.knowledge import KnowledgeStore
from research_agent.reports import ReportDraft, validate_report
from research_data.library.store import ResearchLibrary
from research_data.library.time import timestamp
from research_agent.live import atomic

ROOT = Path(__file__).resolve().parents[2]


def audit(receipt_path):
    receipt_path = Path(receipt_path).resolve()
    receipt = json.loads(receipt_path.read_text())
    if receipt.get('state') != 'completed' or not receipt.get('reports'):
        raise ValueError('Inspect the real completed publication first')
    workspace = ROOT / 'runtime/sessions' / receipt['session_id'] / 'workspace'
    report_path = workspace / receipt['reports'][-1].lstrip('/')
    bundle = json.loads(report_path.with_suffix('.json').read_text())
    draft = ReportDraft.model_validate(bundle['report'])
    library = ResearchLibrary(ROOT / 'data/datasets/aether-month-20260926-20261026-v4', create=False)
    knowledge = KnowledgeStore(ROOT / 'runtime/knowledge')
    records = {}
    def lookup(rid):
        if rid not in records:
            records[rid] = knowledge.get(rid) if knowledge.contains(rid) else library.evidence(rid)
        return records[rid]
    check = validate_report(draft, lookup)
    roots = {rid for c in [*draft.observations, *draft.interpretations] for rid in c.evidence_ids}
    roots.update(advice_evidence_ids(draft.advice))
    roots.update(rid for f in draft.forecasts for rid in [*f.supporting_evidence_ids, *f.counter_evidence_ids])
    if draft.purchase_decision:
        roots.update(rid for c in [*draft.purchase_decision.conditions_to_consider, *draft.purchase_decision.reasons_to_wait] for rid in c.evidence_ids)
        roots.update(draft.purchase_decision.simulation_evidence_ids)
    leaves, calculations, context, visited = set(), set(), set(), set()
    def visit(rid):
        if rid in visited:
            return
        if len(visited) >= 5000:
            raise ValueError('Unexpectedly unbounded report lineage')
        visited.add(rid); row = lookup(rid)
        if row.get('dataset_id') == 'local.user_research_parameters':
            context.add(rid); return
        children = row.get('input_record_ids', [])
        if children:
            calculations.add(rid)
            for child in children:
                visit(child)
        else:
            leaves.add(rid)
    for rid in roots:
        visit(rid)
    outside = sorted(rid for rid in leaves if not library.contains(rid))
    cutoff = timestamp('2026-10-26T00:00:00Z')
    def beyond_cutoff(row):
        if row.get('published_at'):
            return timestamp(row['published_at']) >= cutoff
        return bool((row.get('observed_at') and timestamp(row['observed_at']) >= cutoff) or
                    (row.get('period_end') and timestamp(row['period_end']) > cutoff))
    after_cutoff = sorted(rid for rid in leaves if beyond_cutoff(lookup(rid)))
    # Resolve every frozen leaf independently to verify raw captures, not just IDs.
    for rid in leaves - set(outside):
        frozen = library.evidence(rid); row = lookup(rid)
        if row.get('values') != frozen.get('values') or row.get('source', {}).get('sha256') != frozen.get('source', {}).get('sha256'):
            raise ValueError('Saved source differs from the frozen record: ' + rid)
    result = {'saved_report': str(report_path.relative_to(ROOT)), 'published': True,
        'structured_contract': check, 'product_ai_review_status': bundle.get('review', {}).get('status'),
        'root_evidence_count': len(roots), 'lineage_records_checked': len(visited),
        'calculation_records': sorted(calculations), 'context_records': sorted(context),
        'frozen_leaf_records': len(leaves)-len(outside), 'outside_frozen_archive_leaf_ids': outside,
        'all_market_source_leaves_in_v4': not outside,
        'source_leaves_after_research_cutoff': after_cutoff,
        'all_source_effective_times_within_cutoff': not after_cutoff,
        'semantic_correctness': None, 'human_adjudication': 'pending',
        'scope': 'Actual published report, product contract and independent frozen-source membership/raw-hash lineage. Formula suitability, prose accuracy, causation and advice are not certified by these checks.'}
    package = receipt_path.parent / 'product-report'
    (package / 'reports').mkdir(parents=True, exist_ok=True)
    (package / 'records').mkdir(exist_ok=True)
    (package / 'reports/monthly-report.md').write_text(report_path.read_text())
    for rid in visited:
        atomic(package / 'records' / (rid + '.json'), lookup(rid))
    result['review_copy'] = str((package / 'reports/monthly-report.md').relative_to(ROOT))
    destination = receipt_path.parent / 'PRODUCT_SOURCE_AUDIT.json'
    atomic(destination, result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', type=Path, default=ROOT / 'local/report-baseline/monthly-v1/product-final-revision.json')
    print(json.dumps(audit(parser.parse_args().receipt), indent=2))
