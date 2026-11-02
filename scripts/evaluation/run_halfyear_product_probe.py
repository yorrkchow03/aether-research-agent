"""One bounded real Pro/Plan product report, isolated from the running service.

Reads the selected real library; never fills data, changes holdings, or replaces
the local business harness. Output and original failed attempts stay private.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import time

from research_agent.library_router import open_library
from research_agent.runtime import AgentRuntime
from research_data.http import utcnow

PROJECT=Path(__file__).resolve().parents[2]
PROMPT='''Create and SAVE a concise English ETH research note using the stored
six-month interval [2026-04-26T00:00:00Z, 2026-10-26T00:00:00Z), with BTC as a
comparison. This is retrospective research, not proof of system knowledge at
that date. First inspect the research skill and save a brief actionable plan.
Use the complete Bitstamp USD hourly series for each asset as independent venue
data, inspect coverage, and calculate endpoint close-to-close return using your
own terminal Python recipe and the saved library query collection IDs. Explain
the actual first/last close timestamps and number of return intervals; do not
call a close-to-close return a rolling six-month forecast. Do not print all rows.
Recall stored original ETH news with a generic public query using memory-news,
read one or two relevant original documents and distinguish announcement from
confirmed activation. Run one bounded live search for a related official ETH
update and open at most one original source; current search/acquisition times
must stay separate from the historical interval. A search failure is a gap.
Save a short report with four to six source-linked observations, one restrained
interpretation, no directional forecast, and explicit source/permission/coverage
limits. Give a short general conditional monitoring suggestion if evidence
supports it; personal budget, holdings and tolerance are unknown. Never infer
price causality, a profitable strategy, or a success rate from these sources.
Use publish_report with the existing numeric and source review; exact values and
units must reference registered computations. Reuse acquired inputs after any
review correction. This report tests the existing harness, memory and source
workflow, not model accuracy. Keep the final report compact.'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library',default='data/library-halfyear-20260426-20261026')
    p.add_argument('--output',type=Path,default=PROJECT/'local/halfyear/product')
    p.add_argument('--max-seconds',type=int,default=900)
    p.add_argument('--resume',action='store_true',help='Continue the same terminal attempt, preserving its original receipt')
    p.add_argument('--prompt-file',type=Path,help='Explicit continuation instructions; saved in the new attempt receipt')
    args=p.parse_args();output=args.output.resolve()
    original=None
    if args.resume:
        original=json.loads((output/'RUN.json').read_text())
        last=json.loads((output/'runtime/sessions'/original['session_id']/'jobs'/original['job_id']/'status.json').read_text())
        if last['state'] not in ('failed','cancelled','completed'):raise ValueError('The original attempt is still active')
        if args.library!=original['library']:raise ValueError('Resume must retain the selected historical library')
    elif output.exists():raise ValueError('Choose a fresh output; preserved attempts must not be overwritten')
    library=open_library(PROJECT,args.library)
    if (library.window_start,library.window_end)!=('2026-04-26T00:00:00Z','2026-10-26T00:00:00Z'):
        raise ValueError('Wrong research interval')
    for asset in ('eth','btc'):
        if library.series('market.bitstamp.'+asset+'.hourly',limit=1)['total']!=4392:
            raise ValueError('Complete independent Bitstamp history is required for this probe')
    output.mkdir(parents=True,exist_ok=args.resume)
    runtime=AgentRuntime(PROJECT,state_dir=output/'runtime')
    runtime.settings=replace(runtime.settings,library_path=args.library,max_model_calls=24)
    runtime.library=library;runtime.knowledge.library=library
    if runtime.settings.model_id!='doubao-seed-2.1-pro' or runtime.settings.model_mode!='ark_plan':
        raise ValueError('Probe requires the configured authorized Pro/Plan business model')
    session=runtime.snapshot(original['session_id'])['session'] if original else runtime.create_session(title='Half-year source and memory acceptance',research_mode='current')
    if original:
        last=runtime.snapshot(session['session_id'])['jobs'][-1]
        if last['state'] not in ('failed','cancelled','completed'):raise ValueError('A continuation is already active')
    prompt=args.prompt_file.read_text() if args.prompt_file else PROMPT
    job=runtime.start(session['session_id'],prompt,deliverable='report')
    prefix='CONTINUATION-'+job['job_id']+'-' if original else ''
    receipt={'started_at':utcnow(),'session_id':session['session_id'],'job_id':job['job_id'],
        'library':args.library,'software_fingerprint':runtime.software_fingerprint,
        'model':runtime.settings.model_id,'base_url':runtime.settings.base_url,
        'semantic_mode':runtime.settings.judgment_mode,'maximum_author_calls':24,'prompt':prompt,
        'context_trigger_tokens':runtime.settings.context_trigger_tokens,'continuation_of':original['job_id'] if original else None,
        'scope':'One isolated live product acceptance attempt; not a frozen accuracy experiment or first-pass benchmark.'}
    (output/(prefix+'RUN.json')).write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:receipt[k] for k in ('session_id','job_id','model','semantic_mode')}),flush=True)
    started=time.monotonic();last=None;stopping=False
    while True:
        snap=runtime.snapshot(session['session_id']);status=snap['jobs'][-1]
        current=(status['state'],status.get('last_event'))
        if current!=last:
            print(json.dumps({'state':current[0],'event':current[1]}),flush=True);last=current
        if status['state'] in ('completed','failed','cancelled'):
            receipt.update(ended_at=utcnow(),status=status,reports=snap['reports'],
                task_elapsed_seconds=round(time.monotonic()-started,3))
            (output/(prefix+'RESULT.json')).write_text(json.dumps(receipt,indent=2)+'\n')
            print(json.dumps({'state':status['state'],'reports':snap['reports']}),flush=True)
            return 0 if status['state']=='completed' and snap['reports'] else 1
        if not stopping and time.monotonic()-started>args.max_seconds:
            runtime.stop(session['session_id']);stopping=True
            (output/(prefix+'BOUND_REACHED.json')).write_text(json.dumps({'at':utcnow(),'reason':'Acceptance wall-clock bound reached; original sources/checkpoints retained'})+'\n')
        time.sleep(1)


if __name__=='__main__':raise SystemExit(main())
