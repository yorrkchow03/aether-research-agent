"""Bounded real Pro report, then genuine cross-session conditional-case recall.

Requires the existing source library. Uses an isolated local Deep Agents runtime,
two saved publisher originals, the authorized Pro Plan and mandatory audit.
This is product acceptance, not an investment or model-accuracy benchmark.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import time

from research_agent.library_router import open_library
from research_agent.report_study_runtime import ReportStudyRuntime, StudyPolicy, HttpMeter
from research_agent.settings import Settings
from research_data.http import utcnow


DEFAULT_SOURCES=('page-9794078ccdabb208adf9ff50','page-546a7ebd8e4cad2fd9923418')


class ConditionalAcceptanceRuntime(ReportStudyRuntime):
    def build_research_tools(self,*args,**kwargs):
        service=super().build_research_tools(*args,**kwargs)
        service.SAFE_SDK=set(service.SAFE_SDK)|{'memory-cases'}
        return service


def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def run_task(runtime,output,prompt,deliverable):
    runtime.http_meter=HttpMeter(output/(deliverable+'-http.json'),
        timeout=runtime.study_policy.http_timeout,max_retries=0,
        max_requests=runtime.study_policy.author_limit+runtime.study_policy.review_limit)
    session=runtime.create_session(title='Conditional research '+deliverable,research_mode='current',
        cutoff=json.loads((output/'data_run/manifest.json').read_text())['cutoff'],run_dir=output/'data_run')
    job=runtime.start(session['session_id'],prompt,deliverable=deliverable)
    save(output/(deliverable+'-request.json'),{'session':session,'job':job,'prompt':prompt})
    start=time.monotonic();previous=None;stopped=False
    while True:
        snapshot=runtime.snapshot(session['session_id']);status=snapshot['jobs'][-1]
        state=(status['state'],status.get('last_event'))
        if state!=previous:print(json.dumps({'task':deliverable,'state':state[0],'event':state[1]}),flush=True);previous=state
        if status['state'] in ('completed','failed','cancelled'):
            result={'session_id':session['session_id'],'job_id':job['job_id'],'status':status,
                'reports':snapshot['reports'],'elapsed_seconds':time.monotonic()-start}
            save(output/(deliverable+'-result.json'),result)
            return result
        if time.monotonic()-start>600 and not stopped:
            runtime.stop(session['session_id']);stopped=True
        time.sleep(1)


def main(argv=None):
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source-id',action='append')
    args=parser.parse_args(argv);source_ids=args.source_id or DEFAULT_SOURCES
    settings=Settings.load(root)
    library=open_library(root,settings.library_path)
    rows=[library.evidence(rid) for rid in source_ids]
    args.output.mkdir(parents=True,exist_ok=False)
    policy=StudyPolicy(version='condition-acceptance-v1',author_limit=16,review_limit=3,
        wall_limit=600,finish_after_publish=True,duplicate_support_checks=False,
        memory_budget=6000,http_timeout=90,http_max_retries=0)
    runtime=ConditionalAcceptanceRuntime(root,args.output/'runtime',arm='original_off',source_ids=source_ids,policy=policy)
    for row in rows:runtime.knowledge.register(row)
    run=args.output/'data_run';run.mkdir()
    save(run/'manifest.json',{'cutoff':utcnow(),'datasets':[],'sources':[],
        'scope':'Two actual saved originals; no new data collection.'})
    save(args.output/'PROTOCOL.json',{'started_at':utcnow(),'source_ids':source_ids,
        'software_fingerprint':runtime.software_fingerprint,'policy':policy.public(),
        'model':runtime.settings.model_id,'endpoint':runtime.settings.base_url,
        'scope':'Two sequential genuine harness tasks. No Codex-authored business report or invented historical outcome.'})
    prompt=('Read the research skill and make a short plan, then write and SAVE a concise English ETH research report. '
        'Read the two actual saved originals '+json.dumps(source_ids)+' using read_evidence. '
        'Use only these captured sources; describe this as retrospective research produced now, not current market coverage or historical system knowledge. '
        'Explain how the later Hoodi postponement changes the earlier activation schedule and what remains unknown. '
        'Include two concise observations and exactly one optional explanations entry, asset=ETH: observed_basis, a qualified hypothesis, horizon, '
        'applicability, invalidation and uncertainties. Where supported record context event_status=delayed and network=Hoodi testnet, '
        'but preserve source scope and do not assert price effects or that a mainnet upgrade happened. '
        'Distinguish the revised client/operator guidance for removing the old activation from any instructions for a future replacement activation; inspect the actual source before declaring either absent. '
        'An alternative is optional; do not invent one. Unknown external price effects remain unknown. '
        'Use real source IDs for all nested claims; use plain qualitative claims without metric/value/unit. '
        'User personal context is unavailable and need not be put into this explanation. '
        'Publish via publish_report with mandatory source review, address concrete audit feedback, and finish after one verified publication. '
        'Do not call web search, data capture, strategy/portfolio operations or optional semantic checks.')
    report=run_task(runtime,args.output,prompt,'report')
    cases=runtime.case_memory.recall('Hoodi',budget_chars=24000)
    save(args.output/'retained-cases.json',cases)
    if report['status']['state']!='completed' or not report['reports'] or not cases['results']:
        save(args.output/'ACCEPTANCE.json',{'passed':False,'reason':'Report or host case retention not completed','report':report})
        return 1
    followup=('Read the research skill and use the real terminal ResearchClient to query memory-cases for Hoodi '
        'with context asset=ETH, conditions event_status=delayed and network=Hoodi testnet, budget_chars=24000. '
        'Then reopen the original source IDs returned by the saved case with read_evidence. '
        'Submit a brief source-checked English answer explaining whether the old activation date is still a valid checkpoint '
        'and what would make the earlier explanation need review. Treat the retrieved case only as reasoning context; the submitted factual/interpretive claims must be about what the two originals establish, not certify the history or review status of the case. Explain that a market-price effect is unknown. '
        'Do not cite case IDs as factual evidence, invent outcomes, use other sources or generate a new full report.')
    answer=run_task(runtime,args.output,followup,'answer')
    session_dir=runtime.sessions_dir/answer['session_id']
    event_file=session_dir/'jobs'/answer['job_id']/'events.jsonl'
    events=[json.loads(line) for line in event_file.read_text().splitlines() if line.strip()]
    recall=[event for event in events if event['kind']=='research_case_recall' and event.get('case_ids')]
    answers=list((session_dir/'workspace/answers').glob('*.json'))
    passed=answer['status']['state']=='completed' and bool(recall) and bool(answers)
    save(args.output/'ACCEPTANCE.json',{'passed':passed,'ended_at':utcnow(),
        'report':report,'answer':answer,'retained_case_ids':[c['case_id'] for c in cases['results']],
        'actual_model_case_recalls':recall,'source_ids':source_ids,
        'scope':'Actual Pro/local harness publication and cross-session source reopening. Semantic review is fallible AI, not human truth.'})
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
