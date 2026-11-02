"""One bounded, source-grounded business Pro report; not an accuracy benchmark."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import time
from research_agent.runtime import AgentRuntime
from research_data.http import utcnow

PROJECT=Path(__file__).resolve().parents[2]
PROMPT="Create and SAVE a short English informational ETH protocol research brief\nfrom our saved sources and research memory. The research question is whether a\npreviously saved Hoodi roadmap date should be treated as a firm current event.\nThis is a retrospective source audit, produced now; do not claim current market\nprices, a currently confirmed upgrade date, or what the system knew before\nacquisition. Read the research skill and save a short plan first.\nRecall earlier research about Hoodi and inspect its dated correction notices\nand replacement pointers. Read the relevant original source IDs, including the\nolder roadmap and the later official postponement when available. Distinguish\nthe originally planned Oct26 activation from the separate Oct27 roadmap entry:\na postponement of one date alone cannot prove cancellation of another. Explain\nthe source's testnet scope and the missing later confirmation. Use existing\nnews recall/source-card context, preserve disagreements and actual capture clocks.\nUse the terminal ResearchClient memory-state to inspect the current saved gaps\nand memory-note to save one useful open question; these are derived notes, not\nverified facts. Do not dump whole source arrays or collect fresh network data.\nKeep the report compact: 2or3 original-source observations, 1or2key_changes if\nsupported, at most1qualified interpretation and2questions_to_watch, clear gaps.\nFor qualitative status/dates/reasons omit metric,value,unit. No forecasts,\npurchase_decision, forced advice, simulation, full market collection or custom\nprice calculation is needed. Publish through publish_report with normal source\nreview. Reuse acquired inputs and correct only actual review issues. Complete\nwithin20author responses and2source-review requests. Do not claim a failed save\nsucceeded. This is one real product acceptance, not an accuracy or profit test."

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=PROJECT/'local/memory-upgrade/v2/product')
    p.add_argument('--max-seconds',type=int,default=900)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--prompt-file',type=Path)
    args=p.parse_args();out=args.output.resolve()
    if not out.is_relative_to(PROJECT/'local/memory-upgrade'):raise ValueError('Use the private report-quality directory')
    original=json.loads((out/'RUN.json').read_text()) if args.resume else None
    if not args.resume and out.exists():raise ValueError('Preserve prior attempts; choose a new output')
    if args.resume and list(out.glob('CONTINUATION-*-RUN.json')):raise ValueError('Only one targeted continuation is authorized')
    out.mkdir(parents=True,exist_ok=args.resume)
    runtime=AgentRuntime(PROJECT,state_dir=out/'runtime',review_request_limit=2)
    runtime.settings=replace(runtime.settings,max_model_calls=10 if args.resume else 20)
    if runtime.settings.model_id!='doubao-seed-2.1-pro' or runtime.settings.model_mode!='ark_plan':raise ValueError('Configured Pro/Plan business model required')
    if not runtime.library or runtime.library.window_end!='2026-10-26T00:00:00Z':raise ValueError('Use the selected real half-year library')
    if original:
        session=runtime.snapshot(original['session_id'])['session']
        if runtime.snapshot(session['session_id'])['jobs'][-1]['state'] not in ('completed','failed','cancelled'):raise ValueError('Attempt still active')
        if not args.prompt_file:raise ValueError('A targeted continuation needs explicit saved correction instructions')
    else:
        import sqlite3
        from research_agent.knowledge import KnowledgeStore
        prior=KnowledgeStore(PROJECT/'runtime/knowledge');prior.library=runtime.library
        for report_id in ('research-ce5f9eb63fc8','research-c8a7697b8261','research-3a4e2097085c','research-863b18606bce'):
            body=prior.report(report_id);runtime.knowledge.retain_report(report_id,'prior-source-context',body)
        source_feedback=PROJECT/'runtime/report-feedback/report-feedback.sqlite'
        target_feedback=runtime.state_dir/'report-feedback/report-feedback.sqlite'
        with sqlite3.connect(source_feedback.resolve().as_uri()+'?mode=ro',uri=True) as a,sqlite3.connect(target_feedback) as b:a.backup(b)
        from research_agent.experience_memory import ExperienceMemory
        old=ExperienceMemory(PROJECT/'runtime/experience',evidence_lookup=prior.get)
        for item in old.recall('Hoodi hourly close intervals qualitative report',limit=4,budget_chars=10000)['results']:
            runtime.experience.record_correction(item['topic'],item['mistake'],item['correction'],receipts=[r['path'] for r in item['receipts']],evidence_ids=item['evidence_ids'],applicability=item['applicability'],affected_outputs=item['affected_outputs'],kind=item['kind'])
        from research_agent.memory_service import index_source
        for source_id in ('page-48ec316d529d6c9da0a2329b','page-546a7ebd8e4cad2fd9923418'):
            if not runtime.knowledge.contains(source_id):runtime.knowledge.register(prior.get(source_id))
            index_source(runtime.memory_index,runtime.knowledge.get(source_id))
        session=runtime.create_session(title='Memory v2 · source updates and correction-aware research',research_mode='current')
    prompt=args.prompt_file.read_text() if args.prompt_file else PROMPT
    job=runtime.start(session['session_id'],prompt,deliverable='report')
    prefix='CONTINUATION-'+job['job_id']+'-' if original else ''
    receipt={'started_at':utcnow(),'session_id':session['session_id'],'job_id':job['job_id'],
        'model':runtime.settings.model_id,'base_url':runtime.settings.base_url,'software_fingerprint':runtime.software_fingerprint,
        'maximum_author_calls':runtime.settings.max_model_calls,'maximum_source_review_requests':2,'semantic_mode':runtime.settings.judgment_mode,
        'research_window':'source states and actual capture clocks; retrospective audit','prompt':prompt,
        'continuation_of':original['job_id'] if original else None,
        'scope':'Real local Deep Agents/Pro memory acceptance; original prior reports/notices imported into isolated state. No accuracy benchmark or human labels.'}
    (out/(prefix+'RUN.json')).write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:receipt[k] for k in ('session_id','job_id','model','maximum_author_calls')}),flush=True)
    started=time.monotonic();previous=None;stopped=False
    while True:
        snap=runtime.snapshot(session['session_id']);status=snap['jobs'][-1]
        now=(status['state'],status.get('last_event'))
        if now!=previous:print(json.dumps({'state':now[0],'event':now[1]}),flush=True);previous=now
        if status['state'] in ('completed','failed','cancelled'):
            receipt.update(ended_at=utcnow(),status=status,reports=snap['reports'],elapsed_seconds=round(time.monotonic()-started,3))
            (out/(prefix+'RESULT.json')).write_text(json.dumps(receipt,indent=2)+'\n')
            print(json.dumps({'state':status['state'],'reports':snap['reports']}),flush=True)
            return 0 if status['state']=='completed' and snap['reports'] else 1
        if not stopped and time.monotonic()-started>args.max_seconds:
            runtime.stop(session['session_id']);stopped=True
            (out/(prefix+'BOUND_REACHED.json')).write_text(json.dumps({'at':utcnow(),'reason':'Wall-clock bound; original work retained'})+'\n')
        time.sleep(1)

if __name__=='__main__':raise SystemExit(main())
