"""Small offline report-quality regression ledger, not an accuracy benchmark.

Reads selected saved reports/evidence and previous review receipts only. Never
invokes a model, executes calculation recipes, or searches/fetches sources.
"""
from __future__ import annotations

import argparse
import copy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from research_agent.audit import report_evidence_ids
from research_agent.claims import Claim, validate_claims
from research_agent.report_event_memory import build_report_event_context
from research_agent.reports import ReportDraft, render_report, validate_report
from research_agent.review import ReviewResponse, check_review, review_targets
from research_data.library.time import timestamp


ROOT=Path(__file__).resolve().parents[2]
REPORTS=[
    'runtime/sessions/4264a2d26d74451cabb5f4edb510ff14/workspace/reports/research-ce5f9eb63fc8.json',
    'runtime/sessions/f8d087095dde473db7b114f3eab86251/workspace/reports/research-3a4e2097085c.json',
    'runtime/sessions/50ce137b7e6f420f9af5fed04824a6cc/workspace/reports/research-9bce0a4473a4.json',
]
PREFIX='local/report-quality/v1'
OFFICIAL='page-546a7ebd8e4cad2fd9923418'
ROADMAP='page-48ec316d529d6c9da0a2329b'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def must_reject(call):
    try:
        call()
    except (ValueError,KeyError):
        return True
    raise ValueError('An intentionally invalid synthetic case was accepted')


def model_labels(review, report_id):
    """Keep a model's own status as attribution; never upgrade it to truth."""
    return [{**copy.deepcopy(item),'label_type':'independent_model','human_ground_truth':False,
        'reviewer':review.get('reviewer'),'section':section}
        for section in ('checks','summaries') for item in review.get(section,[])
        if item.get('report')==report_id]


def independent_report_groups(artifacts):
    """Replays/renderings with identical original bytes share one source group."""
    return sorted({item['original_sha256'] for item in artifacts})


def candidate_navigation(context):
    candidate=next((c for c in context.get('newer_related_candidates',[]) if c.get('evidence_id')==OFFICIAL),None)
    require(candidate is not None,'Saved context did not surface the expected official source')
    require(candidate.get('event_equivalence_confirmed') is False,'Candidate was promoted into confirmed event identity')
    require(candidate.get('needs_original_review') is True,'Candidate lost its original-review qualification')
    return {'official_candidate_id':OFFICIAL,'candidate_surfaced':True,
        'event_equivalence_confirmed':False,'cancellation_confirmed':False,
        'semantic_report_repair_confirmed':False,
        'scope':'Navigation to a saved candidate only; no keyword-based semantic repair or cancellation verdict.'}


def verify_context_receipt(directory):
    directory=Path(directory); receipt=load_json(directory/'acceptance.json')
    for name,expected in receipt['artifacts'].items():
        path=(directory/name).resolve()
        require(path.is_relative_to(directory.resolve()),'Invalid context receipt path')
        require(digest(path)==expected,'Saved event-context artifact changed: '+name)
    require(receipt['network_requests']==receipt['model_calls']==receipt['writes_to_source_stores']==0,
        'Expected a read-only offline context receipt')
    require(receipt['observed_source_read_attempts']<=12,'Context source budget exceeded')
    context=load_json(directory/'actual-context.json')
    strict=load_json(directory/'actual-strict-context.json')
    require(strict.get('availability_mode')=='strict' and not strict.get('source_cards'),
        'Saved strict replay did not reject later-acquired selected sources')
    return {**candidate_navigation(context),'source_read_attempts':receipt['observed_source_read_attempts'],
        'strict_saved_replay_excludes_selected_sources':True,'acceptance_sha256':digest(directory/'acceptance.json'),
        'accepted_implementation_sha256':receipt.get('implementation_sha256')}


class SavedCase:
    def __init__(self, root, relative):
        self.path=(root/relative).resolve();self.saved=load_json(self.path)
        self.audit=load_json(self.path.with_suffix('.audit.json'))
        self.bundle=Path(self.audit['path']).resolve()
        require(self.bundle.is_relative_to(root.resolve()),'Archived bundle lies outside project')
        manifest_path=self.bundle/'manifest.json'
        require(digest(manifest_path)==self.audit['manifest_hash'],'Archived manifest reference changed')
        self.files={entry['path']:entry for entry in load_json(manifest_path)['files']}
        require(digest(self.path)==self.files['report/report.json']['sha256'],'Report differs from archived original')
        self.loaded={}

    def record(self,rid):
        if rid not in self.loaded:
            name='records/'+rid+'.json';entry=self.files[name];path=self.bundle/name
            require(digest(path)==entry['sha256'],'Selected archived evidence changed')
            row=load_json(path);require(row.get('record_id')==rid,'Selected record identity differs')
            self.loaded[rid]=row
        return self.loaded[rid]

    def artifact(self,root):
        return {'report_id':self.path.stem,'path':str(self.path.relative_to(root)),
            'original_sha256':digest(self.path),'independence_group':'saved-report-'+digest(self.path)[:24],
            'provenance':'real_saved_report','new_generation':False,
            'original_ai_review_version':self.saved.get('review',{}).get('review_version')}


