"""Completed Coinbase OHLCV with original paged response lineage."""
from __future__ import annotations
import hashlib,json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from decimal import Decimal
from research_data.http import dumps,utcnow
from research_data.normalize import iso,timestamp
from research_agent.live import atomic

VERSION='ohlcv-history-v1'

def normalize_history(inputs,asset,start,end):
    candles={};begin=timestamp(start);stop=timestamp(end)
    for record in inputs:
        payload=json.loads(Path(record['raw_path']).read_bytes())
        if not isinstance(payload,list):raise ValueError('Expected Coinbase candle array')
        for values in payload:
            if not isinstance(values,list) or len(values)<6:raise ValueError('Malformed Coinbase OHLCV row')
            t=timestamp(values[0]);close_time=t+timedelta(days=1)
            if t<begin or close_time>stop:continue
            low,high,opening,close,volume=[Decimal(str(v)) for v in values[1:6]]
            if not all(v.is_finite() for v in (low,high,opening,close,volume)) or low<=0 or not low<=opening<=high or not low<=close<=high or volume<0:raise ValueError('Invalid OHLCV range or volume')
            row={'observed_at':iso(t),'period_end':iso(close_time),'low_usd':str(values[1]),'high_usd':str(values[2]),'open_usd':str(values[3]),'close_usd':str(values[4]),'volume_asset':str(values[5])}
            if t in candles and candles[t]!=row:raise ValueError('Paged OHLCV responses disagree on an overlapping candle')
            candles[t]=row
    ordered=[candles[t] for t in sorted(candles)]
    if len(ordered)<3:raise ValueError('Not enough completed price history')
    if any(timestamp(b['observed_at'])-timestamp(a['observed_at'])!=timedelta(days=1) for a,b in zip(ordered,ordered[1:])):raise ValueError('Candle history has missing/duplicate daily intervals')
    if timestamp(ordered[0]['observed_at'])!=begin or timestamp(ordered[-1]['period_end'])!=stop:raise ValueError('Candle history has missing requested boundary intervals')
    return {'series':ordered,'values':{'latest_close_usd':ordered[-1]['close_usd'],'completed_candles':str(len(ordered))},'units':{'latest_close_usd':'USD','completed_candles':'count'},'metadata':{'asset':asset,'start':ordered[0]['observed_at'],'end':ordered[-1]['period_end'],'version':VERSION}}


def fetch_history(live,knowledge,asset='ETH',days=730):
    if asset not in ('ETH','BTC') or not 240<=days<=1095:raise ValueError('Use ETH/BTC and 240–1095 daily periods')
    end=datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0);start=end-timedelta(days=days);cursor=start;inputs=[]
    while cursor<end:
        boundary=min(end,cursor+timedelta(days=250))
        response=live.request('coinbase',f'/products/{asset}-USD/candles',{'granularity':86400,'start':iso(cursor),'end':iso(boundary)},ttl=3600)
        row=response['record']
        if not knowledge.contains(row['record_id']):knowledge.register(row)
        inputs.append(knowledge.get(row['record_id']));cursor=boundary
    normalized=normalize_history(inputs,asset,iso(start),iso(end));now=utcnow()
    identity=dumps({'version':VERSION,'asset':asset,'start':iso(start),'end':iso(end),'inputs':[r['record_id'] for r in inputs]},sort_keys=True)
    rid='live-'+hashlib.sha256(identity.encode()).hexdigest()[:24]
    if knowledge.contains(rid):return knowledge.get(rid)
    recipe=live.root/'recipes'/(rid+'.py');recipe.parent.mkdir(parents=True,exist_ok=True)
    recipe.write_text('import json,sys\nfrom research_agent.investment.history import normalize_history\n'+f'r=normalize_history([json.load(open(p)) for p in sys.argv[1:]],{asset!r},{iso(start)!r},{iso(end)!r})\nprint(json.dumps(r))\n')
    record={'record_id':rid,'dataset_id':'live.strategy_history.'+asset.lower(),'asset':asset,'observed_at':normalized['series'][-1]['period_end'],'retrieved_at':now,'available_at':now,'series':normalized['series'],'values':normalized['values'],'units':normalized['units'],'input_record_ids':[r['record_id'] for r in inputs],'recipe_path':str(recipe),'recipe_sha256':hashlib.sha256(recipe.read_bytes()).hexdigest(),'method':'Union original Coinbase daily OHLCV pages; reject conflicting overlaps and gaps; completed UTC intervals only','calculation_version':VERSION,'quality_flags':['derived','retrospective_current_retrieval','single_exchange','not_strict_point_in_time'],'scope':'Completed Coinbase USD candles captured now; historical revisions/public availability are not authenticated'}
    atomic(live.root/'records'/(rid+'.json'),record);knowledge.register(record);return knowledge.get(rid)
