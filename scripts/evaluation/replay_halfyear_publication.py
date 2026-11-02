"""Replay one exact saved Pro-reviewed draft through the current publisher.

No model/source requests. Original live attempts and artifacts remain unchanged.
The new report records that review was replayed, not newly generated.
"""
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch

from research_agent.audit import BundleBuilder
from research_agent.evidence_judgments import EvidenceJudgmentService
from research_agent.knowledge import KnowledgeStore
from research_agent.library_router import open_library
from research_agent.reports import ReportDraft
from research_agent.review import check_review, evidence_text, review_targets
from research_agent.runtime import freeze_software
from research_agent.settings import Settings
from research_agent.tools import ResearchTools
from research_data.http import utcnow

ROOT=Path(__file__).resolve().parents[2]


class ExactSavedReview:
    def __init__(self, settings, saved):
        self.settings,self.saved=settings,saved
        contexts={rid for target in saved['targets'] for rid in target['evidence_ids'] if rid.startswith('context-')}
        if len(contexts)>1:raise ValueError('Ambiguous original research context')
        self.user_context_id=next(iter(contexts),None)

    def targets(self,draft):
        targets=review_targets(draft)
        if self.user_context_id:
            for item in targets:
                if item['kind'] in ('summary','judgment') and self.user_context_id not in item['evidence_ids']:
                    item['evidence_ids'].append(self.user_context_id)
        return targets

    def review(self, draft, lookup):
        targets=self.targets(draft)
        if targets!=self.saved['targets']:
            raise ValueError('Current review targets differ from the original live request')
        ids={rid for target in targets for rid in target['evidence_ids']}
        texts={rid:evidence_text(lookup(rid)) for rid in ids}
        if texts!=self.saved['evidence_text']:
            raise ValueError('Current review source text differs from the original live request')
        checked=check_review({'verdicts':self.saved['verdicts']},targets,texts)
        if checked['status']!='reviewed':raise ValueError('Saved review does not pass exact quote/source validation')
        return self.saved


def main():
    output=ROOT/'local/halfyear/product'
    target=output/'CURRENT_PUBLICATION.json'
    if target.exists():raise ValueError('Current publication replay already has a receipt; do not overwrite')
    original=json.loads((output/'RUN.json').read_text())
    state=output/'runtime';session_dir=state/'sessions'/original['session_id'];workspace=session_dir/'workspace'
    meta=json.loads((session_dir/'session.json').read_text())
    report=json.loads((workspace/'reports/research-ce5f9eb63fc8.json').read_text())
    original_review=json.loads((workspace/'reviews'/(report['review']['review_id']+'.json')).read_text())
    drafts=[json.loads(p.read_text())['draft'] for p in (workspace/'drafts').glob('*.json')]
    settings=replace(Settings.load(ROOT),library_path=original['library'])
    reviewer=ExactSavedReview(settings,original_review)
    matches=[ReportDraft.model_validate(d) for d in drafts if reviewer.targets(ReportDraft.model_validate(d))==original_review['targets']]
    if len(matches)!=1:raise ValueError('Expected one exact original pre-review draft')
    knowledge=KnowledgeStore(state/'knowledge');library=open_library(ROOT,settings.library_path);knowledge.library=library
    software,fingerprint=freeze_software(ROOT,output/'publication-replay')
    builder=BundleBuilder(ROOT,ROOT/'data/audit',(settings.api_key,*settings.private_secrets),software,state/'knowledge')
    judge=EvidenceJudgmentService(ROOT,state/'evidence-judgments',provider='jev',cache_only=True,
        timeout_seconds=12,max_attempts=1)
    events=[]
    service=ResearchTools(Path(meta['data_run']),workspace,meta['cutoff'],meta['mode'],
        event_sink=lambda kind,data:events.append({'kind':kind,**data}),allow_live=True,
        reviewer=reviewer,knowledge=knowledge,library=library,
        report_judgments=judge,judgment_mode='shadow',audit_builder=builder,
        run_metadata={'session_id':original['session_id'],'software_fingerprint':fingerprint,
            'publication_mode':'offline_replay_of_exact_saved_Pro_review',
            'original_live_report':'research-ce5f9eb63fc8','original_review_id':original_review['review_id'],
            'new_author_calls':0,'new_reviewer_calls':0})
    original_contexts={rid for target in original_review['targets'] for rid in target['evidence_ids'] if rid.startswith('context-')}
    if original_contexts:
        original_context=next(iter(original_contexts))
        service.context_record=knowledge.get(original_context)
        reviewer.user_context_id=original_context
    with patch('httpx.Client.request',side_effect=AssertionError('Network forbidden in saved-review replay')), \
         patch('httpx.Client.stream',side_effect=AssertionError('Network forbidden in saved-review replay')):
        result=service.publish(matches[0])
    obj=json.loads((workspace/result['structured_path'].lstrip('/')).read_text())
    semantic=obj['semantic_checks']['results']
    receipt={'passed':True,'at':utcnow(),'result':result,'software_fingerprint':fingerprint,
        'source_receipts_before':len(report['sources']),'source_receipts_after':len(obj['sources']),
        'original_review_targets_and_source_text_exact':True,'new_author_calls':0,'new_reviewer_calls':0,
        'semantic_cache_hits':sum(r.get('cache_hit') is True for r in semantic),
        'semantic_statuses':[r['status'] for r in semantic],'events':events,
        'scope':'Current source/number/publisher/bundle implementation on the unchanged live-reviewed draft. Exact saved Pro review reused only after target/text/quote equality; not a new live generation or accuracy experiment.'}
    target.write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:receipt[k] for k in ('passed','source_receipts_before','source_receipts_after','semantic_cache_hits','new_author_calls')}))


if __name__=='__main__':main()