def synthetic_checks():
    """Explicit contract counterexamples, never counted as real reports/news."""
    rows={'fixture-base':{'record_id':'fixture-base','values':{},'units':{}},
          'fixture-change':{'record_id':'fixture-change','values':{'return_fraction':'0.03248'},'units':{'return_fraction':'fraction'}}}
    base={'title':'SYNTHETIC informational brief','summary':'SYNTHETIC source-based summary.',
        'observations':[{'statement':'SYNTHETIC saved observation.','evidence_ids':['fixture-base']}],
        'limitations':['SYNTHETIC fixture; no actual investment information.']}
    change={'statement':'SYNTHETIC saved change.','evidence_ids':['fixture-change'],
        'metric':'return_fraction','value':'0.03248','unit':'fraction'}
    draft=ReportDraft.model_validate({**base,'key_changes':[change]})
    checked=validate_report(draft,rows.__getitem__)
    targets=review_targets(draft)
    require(next(t for t in targets if t['claim_id']=='K1')['kind']=='observation','Key change bypassed factual review')
    require(report_evidence_ids(draft.model_dump())==set(rows),'Key-change-only source missing from audit source selection')
    require(checked['numeric_claims_checked']==1,'Key-change value escaped numeric validation')
    for update in ({'value':'3.248'},{'unit':'percent'},{'evidence_ids':['fixture-base']}):
        bad=ReportDraft.model_validate({**base,'key_changes':[{**change,**update}]})
        must_reject(lambda:validate_report(bad,rows.__getitem__))
    neutral=ReportDraft.model_validate(base)
    require(neutral.judgment=='informational' and not neutral.forecasts and neutral.advice is None,
        'Neutral brief forces an investment judgment')
    require(not any(t['claim_id']=='J1' for t in review_targets(neutral)),'Neutral brief forces judgment review')
    require('## Research judgment' not in render_report(neutral,'2026-10-25T00:00:00Z',{},[]),'Neutral brief renders forced judgment')
    watched=ReportDraft.model_validate({**base,'questions_to_watch':['SYNTHETIC: has the announced milestone activated?']})
    action=next(t for t in review_targets(watched) if t['claim_id']=='M1')
    require(action['kind']=='research_action','Open watch question is treated as an occurred fact')
    verdict={'verdicts':[{'claim_id':'M1','status':'conditional','reason':'Synthetic future check.','quotes':[]}]}
    require(check_review(verdict,[action],{})['status']=='reviewed','Future research action cannot remain conditional')
    must_reject(lambda:check_review(verdict,[{**action,'kind':'observation'}],{}))
    late={'record_id':'fixture-late','published_at':'2026-10-22T00:00:00Z',
        'retrieved_at':'2026-10-27T00:00:00Z','url':'https://synthetic.invalid/update','text':'SYNTHETIC update.'}
    knowledge=SimpleNamespace(get=lambda _:copy.deepcopy(late),event_history=lambda *a,**k:[])
    strict=build_report_event_context(knowledge,['fixture-late'],as_of='2026-10-26T00:00:00Z',availability_mode='strict',related_queries=[])
    observed=build_report_event_context(knowledge,['fixture-late'],as_of='2026-10-26T00:00:00Z',related_queries=[])
    require(not strict['source_cards'],'Strict mode admitted later-acquired version')
    require(observed['source_cards'][0]['version_acquired_after_as_of'],'Observed reconstruction hides later acquisition')
    return [{'case_id':cid,'provenance':'synthetic','label_type':'programmatic_contract','status':'passed',
        'scope':scope} for cid,scope in [
        ('synthetic-key-change-review-audit','K1 targets, audit source selection, exact saved value/unit gates; not a complete archive build.'),
        ('synthetic-neutral-brief','Ordinary research may remain informational without advice or a directional judgment.'),
        ('synthetic-watch-action','Conditional follow-up uses research_action; a factual observation cannot pass as a quote-free conditional.'),
        ('synthetic-late-version','Strict cutoff rejects a later-acquired version; observed reconstruction flags its acquisition.')]]


