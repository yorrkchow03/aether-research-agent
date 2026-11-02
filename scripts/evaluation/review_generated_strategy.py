#!/usr/bin/env python3
"""Developer-requested constant-price invariant for a declared price-mean variable.

This is a narrow deterministic diagnostic, not a generic semantic verifier. Run
only on locally reviewed source passing the pure-code gate. Original strategy,
reviews, selection and confirmation are never rewritten or tuned by this check.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import math
import sys
from research_agent.research_lab.strategy_code import validate_source, _range
from research_agent.live import atomic
from research_data.http import utcnow

def constant_mean_check(source,variable,window):
    validate_source(source)
    if not variable.isidentifier() or variable.startswith('_') or not 1<=window<=252:raise ValueError('Explicit local variable/window required')
    scope={'__builtins__':{},'len':len,'sum':sum,'min':min,'max':max,'abs':abs,'float':float,'int':int,'mean':statistics.mean,'std':statistics.stdev,'sqrt':math.sqrt,'log':math.log,'range':_range}
    exec(compile(source,'<strategy-invariant>','exec'),scope);seen={}
    def trace(frame,event,arg):
        if frame.f_code.co_filename=='<strategy-invariant>' and event=='return':seen.update(frame.f_locals)
        return trace
    bars=tuple({'close_usd':'100','open_usd':'100','high_usd':'100','low_usd':'100','volume_asset':'100'} for _ in range(252))
    previous=sys.gettrace();sys.settrace(trace)
    try:scope['strategy'](bars,{'allocation_cap':.5})
    finally:sys.settrace(previous)
    actual=seen.get(variable)
    return {'check':'Declared price SMA must equal100 on252 constant100-price bars','variable':variable,'declared_window':window,
        'expected':100.,'actual':actual,'passed':isinstance(actual,(int,float)) and math.isclose(actual,100.,rel_tol=1e-10),
        'scope':'One explicit invariant for the stated mean; passing does not verify all formulas, timing, or strategy quality.'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--variable',required=True);p.add_argument('--window',type=int,required=True);a=p.parse_args()
    r=json.loads((a.run/'artifacts/result.json').read_text());check=constant_mean_check(r['source'],a.variable,a.window)
    output=a.run/'developer_review.json'
    if output.exists():raise ValueError('Retain earlier reviews; do not overwrite')
    review={'status':'blocked for semantic correction' if not check['passed'] else 'limited invariant passed; further review required','reviewed_at':utcnow(),
        'source_sha256':hashlib.sha256(r['source'].encode()).hexdigest(),'diagnostic':check,'orders_authorized':False,
        'findings':[f'The declared {a.window}-bar price mean {a.variable} equals {check["actual"]} on constant100 prices; expected100. The computed value does not satisfy the declared price-mean invariant.' if not check['passed'] else 'One explicit constant-price mean invariant passed. Independent semantic review remains required.',
            'Model reviewer approval is research-test eligibility, not a mathematical correctness guarantee. Saved performance describes the actual code, not the intended formula. No corrected strategy or new confirmation study is inferred.']}
    atomic(output,review);print(json.dumps(review,indent=2))
