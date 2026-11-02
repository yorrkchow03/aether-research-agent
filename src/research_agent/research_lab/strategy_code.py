"""Pure strategy source contract and bounded, credential-free local child evaluation.

Research restrictions for reviewed local code, not a hostile-code security sandbox.
The main analyst terminal is deliberately unaffected.
"""
from __future__ import annotations
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile

MAX_CODE_BYTES=16000
HELPERS={'len','sum','min','max','abs','float','int','mean','std','sqrt','log','range'}
NODES=(ast.Module,ast.FunctionDef,ast.arguments,ast.arg,ast.Return,ast.If,ast.Assign,ast.AugAssign,ast.For,
       ast.Expr,ast.Break,ast.Continue,ast.Pass,ast.List,ast.Tuple,ast.Dict,ast.Name,ast.Load,ast.Store,ast.Constant,
       ast.BinOp,ast.UnaryOp,ast.BoolOp,ast.Compare,ast.IfExp,ast.Call,ast.Attribute,ast.Subscript,ast.Slice,
       ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Mod,ast.Pow,ast.USub,ast.UAdd,ast.Not,ast.And,ast.Or,
       ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE,ast.In,ast.NotIn,ast.keyword)


def validate_source(source):
    if not isinstance(source,str) or not 0<len(source.encode())<=MAX_CODE_BYTES:raise ValueError('Strategy source must be 1..16000 bytes')
    tree=ast.parse(source)
    if len(tree.body)!=1 or not isinstance(tree.body[0],ast.FunctionDef):raise ValueError('Exactly one strategy function is required')
    fn=tree.body[0]
    if fn.name!='strategy' or [a.arg for a in fn.args.args]!=['bars','config'] or fn.args.posonlyargs or fn.args.kwonlyargs or fn.args.defaults or fn.args.vararg or fn.args.kwarg or fn.decorator_list or fn.returns:
        raise ValueError('Required signature: strategy(bars, config), with no decorators/defaults/annotations')
    if any(a.annotation for a in fn.args.args):raise ValueError('Annotations are not part of the pure-code contract')
    for node in ast.walk(tree):
        if not isinstance(node,NODES):raise ValueError('Disallowed syntax: '+type(node).__name__)
        if isinstance(node,ast.FunctionDef) and node is not fn:raise ValueError('Nested functions are not allowed')
        if isinstance(node,ast.Name) and (node.id.startswith('_') or node.id in {'eval','exec','open','getattr','setattr','globals','locals','type','object','compile','input','print','strategy'}):
            raise ValueError('Disallowed name: '+node.id)
        if isinstance(node,ast.arg) and node.arg.startswith('_'):raise ValueError('Private names are not allowed')
        if isinstance(node,ast.Attribute) and (node.attr!='get' or not isinstance(node.ctx,ast.Load)):
            raise ValueError('Only dictionary .get is allowed')
        if isinstance(node,ast.Call) and not ((isinstance(node.func,ast.Name) and node.func.id in HELPERS) or (isinstance(node.func,ast.Attribute) and node.func.attr=='get')):
            raise ValueError('Disallowed call '+ast.unparse(node.func)+'. Only numeric helpers and dictionary .get are allowed. No list.append: use local numeric accumulators with direct bars[i] indexing.')
        if isinstance(node,(ast.Assign,ast.AugAssign)):
            targets=node.targets if isinstance(node,ast.Assign) else [node.target]
            if any(not isinstance(t,ast.Name) or t.id in {'bars','config',*HELPERS} for t in targets):raise ValueError('Only local variable assignment is allowed')
        if isinstance(node,ast.For) and (not isinstance(node.target,ast.Name) or node.target.id in {'bars','config',*HELPERS}):raise ValueError('For loops need a local iterator')
        if isinstance(node,ast.Constant) and (isinstance(node.value,str) and len(node.value)>200 or isinstance(node.value,int) and abs(node.value)>1000000):
            raise ValueError('Oversized constant')
        if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Pow) and not (isinstance(node.right,ast.Constant) and isinstance(node.right.value,(int,float)) and -4<=node.right.value<=4):
            raise ValueError('Exponents must be fixed between -4 and4')
    return {'sha256':hashlib.sha256(source.encode()).hexdigest(),'contract':'pure-prefix-weight-v1','bytes':len(source.encode())}