def build_manifest(root):
    root=Path(root).resolve();cases=[SavedCase(root,p) for p in REPORTS]
    artifacts=[case.artifact(root) for case in cases]
    high_path=root/PREFIX/'reviewer-terra/REVIEW.json';xhigh_path=root/PREFIX/'reviewer-terra-xhigh/REVIEW.md'
    high=load_json(high_path)
    # Load the targeted second review as an attributed artifact; Markdown labels
    # are not parsed into machine truth or a quality pass.
    require(bool(xhigh_path.read_text().strip()),'Second independent review is missing')
    old,monthly,daily=cases
    newer=monthly.record(OFFICIAL);roadmap=old.record(ROADMAP)
    require(ROADMAP in report_evidence_ids(old.saved['report']),'Expected old roadmap citation absent')
    require(OFFICIAL not in report_evidence_ids(old.saved['report']),'Old example already cites candidate; case needs reinspection')
    acquired=newer.get('available_at') or newer.get('retrieved_at')
    require(acquired and timestamp(acquired)<=timestamp(old.saved['created_at']),'Official evidence not acquired before old report')
    require(timestamp(newer['published_at'])<=timestamp(acquired),'Official claimed publication is after its acquisition')
    contextual=verify_context_receipt(root/PREFIX/'event-memory')
    numeric=[]
    for index in (2,3):
        claim=Claim.model_validate(old.saved['report']['observations'][index])
        _,count=validate_claims([claim],old.record)
        numeric.append({'claim_id':'O'+str(index+1),'metric':claim.metric,'numeric_fields_checked':count})
    calc=old.record('live-0c45fc59f8b848c62e04d4ef')['values']
    for asset in ('eth','btc'):
        actual=Decimal(calc[asset+'_last_close_usd'])/Decimal(calc[asset+'_first_close_usd'])-1
        require(abs(actual-Decimal(calc[asset+'_close_to_close_return_fraction']))<Decimal('1e-12'),'Saved endpoint arithmetic differs')
    daily_row=daily.record('live-3ba46dc654323be25d5d30a7')
    daily_claim=Claim.model_validate(daily.saved['report']['observations'][1])
    validate_claims([daily_claim],daily.record)
    previous,last=daily_row['series'][-2:]
    returned=Decimal(last['close_usd'])/Decimal(previous['close_usd'])-1
    require(abs(returned-Decimal(daily_claim.value))<Decimal('1e-12'),'Saved daily endpoint arithmetic differs')
    compatible=[]
    for case in cases:
        draft=ReportDraft.model_validate(case.saved['report'])
        ReviewResponse.model_validate({'verdicts':case.saved['review']['verdicts']})
        rendered=render_report(draft,case.saved['cutoff'],case.saved['validation'],case.saved['sources'],
            case.saved.get('online_retrieved_through'),case.saved['review'])
        require(draft.title in rendered,'Old report cannot render with retained review metadata')
        compatible.append({'report_id':case.path.stem,'review_version':case.saved['review'].get('review_version'),
            'readable_and_renderable':True,'old_review_rejudged':False})
    real=[{'case_id':'real-hoodi-source-omission','provenance':'real_saved_report',
        'independence_group':artifacts[0]['independence_group'],
        'programmatic_checks':{'old_roadmap_cited':True,'official_candidate_not_in_citation_set':True,
            'official_claimed_publication':newer['published_at'],'official_acquired_at':acquired,
            'official_available_before_old_report':True,'old_roadmap_publication_field':roadmap.get('published_at'),
            'scope':'Citation membership and stored date ordering only; omission severity is an independent-model label.'},
        'independent_model_labels':model_labels(high,old.path.stem),
        'second_review_reference':{'path':str(xhigh_path.relative_to(root)),'label_type':'independent_model',
            'human_ground_truth':False,'scope':'Targeted review of three preselected findings; not an independent report sample.'},
        'enhanced_context':contextual},
        {'case_id':'real-saved-numeric-support','provenance':'real_saved_report',
            'independence_groups':[artifacts[0]['independence_group'],artifacts[2]['independence_group']],
            'structured_numeric_checks':numeric,'daily_return_recomputed_from_two_saved_closes':str(returned),
            'scope':'Saved field/unit support and endpoint arithmetic. Does not certify upstream prices, full-series integrity, ETF totals, causation or profitability.'},
        {'case_id':'real-old-review-compatibility','provenance':'real_saved_report','reports':compatible,
            'scope':'Retained old review schema/rendering only; no new semantic review.'},
        {'case_id':'real-attributed-interpretation-and-monitoring-findings','provenance':'real_saved_report',
            'independence_group':artifacts[1]['independence_group'],
            'independent_model_labels':[item for item in model_labels(high,monthly.path.stem) if item['claim_id'] in ('I1','V0')],
            'second_review_reference':{'path':str(xhigh_path.relative_to(root)),
                'label_type':'independent_model','human_ground_truth':False},
            'programmatic_semantic_verdict':None,
            'scope':'Attributed concerns about beta-language and an undefined future ETF monitoring window. Neither finding is promoted to human truth or automatically marked repaired.'}]
    return {'schema':'report-quality-case-manifest-v1','evaluation_type':'bounded offline regression and attributed review ledger',
        'report_artifacts':artifacts,'independent_saved_report_groups':independent_report_groups(artifacts),
        'new_independent_report_samples':0,'new_model_generations':0,'new_human_labels':0,
        'independent_reviews':[{'path':str(p.relative_to(root)),'sha256':digest(p),'label_type':'independent_model',
            'human_ground_truth':False} for p in (high_path,xhigh_path)],
        'historical_high_review_reported_scope':high.get('scope'),
        'real_cases':real,'synthetic_cases':synthetic_checks(),
        'selected_archived_records_read':sum(len(c.loaded) for c in cases),
        'comparison':{'old_saved_example':'Independent model reviews identify a missing newer official update.',
            'enhanced_context':'Saved bounded acceptance surfaces that official source as unverified candidate.',
            'new_real_pro_report':'pending','new_independent_post_upgrade_review':'pending',
            'semantic_fix_proven':False,'statistical_accuracy_improvement_claimed':False},
        'limits':['Development examples were selected after inspection; they are not a held-out benchmark.',
            'Multiple checks, targeted rereviews and offline replays do not add independent samples.',
            'No human truth labels; model judgments remain attributed and may be wrong.',
            'Candidate discovery and citation/numeric gates do not establish semantic correctness.']}


