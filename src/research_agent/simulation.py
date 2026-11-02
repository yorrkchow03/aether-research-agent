"""Deterministic long/cash paper trading. Signals see only prior completed rows."""
from __future__ import annotations
import math
import statistics
from datetime import timedelta
from pydantic import BaseModel, Field, ConfigDict
from research_data.normalize import timestamp


class DecisionProfile(BaseModel):
    model_config=ConfigDict(extra='forbid')
    horizon_days:int=Field(default=30,ge=1,le=365)
    budget_usd:float|None=Field(default=None,gt=0,le=1e9)
    existing_crypto_usd:float|None=Field(default=None,ge=0,le=1e12)
    portfolio_total_usd:float|None=Field(default=None,gt=0,le=1e12)
    max_loss_usd:float|None=Field(default=None,gt=0,le=1e9)
    allocation_fraction:float=Field(default=1,gt=0,le=1)
    fee_bps:float=Field(default=10,ge=0,le=500)
    slippage_bps:float=Field(default=10,ge=0,le=500)
    preference:str=Field(default='',max_length=2000)


def price_rows(series):
    if not isinstance(series,list) or len(series)<3:raise ValueError('至少需要3个日频价格点')
    rows=[]
    for r in series:
        close=float(r.get('close_usd',r.get('price_usd',r.get('values',{}).get('price_usd',0))))
        op=float(r.get('open_usd',close));time=r['observed_at']
        if not all(math.isfinite(v) and v>0 for v in (op,close)):raise ValueError('价格必须是有限正数')
        rows.append({'time':time,'close_time':r.get('period_end') or time,'open':op,'close':close})
    rows.sort(key=lambda r:timestamp(r['time']))
    if any(timestamp(b['time'])-timestamp(a['time'])!=timedelta(days=1) for a,b in zip(rows,rows[1:])):
        raise ValueError('日频价格窗口缺日/重复，不能把不连续点当连续交易日')
    return rows


def market_statistics(series,periods=30):
    rows=price_rows(series)
    if len(rows)<periods+1:raise ValueError(f'{periods}个周期需要{periods+1}个连续价格点')
    rows=rows[-(periods+1):];prices=[r['close'] for r in rows]
    logs=[math.log(b/a) for a,b in zip(prices,prices[1:])]
    peak=prices[0];mdd=0
    for p in prices:peak=max(peak,p);mdd=min(mdd,p/peak-1)
    return {'periods':periods,'price_points':len(rows),'start':rows[0]['close_time'],'end':rows[-1]['close_time'],
            'return_fraction':prices[-1]/prices[0]-1,'max_drawdown_fraction':mdd,
            'annualized_volatility_fraction':statistics.stdev(logs)*math.sqrt(365) if len(logs)>1 else None,
            'method':'Consecutive daily closes; sample standard deviation of log returns × sqrt(365); close/running peak − 1',
            'interpretation':'Historical description, not a forecast or evidence of investment skill'}