def _range(*args):
    values=range(*args)
    if len(values)>504:raise ValueError('Numeric range exceeds504 iterations')
    return values


def _child(payload):
    import resource
    def bound(kind,value):
        _,hard=resource.getrlimit(kind)
        limit=min(value,hard) if hard!=resource.RLIM_INFINITY else value
        resource.setrlimit(kind,(limit,hard))
    bound(resource.RLIMIT_CPU,10)
    if sys.platform!='darwin':bound(resource.RLIMIT_AS,512*1024*1024)
    else:
        # macOS rejects DATA/AS limits in this runtime. Sample peak RSS instead;
        # this is an operational guard, not a hard allocation/security boundary.
        import threading
        import time
        def watch_memory():
            while True:
                if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss>512*1024*1024:os._exit(88)
                time.sleep(.025)
        threading.Thread(target=watch_memory,daemon=True).start()
    source=payload['source'];validate_source(source)
    namespace={'__builtins__':{},'len':len,'sum':sum,'min':min,'max':max,'abs':abs,'float':float,'int':int,
        'mean':statistics.mean,'std':statistics.stdev,'sqrt':math.sqrt,'log':math.log,'range':_range}
    exec(compile(source,'<reviewed-strategy>','exec'),namespace)
    function=namespace['strategy'];results=[]
    for case in payload['cases']:
        if len(case['series'])>3000 or len(case['indices'])>2500:raise ValueError('Bounded strategy input exceeded')
        values=[]
        for index in case['indices']:
            if not isinstance(index,int) or not 0<=index<len(case['series']):raise ValueError('Invalid decision index')
            # Function receives only completed prefix data, never an execution/future bar.
            bars=tuple(dict(r) for r in case['series'][max(0,index-251):index+1])
            value=function(bars,dict(payload['config']))
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=payload['config']['allocation_cap']:
                raise ValueError('Strategy must return a finite long/cash weight within allocation_cap')
            values.append(float(value))
        results.append(values)
    return results


def execute_source(source,cases,config,*,timeout=20):
    identity=validate_source(source)
    if not 1<=len(cases)<=24 or sum(len(c['indices']) for c in cases)>12000:raise ValueError('Too many strategy cases')
    data=json.dumps({'source':source,'cases':cases,'config':{'allocation_cap':config['allocation_cap']}},allow_nan=False)
    if len(data.encode())>16*1024*1024:raise ValueError('Strategy input exceeds16 MB')
    with tempfile.TemporaryDirectory(prefix='aether-pure-strategy-') as directory:
        try:
            proc=subprocess.run([sys.executable,'-I',str(Path(__file__).resolve())],input=data,text=True,
                capture_output=True,timeout=timeout,cwd=directory,env={'PATH':'/usr/bin:/bin','LANG':'en_US.UTF-8'})
        except subprocess.TimeoutExpired as exc:raise ValueError('Strategy exceeded its wall-clock budget') from exc
    if proc.returncode:raise ValueError('Strategy child failed: '+proc.stderr[-800:].strip())
    result=json.loads(proc.stdout)
    if len(result)!=len(cases) or any(len(a)!=len(c['indices']) for a,c in zip(result,cases)):raise ValueError('Strategy child output shape changed')
    return {'identity':identity,'weights':result,'execution':'Credential-free isolated Python child; pure prefix contract, CPU/wall-clock bounds and memory guard (sampled RSS on macOS); not a hostile-code sandbox'}


if __name__=='__main__':
    try:
        payload=json.loads(sys.stdin.read(16*1024*1024+1));print(json.dumps(_child(payload),allow_nan=False))
    except Exception as exc:
        print(type(exc).__name__+': '+str(exc)[:600],file=sys.stderr);sys.exit(1)
