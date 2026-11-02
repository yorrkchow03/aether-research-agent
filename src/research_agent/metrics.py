"""Descriptive market indicators with saved inputs and reproducible recipes."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from research_data.http import dumps, utcnow
from research_data.normalize import timestamp

VERSION = 'indicators-v3'


def calculation(rows, kind, periods=30):
    """Pure calculations. Missing facts fail explicitly instead of becoming zero."""
    values = {}; units = {}; meta = {'kind':kind,'periods':periods,'version':VERSION}
    if kind in ('daily', 'relative'):
        from .simulation import price_rows, market_statistics
        primary = rows[0]['series']
        stats = market_statistics(primary, periods)
        if kind == 'daily':
            for key in ('return_fraction','max_drawdown_fraction','annualized_volatility_fraction'):
                if stats[key] is not None:values[key] = str(stats[key]);units[key] = 'fraction'
            meta.update(window_start=stats['start'],window_end=stats['end'],formula=stats['method'],
                        year_days=365,price_points=stats['price_points'])
            window = sorted(primary,key=lambda r:timestamp(r['observed_at']))[-periods:]
            volumes = [Decimal(r['volume_asset']) for r in window if r.get('volume_asset') is not None]
            if len(volumes) == periods:
                values['period_volume_asset'] = str(sum(volumes));units['period_volume_asset'] = rows[0]['asset']
                if periods >= 2 and sum(volumes[:-1]) > 0:
                    values['latest_volume_vs_prior_average_fraction'] = str(volumes[-1]/(sum(volumes[:-1])/(periods-1))-1)
                    units['latest_volume_vs_prior_average_fraction'] = 'fraction'
            else:meta['volume_gap'] = 'Not all completed daily rows contain volume; no volume statistic published'
        else:
            other = price_rows(rows[1]['series']);base = price_rows(primary)
            base = base[-(periods+1):];reference = {r['time']:r['close'] for r in other}
            if any(r['time'] not in reference for r in base):raise ValueError('Reference prices do not cover the same daily window')
            relative = [(r['close']/reference[r['time']]) for r in base]
            values['relative_return_fraction'] = str(relative[-1]/relative[0]-1)
            units['relative_return_fraction'] = 'fraction'
            meta.update(window_start=base[0]['close_time'],window_end=base[-1]['close_time'],reference_asset=rows[1]['asset'],
                        formula='Change in primary/reference daily-close price ratio over identical timestamps')
    elif kind == 'hourly_return':
        series=sorted(rows[0]['series'],key=lambda r:timestamp(r['observed_at']))[-(periods+1):]
        if len(series)!=periods+1 or any(timestamp(b['period_end'])-timestamp(a['period_end'])!=timedelta(hours=1) for a,b in zip(series,series[1:])):
            raise ValueError('Need consecutive completed hourly close points for the entire window')
        prices=[Decimal(r['close_usd']) for r in series]
        if any(not p.is_finite() or p<=0 for p in prices):raise ValueError('Hourly close prices must be positive and finite')
        values={'return_fraction':str(prices[-1]/prices[0]-1)};units={'return_fraction':'fraction'}
        meta.update(window_start=series[0]['period_end'],window_end=series[-1]['period_end'],interval_hours=1,formula='Last completed hourly close / first completed hourly close - 1')
    elif kind == 'oi_change':
        prior,current=rows
        if prior.get('dataset_id')!=current.get('dataset_id') or any(prior.get(k)!=current.get(k) for k in ('instrument','provider','quote_currency','schema_version')):
            raise ValueError('Open-interest snapshots must use the same source and instrument contract')
        before=Decimal(prior['values']['open_interest_usd']);after=Decimal(current['values']['open_interest_usd'])
        elapsed=(timestamp(current['observed_at'])-timestamp(prior['observed_at'])).total_seconds()
        if before<=0 or after<0 or elapsed<=0:raise ValueError('Invalid open-interest comparison')
        values={'oi_change_fraction':str(after/before-1),'oi_change_usd':str(after-before)};units={'oi_change_fraction':'fraction','oi_change_usd':'USD'}
        meta.update(window_start=prior['observed_at'],window_end=current['observed_at'],elapsed_seconds=elapsed,formula='Change between saved same-contract USD exposure snapshots',limits='Irregular saved interval, not a 24h change. USD exposure also changes with repricing; does not prove new positions or capital inflow.')
    elif kind == 'purchase':
        estimate=buy_cost(rows[0],periods)
        values={k:v for k,v in estimate.items() if k in ('budget_usd','covered_notional_usd','uncovered_notional_usd','asset_quantity','estimated_vwap_usd','slippage_from_best_ask_bps') and v is not None}
        units={k:'bps' if k.endswith('bps') else rows[0].get('asset','ETH') if k=='asset_quantity' else 'USD' for k in values}
        meta.pop('periods');meta.update(budget_usd=str(periods),window_start=rows[0]['observed_at'],window_end=rows[0]['observed_at'],fully_covered=estimate['fully_covered'],formula='Consume saved ask levels in price order; VWAP = covered USD / acquired quantity',limits=estimate['scope'])
    elif kind == 'execution':
        book = rows[0];data = json.loads(Path(book['raw_path']).read_text())
        bids = data.get('bids',[])[:25];asks = data.get('asks',[])[:25]
        if data.get('auction_mode'):raise ValueError('Auction indicative quotes are not executable estimates')
        if not bids or not asks:raise ValueError('Order book is missing bids or asks')
        bid=Decimal(bids[0][0]);ask=Decimal(asks[0][0]);mid=(bid+ask)/2
        if bid<=0 or ask<bid:raise ValueError('Order book is crossed or has invalid prices')
        values['spread_bps']=str((ask-bid)/mid*10000);units['spread_bps']='bps'
        for side,levels in (('bid',bids),('ask',asks)):
            for band in (10,50,100):
                depth=sum(Decimal(p)*Decimal(q) for p,q,*_ in levels if abs(Decimal(p)/mid-1)*10000<=band)
                key=f'{side}_depth_within_{band}bps_usd';values[key]=str(depth);units[key]='USD'
        meta.update(coverage='Only first 25 saved levels on each side; may underestimate full-band depth',
                    formula='Sum(price * asset quantity) within stated distance from the midprice',
                    limits='Snapshot only. Does not guarantee fills or account for latency, fees, or subsequent book changes')
    elif kind == 'funding':
        series=sorted(rows[0]['series'],key=lambda r:timestamp(r['observed_at']))
        if len(series)<3:raise ValueError('Not enough settled funding observations')
        intervals={(timestamp(b['observed_at'])-timestamp(a['observed_at'])).total_seconds() for a,b in zip(series,series[1:])}
        if len(intervals)!=1 or min(intervals)<=0:raise ValueError('Funding intervals vary; raw-rate percentiles would mix settlement periods')
        rates=[float(r['funding_rate_fraction']) for r in series]
        if any(not math.isfinite(v) for v in rates):raise ValueError('Funding contains missing/invalid rates')
        values={'latest_settled_rate_fraction':str(rates[-1]),'median_settled_rate_fraction':str(statistics.median(rates)),
                'empirical_percentile_fraction':str(sum(v<=rates[-1] for v in rates)/len(rates))}
        units={k:'fraction' for k in values}
        meta.update(observations=len(rates),interval_hours=next(iter(intervals))/3600,
                    window_start=series[0]['observed_at'],window_end=series[-1]['observed_at'],
                    formula='Empirical fraction of saved same-interval settled rates <= the latest rate',
                    limits='One venue and one instrument; not annualized, not a market-wide crowding or price forecast')
    else:raise ValueError('Unknown indicator calculation')
    if any(not Decimal(v).is_finite() for v in values.values()):raise ValueError('Indicator result is not finite')
    return {'values':values,'units':units,'meta':meta}


def buy_cost(book, budget_usd):
    """Consume a saved ask snapshot; report uncovered demand without invented fills."""
    budget=Decimal(str(budget_usd))
    if not budget.is_finite() or budget<=0:raise ValueError('Purchase amount must be a finite positive USD value')
    data=json.loads(Path(book['raw_path']).read_text())
    if data.get('auction_mode'):raise ValueError('Auction quotes cannot support a purchase estimate')
    levels=data.get('asks',[])[:25]
    if not levels:raise ValueError('No ask levels available')
    best=Decimal(levels[0][0]);remaining=budget;quantity=Decimal(0)
    for price,size,*_ in levels:
        p=Decimal(price);q=Decimal(size)
        if p<=0 or q<0:raise ValueError('Invalid order book level')
        take=min(q,remaining/p);quantity+=take;remaining-=take*p
        if remaining<=Decimal('1e-12'):break
    covered=budget-remaining
    return {'budget_usd':str(budget),'covered_notional_usd':str(covered),'uncovered_notional_usd':str(remaining),
            'asset_quantity':str(quantity),'estimated_vwap_usd':str(covered/quantity) if quantity else None,
            'slippage_from_best_ask_bps':str((covered/quantity/best-1)*10000) if quantity and best>0 else None,
            'fully_covered':remaining<=Decimal('1e-12'),'source_record_id':book['record_id'],
            'scope':'Saved top-25 ask levels, before fees; estimated immediate market buy, no guaranteed execution'}


def derive_dashboard(items, root,knowledge=None):
    """Persist derived records once per input/version, so every tile has provenance."""
    specs=[]
    for asset in ('ETH','BTC'):
        for days in (1,7,30):specs.append((f'{asset.lower()}_{days}d','daily',[f'{asset.lower()}_candles'],days))
    specs.extend([('eth_24h','hourly_return',['eth_hourly'],24),('eth_btc_7d','relative',['eth_candles','btc_candles'],7),
                  ('eth_btc_30d','relative',['eth_candles','btc_candles'],30),
                  ('execution','execution',['orderbook'],30),('funding_distribution','funding',['funding_history'],30)])
    current=items.get('open_interest',{}).get('record')
    if knowledge and current:
        previous=knowledge.previous_market_record(current)
        if previous:
            items={**items,'previous_open_interest':{'status':'ok','record':previous}}
            specs.append(('oi_change','oi_change',['previous_open_interest','open_interest'],0))
    result=derive_records(items,root,knowledge,specs)
    result.setdefault('oi_change',{'status':'unavailable','reason':'Not enough comparable saved history; 24h OI history is not claimed'})
    return result


def derive_records(items,root,knowledge,specs):
    root=Path(root);output={}
    for name,kind,keys,periods in specs:
        inputs=[items.get(k,{}).get('record') for k in keys]
        if any(r is None for r in inputs):
            output[name]={'status':'unavailable','reason':'Required source is unavailable','input_keys':keys};continue
        try:
            result=calculation(inputs,kind,periods);now=utcnow()
            identity=dumps({'version':VERSION,'kind':kind,'periods':periods,'inputs':[r['record_id'] for r in inputs],
                            'values':result['values']},sort_keys=True)
            rid='live-'+hashlib.sha256(identity.encode()).hexdigest()[:24]
            destination=root/'records'/(rid+'.json')
            if destination.exists():record=json.loads(destination.read_text())
            else:
                recipes=root/'recipes';recipes.mkdir(parents=True,exist_ok=True)
                recipe=recipes/(rid+'.py')
                body=('import json,sys\nfrom research_agent.metrics import calculation\n'
                      f'rows=[json.load(open(p)) for p in sys.argv[1:]]\nprint(json.dumps(calculation(rows,{kind!r},{periods})))\n')
                recipe.write_text(body)
                record={'record_id':rid,'dataset_id':'live.indicators.'+name,'asset':inputs[0].get('asset','ETH'),
                        'observed_at':max(r['observed_at'] for r in inputs),'retrieved_at':now,'available_at':now,'calculated_at':now,
                        'values':result['values'],'units':result['units'],'method':dumps(result['meta']),
                        'calculation_meta':result['meta'],'calculation_version':VERSION,
                        'input_record_ids':[r['record_id'] for r in inputs],'recipe_path':str(recipe),'recipe_sha256':hashlib.sha256(recipe.read_bytes()).hexdigest(),
                        'quality_flags':['derived','descriptive_not_forecast','saved_input_recipe'],
                        'scope':'Descriptive indicator on explicitly saved inputs and declared window; not an investment signal'}
                from .live import atomic
                atomic(destination,record)
            if knowledge:
                if not knowledge.contains(rid):knowledge.register(record)
                record=knowledge.get(rid)
            degraded=any(items[k].get('status')!='ok' for k in keys)
            output[name]={'status':'stale' if degraded else 'ok','record':record,'input_keys':keys,'retained_previous':degraded}
        except (ValueError,KeyError,TypeError,OSError,ArithmeticError) as exc:
            output[name]={'status':'unavailable','reason':str(exc)[:300],'input_keys':keys}
    return output
