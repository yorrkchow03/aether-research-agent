"""One bounded, source-grounded business Pro report; not an accuracy benchmark."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import time
from research_agent.runtime import AgentRuntime
from research_data.http import utcnow

PROJECT=Path(__file__).resolve().parents[2]
PROMPT='''Prepare and SAVE a concise English investor research brief for ETH for
[2026-10-19T00:00:00Z,2026-10-26T00:00:00Z), using BTC as market context.
Use our stored real library, not a new data collection. This is a retrospective
brief produced now; distinguish the research interval, publisher dates and actual
source captures. First read the research skill and save a short actionable plan.
Choose a few relevant data points; use complete Bitstamp USD hourly closes for
ETH and BTC over that week, compute endpoint close-to-close returns with your own
small Python recipe via the terminal and register the computation with declared
input collection IDs. Explain actual close endpoints/number of intervals. Read
original ETH protocol news, especially the Hoodi upgrade state. Use saved news
recall and bounded related entity lookup, inspect newer primary-source updates,
and distinguish an older roadmap, a later postponement and any unconfirmed date.
Treat related retrieval matches as candidates until the original is read.
Write for an ordinary investor: about three main observations, one or two sourced
key_changes, one restrained interpretation and up to two questions_to_watch.
Lead with what changed and why a reader might care, with scope/uncertainty. Keep
technical/raw numeric fields in citations/structured fields and use readable
rounded percentages in prose. Do not assert price causality or factor dominance.
Default judgment=informational. No directional forecast, purchase_decision or
forced investment advice; user holdings, risk tolerance and capital are unknown.
Follow-up questions must be genuine proposed checks with no invented thresholds.
Do not treat a fixed historical aggregate as able to change later. Publish using
publish_report and normal numeric/source review. Correct minimal unsupported
claims and reuse acquired inputs. Maximum18author responses and2review requests;
keep it compact and finish the saved publication rather than reopening research.
This is one product acceptance attempt, not proof of model accuracy or profits.'''

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=PROJECT/'local/report-quality/v1/product')
    p.add_argument('--max-seconds',type=int,default=900)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--prompt-file',type=Path)
    args=p.parse_args();out=args.output.resolve()
    if not out.is_relative_to(PROJECT/'local/report-quality'):raise ValueError('Use the private report-quality directory')
    original=json.loads((out/'RUN.json').read_text()) if args.resume else None
    if not args.resume and out.exists():raise ValueError('Preserve prior attempts; choose a new output')
    if args.resume and list(out.glob('CONTINUATION-*-RUN.json')):raise ValueError('Only one targeted continuation is authorized')
    out.mkdir(parents=True,exist_ok=args.resume)
    runtime=AgentRuntime(PROJECT,state_dir=out/'runtime',review_request_limit=2)
    runtime.settings=replace(runtime.settings,max_model_calls=8 if args.resume else 18)
    if runtime.settings.model_id!='doubao-seed-2.1-pro' or runtime.settings.model_mode!='ark_plan':raise ValueError('Configured Pro/Plan business model required')
    if not runtime.library or runtime.library.window_end!='2026-10-26T00:00:00Z':raise ValueError('Use the selected real half-year library')
    if original:
        session=runtime.snapshot(original['session_id'])['session']
        if runtime.snapshot(session['session_id'])['jobs'][-1]['state'] not in ('completed','failed','cancelled'):raise ValueError('Attempt still active')
        if not args.prompt_file:raise ValueError('A targeted continuation needs explicit saved correction instructions')
    else:session=runtime.create_session(title='ETH weekly investor brief · report quality acceptance',research_mode='current')
    prompt=args.prompt_file.read_text() if args.prompt_file else PROMPT
    job=runtime.start(session['session_id'],prompt,deliverable='report')
    prefix='CONTINUATION-'+job['job_id']+'-' if original else ''
    receipt={'started_at':utcnow(),'session_id':session['session_id'],'job_id':job['job_id'],
        'model':runtime.settings.model_id,'base_url':runtime.settings.base_url,'software_fingerprint':runtime.software_fingerprint,
        'maximum_author_calls':runtime.settings.max_model_calls,'maximum_source_review_requests':2,'semantic_mode':runtime.settings.judgment_mode,
        'research_window':['2026-10-19T00:00:00Z','2026-10-26T00:00:00Z'],'prompt':prompt,
        'continuation_of':original['job_id'] if original else None,
        'scope':'Real local Deep Agents/Pro product acceptance. No accuracy benchmark, no human truth labels.'}
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