def simulate(series,profile=None,strategy='buy_hold',lookback=7,signals=None,start_index=None):
    profile=DecisionProfile.model_validate(profile or {});rows=price_rows(series)
    if strategy not in ('buy_hold','momentum','dca','model_signals'):raise ValueError('Unknown paper strategy')
    if not 1<=lookback<=90:raise ValueError('Lookback must be 1..90')
    start_index=lookback+1 if start_index is None else start_index
    if not 1<=start_index<len(rows)-1:raise ValueError('Need prior data and at least2 execution days')
    if strategy=='momentum' and start_index<lookback+1:raise ValueError('Momentum needs the complete prior lookback window')
    budget=profile.budget_usd or 1000.0;cash=budget;qty=0.0;fee=profile.fee_bps/10000;slip=profile.slippage_bps/10000
    signals=signals or {};cap=profile.allocation_fraction;curve=[];trades=[];fees=0.;slippage=0.;turnover=0.;held=0.;peak=budget;mdd=0.
    execution_has_ohlc=all('open_usd' in r for r in series)
    dca_stages=max(1,math.ceil((len(rows)-start_index)/7))
    for i in range(start_index,len(rows)):
        row=rows[i];prior=rows[i-1];op=row['open'];close=row['close'];equity=cash+qty*op
        if strategy=='buy_hold':target=cap if i==start_index else qty*op/equity
        elif strategy=='momentum':target=cap if prior['close']>rows[i-1-lookback]['close'] else 0.
        elif strategy=='dca':target=cap*min(1,(math.floor((i-start_index)/7)+1)/dca_stages)
        else:
            # Forecast/paper signal is labelled at prior completed observation, never the execution row.
            if prior['close_time'] in signals:held=float(signals[prior['close_time']])
            if not math.isfinite(held) or not 0<=held<=1:raise ValueError('Model weights must be finite long/cash fractions')
            target=min(cap,held)
        delta=(equity*target/op)-qty
        if strategy=='dca':delta=(budget*cap/dca_stages)/(op*(1+slip)*(1+fee)) if (i-start_index)%7==0 else 0.
        if abs(delta*op)>1e-8:
            if delta>0:
                exec_price=op*(1+slip);delta=min(delta,cash/(exec_price*(1+fee)))
                amount=delta*exec_price;charge=amount*fee;cash-=amount+charge;qty+=delta;side='buy'
            else:
                delta=max(delta,-qty);exec_price=op*(1-slip);amount=-delta*exec_price;charge=amount*fee
                cash+=amount-charge;qty+=delta;side='sell'
            fees+=charge;slippage+=abs(delta)*abs(exec_price-op);turnover+=abs(delta)*op
            trades.append({'time':row['time'],'signal_as_of':prior['close_time'],'side':side,'quantity':abs(delta),'reference_price_usd':op,
                           'execution_price_usd':exec_price,'fee_usd':charge,'target_fraction':target})
        marked=cash+qty*close;peak=max(peak,marked);mdd=min(mdd,marked/peak-1)
        curve.append({'time':row['close_time'],'equity_usd':marked,'cash_usd':cash,'asset_quantity':qty})
    # Fair terminal liquidation: charge exit costs for all strategies including buy-and-hold.
    final_price=rows[-1]['close']
    if qty>0:
        price=final_price*(1-slip);amount=qty*price;charge=amount*fee;cash+=amount-charge
        fees+=charge;slippage+=qty*(final_price-price);turnover+=qty*final_price
        trades.append({'time':rows[-1]['close_time'],'side':'terminal_sell','quantity':qty,'reference_price_usd':final_price,'execution_price_usd':price,'fee_usd':charge})
        curve[-1].update(equity_usd=cash,cash_usd=cash,asset_quantity=0);mdd=min(mdd,cash/peak-1)
    return {'strategy':strategy,'budget_usd':budget,'budget_is_default_scenario':profile.budget_usd is None,
            'start':rows[start_index]['time'],'end':rows[-1]['close_time'],'final_equity_usd':cash,'net_return_fraction':cash/budget-1,
            'max_drawdown_fraction':mdd,'fees_usd':fees,'slippage_usd':slippage,'turnover_fraction':turnover/budget,
            'trades':trades,'curve':curve,'profile':profile.model_dump(),'signal_lookback_days':lookback,
            'execution_convention':'Signal from prior completed close, executed at next daily open' if execution_has_ohlc else 'Signal from prior price observation, executed at next observation price; no intraday execution information',
            'terminal_liquidation':True,'scope':'Historical paper simulation, not a promised return; inputs retrieved now may be retrospective revisions'}


def compare_strategies(series,profile=None,lookback=7,signals=None):
    strategies=['buy_hold','momentum','dca']+(['model_signals'] if signals else [])
    return {'results':[simulate(series,profile,s,lookback,signals) for s in strategies],
            'benchmark':'buy_hold','same_windows_and_costs':True,'strategy_selection':'Explicit comparison presets, not an agent decision policy'}


def loss_scenarios(profile):
    p=DecisionProfile.model_validate(profile or {});budget=p.budget_usd or 1000.;exposure=budget*p.allocation_fraction
    return {'assumed_budget_usd':budget,'budget_is_default_scenario':p.budget_usd is None,'exposure_usd':exposure,
            'scenarios':[{'asset_change_fraction':decline,'loss_usd':exposure*abs(decline),
                          'exceeds_user_max_loss':p.max_loss_usd is not None and exposure*abs(decline)>p.max_loss_usd} for decline in (-.1,-.2,-.4,-1)],
            'note':'Hypothetical asset losses, not probabilities or forecasts; fees add losses. Spot long/cash only.'}