def default_external_status():
    return {'schema':'report-quality-external-acceptance-v1',
        'new_real_pro':{'status':'pending','report_path':None,'run_receipt_path':None,
            'scope':'New business-harness Doubao Pro report, not an offline replay.'},
        'post_upgrade_independent_model':{'status':'pending','review_path':None,'reviewer_model':None,
            'scope':'Independent model assessment of the new report; not human ground truth.'},
        'human_review':{'status':'not_performed','label_count':0},
        'note':'Parent may update these fields with inspectable artifact paths. This ledger does not infer semantic acceptance from keyword matches.'}


def run(root, output):
    root=Path(root).resolve();output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    manifest=build_manifest(root)
    (output/'case_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    status_path=output/'acceptance_status.json'
    if not status_path.exists():
        status_path.write_text(json.dumps(default_external_status(),ensure_ascii=False,indent=2)+'\n')
    external=load_json(status_path)
    receipt={'schema':'report-quality-offline-receipt-v1','status':'offline_checks_passed_external_acceptance_separate',
        'network_requests':0,'model_calls':0,'source_store_writes':0,'original_reports_modified':False,
        'real_saved_report_groups':len(manifest['independent_saved_report_groups']),
        'real_cases':len(manifest['real_cases']),'synthetic_contract_cases':len(manifest['synthetic_cases']),
        'new_independent_report_samples':0,'new_human_labels':0,
        'selected_archived_records_read':manifest['selected_archived_records_read'],
        'case_manifest_sha256':digest(output/'case_manifest.json'),
        'software_sha256':{p:digest(root/p) for p in ('scripts/evaluation/evaluate_report_quality.py',
            'src/research_agent/reports.py','src/research_agent/review.py',
            'src/research_agent/audit.py','src/research_agent/report_event_memory.py')},
        'external_acceptance_reported_status':external,
        'scope':'Programmatic regression only. No claim of semantic repair, predictive performance or statistical quality improvement.'}
    (output/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path,default=ROOT/PREFIX/'evaluation')
    args=parser.parse_args();receipt=run(args.root,args.output)
    print(json.dumps({k:receipt[k] for k in ('status','real_saved_report_groups','synthetic_contract_cases','model_calls','selected_archived_records_read')}))


if __name__=='__main__':
    main()
