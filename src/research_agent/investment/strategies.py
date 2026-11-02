"""Chronological local model research with saved folds, next-open costs and baselines."""
from __future__ import annotations
import hashlib,json,math,statistics
from datetime import timedelta
import numpy as np
import sklearn
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import brier_score_loss,log_loss,balanced_accuracy_score
from pydantic import BaseModel,ConfigDict,Field
from research_data.normalize import timestamp,iso
from research_agent.simulation import simulate,price_rows

VERSION='walk-forward-v1'
FEATURES=['return_1d','return_3d','return_7d','return_21d','realized_vol_7d','realized_vol_21d','distance_sma21','drawdown_21d','volume_vs_prior20','range_fraction']

class StrategyConfig(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    asset:str=Field(default='ETH',pattern='^(ETH|BTC)$')
    history_days:int=Field(default=730,ge=240,le=1095)
    budget_usd:float=Field(default=1000,gt=0,le=1e9)
    fee_bps:float=Field(default=10,ge=0,le=500)
    slippage_bps:float=Field(default=10,ge=0,le=500)
    allocation_cap:float=Field(default=.5,gt=0,le=1)
    long_probability:float=Field(default=.6,gt=.5,lt=1)
    cash_probability:float=Field(default=.4,gt=0,lt=.5)
    refit_every:int=Field(default=14,ge=7,le=30)
    execution_delay_bars:int=Field(default=1,ge=1,le=2)
    target_volatility:float=Field(default=.2,gt=0,le=1)


def features(series,index):
    if index<21:raise ValueError('Features require 21 prior completed periods')
    rows=series[index-21:index+1];prices=np.array([float(r['close_usd']) for r in rows]);volumes=np.array([float(r['volume_asset']) for r in rows])
    if not np.isfinite(prices).all() or min(prices)<=0 or not np.isfinite(volumes).all() or min(volumes)<0:raise ValueError('Invalid OHLCV features')
    logs=np.diff(np.log(prices));last=series[index];denom=float(np.mean(volumes[-21:-1]))
    if denom<=0:raise ValueError('Volume feature has no nonzero prior baseline')
    result=[prices[-1]/prices[-2]-1,prices[-1]/prices[-4]-1,prices[-1]/prices[-8]-1,prices[-1]/prices[0]-1,
            float(np.std(logs[-7:],ddof=1)),float(np.std(logs,ddof=1)),prices[-1]/float(np.mean(prices[-21:]))-1,
            prices[-1]/float(np.max(prices[-21:]))-1,volumes[-1]/denom-1,(float(last['high_usd'])-float(last['low_usd']))/prices[-1]]
    if not np.isfinite(result).all():raise ValueError('Feature vector is not finite')
    return [float(v) for v in result]


def samples(series,delay=1):
    price_rows(series)  # checks spacing and positive prices
    output=[]
    for i in range(21,len(series)-delay-1):
        execute=i+delay+1;future=series[execute];value=float(future['close_usd'])/float(future['open_usd'])-1
        output.append({'feature_index':i,'decision_at':series[i]['period_end'],'execute_index':execute,'execute_at':future['observed_at'],'label_available_at':future['period_end'],'target_return_fraction':value,'label':int(value>0),'x':features(series,i)})
    return output


def training_before(rows,decision_at):
    cutoff=timestamp(decision_at)
    return [r for r in rows if timestamp(r['label_available_at'])<cutoff]


def estimator(name):
    if name=='logistic':return make_pipeline(StandardScaler(),LogisticRegression(C=.2,max_iter=500,random_state=42))
    if name=='random_forest':return RandomForestClassifier(n_estimators=80,max_depth=3,min_samples_leaf=15,max_features=.7,random_state=42,n_jobs=1)
    raise ValueError('Unknown model family')


def fit_model(name,training):
    if len(training)<100:raise ValueError('At least100 fully available training labels required')
    labels=[r['label'] for r in training]
    if min(labels)==max(labels):raise ValueError('Training data has only one direction class')
    model=estimator(name);model.fit([r['x'] for r in training],labels);return model


def model_json(model,name):
    if name=='logistic':
        scale,reg=model.steps[0][1],model.steps[1][1]
        return {'family':name,'scale_mean':scale.mean_.tolist(),'scale_scale':scale.scale_.tolist(),'coefficients':reg.coef_.tolist(),'intercept':reg.intercept_.tolist(),'classes':reg.classes_.tolist()}
    trees=[]
    for estimator_ in model.estimators_:
        t=estimator_.tree_;trees.append({'children_left':t.children_left.tolist(),'children_right':t.children_right.tolist(),'feature':t.feature.tolist(),'threshold':t.threshold.tolist(),'value':t.value.tolist()})
    return {'family':name,'classes':model.classes_.tolist(),'feature_importance':model.feature_importances_.tolist(),'trees':trees}


def scores(predictions):
    if not predictions:return {'samples':0}
    y=[p['label'] for p in predictions];p=[p['probability_up'] for p in predictions]
    bins=[]
    for low,high in [(0,.2),(.2,.4),(.4,.6),(.6,.8),(.8,1.00001)]:
        members=[r for r in predictions if low<=r['probability_up']<high]
        bins.append({'probability_bin':[low,min(high,1)],'count':len(members),'mean_prediction':statistics.mean(r['probability_up'] for r in members) if members else None,'observed_up_fraction':statistics.mean(r['label'] for r in members) if members else None})
    return {'samples':len(y),'brier_score':float(brier_score_loss(y,p)),'log_loss':float(log_loss(y,p,labels=[0,1])),'directional_accuracy':float(np.mean((np.array(p)>=.5)==np.array(y))),'balanced_accuracy':float(balanced_accuracy_score(y,[int(v>=.5) for v in p])),'calibration_bins':bins,'scope':'Out-of-sample direction scores, not a calibrated probability guarantee or net-profit accuracy'}


def forecast_weights(predictions,series,cfg):
    weights={};decisions=[];held=0
    for p in predictions:
        probability=p['probability_up'];annual=p['x'][5]*math.sqrt(365)
        cap=min(cfg.allocation_cap,cfg.target_volatility/annual) if annual>0 else cfg.allocation_cap
        if probability>=cfg.long_probability:held=cap;state='long_research'
        elif probability<=cfg.cash_probability:held=0;state='cash_research'
        else:held=min(held,cap);state='abstain_keep_previous_weight'
        execution=p['execute_index'];weights[series[execution-1]['period_end']]=held
        decisions.append({k:p[k] for k in ('decision_at','execute_at','label_available_at','probability_up','target_return_fraction','label') }|{'weight':held,'state':state})
    return weights,decisions


def run_experiment(history,config):
    cfg=config if isinstance(config,StrategyConfig) else StrategyConfig.model_validate(config)
    series=history['series'];rows=samples(series,cfg.execution_delay_bars)
    if len(rows)<220:raise ValueError('At least220 feature/target observations required for chronological research')
    a=int(len(rows)*.6);b=int(len(rows)*.8);development=rows[:a];holdout=rows[b:]
    validation=training_before(rows[a:b],holdout[0]['decision_at'])
    train=training_before(development,validation[0]['decision_at']);candidate_scores={};candidate_predictions={}
    for name in ('logistic','random_forest'):
        fitted=fit_model(name,train);probs=fitted.predict_proba([r['x'] for r in validation])[:,list(fitted.classes_).index(1)]
        predictions=[{**r,'probability_up':float(p)} for r,p in zip(validation,probs)]
        candidate_scores[name]=scores(predictions);candidate_predictions[name]=predictions
    chosen=min(candidate_scores,key=lambda name:candidate_scores[name]['brier_score']);predictions=[];folds=[]
    for start in range(0,len(holdout),cfg.refit_every):
        block=holdout[start:start+cfg.refit_every];training=training_before(rows,block[0]['decision_at']);model=fit_model(chosen,training)
        if max(timestamp(r['label_available_at']) for r in training)>=timestamp(block[0]['decision_at']):raise ValueError('Training label leaked into decision cutoff')
        probs=model.predict_proba([r['x'] for r in block])[:,list(model.classes_).index(1)]
        predictions.extend({**r,'probability_up':float(p)} for r,p in zip(block,probs))
        folds.append({'first_decision_at':block[0]['decision_at'],'last_decision_at':block[-1]['decision_at'],'training_samples':len(training),'last_training_label_available_at':training[-1]['label_available_at'],'test_samples':len(block),'model':model_json(model,chosen)})
    weights,decisions=forecast_weights(predictions,series,cfg);begin=predictions[0]['execute_index'];end=predictions[-1]['execute_index']
    data=series[:end+1];profile={'budget_usd':cfg.budget_usd,'fee_bps':cfg.fee_bps,'slippage_bps':cfg.slippage_bps,'allocation_fraction':cfg.allocation_cap}
    results=[]
    for strategy in ('buy_hold','dca','momentum','model_signals'):
        result=simulate(data,profile,strategy,7,weights if strategy=='model_signals' else None,start_index=begin)
        exposure=[]
        for point in result['curve']:
            idx=next(i for i,r in enumerate(data) if r['period_end']==point['time'])
            exposure.append(point['asset_quantity']*float(data[idx]['close_usd'])/point['equity_usd'] if point['equity_usd'] else 0)
        result['mean_marked_exposure_fraction']=statistics.mean(exposure);results.append(result)
    latest_decision=series[-1]['period_end'];latest_training=training_before(rows,latest_decision);latest_model=fit_model(chosen,latest_training);vector=features(series,len(series)-1)
    probability=float(latest_model.predict_proba([vector])[0,list(latest_model.classes_).index(1)]);vol=vector[5]*math.sqrt(365)
    latest={'decision_at':latest_decision,'execute_not_before':iso(timestamp(latest_decision)+timedelta(days=cfg.execution_delay_bars)),'probability_up':probability,'state':'long_research' if probability>=cfg.long_probability else 'cash_research' if probability<=cfg.cash_probability else 'abstain','weight_cap':min(cfg.allocation_cap,cfg.target_volatility/vol) if vol else cfg.allocation_cap,'realized_annual_volatility_fraction':vol,'model':model_json(latest_model,chosen),'features':dict(zip(FEATURES,vector)),'training_samples':len(latest_training)}
    return {'version':VERSION,'config':cfg.model_dump(),'source_record_id':history['record_id'],'source_scope':history['scope'],'feature_names':FEATURES,'model_selected':chosen,'selection_rule':'Lowest validation Brier score; holdout never used for model/threshold selection','split':{'development_samples':len(development),'validation_samples':len(validation),'holdout_samples':len(holdout),'development_end':development[-1]['decision_at'],'validation_start':validation[0]['decision_at'],'validation_end':validation[-1]['decision_at'],'holdout_start':holdout[0]['decision_at'],'holdout_end':holdout[-1]['decision_at']},'candidate_validation':candidate_scores,'holdout_scores':scores(predictions),'constant_probability_brier':float(brier_score_loss([p['label'] for p in predictions],[statistics.mean(r['label'] for r in train)]*len(predictions))),'folds':folds,'decisions':decisions,'comparison':{'results':results,'same_windows_and_costs':True,'allocation_cap':cfg.allocation_cap},'latest':latest,'environment':{'sklearn':sklearn.__version__,'numpy':np.__version__},'limitations':['Retrospective completed OHLCV from one venue; captures do not authenticate historical public availability','Feature decisions use only completed bars; targets are next-open-to-close after a declared full-bar execution delay','Daily-open fills remain idealized; fixed per-side fee/slippage assumptions omit latency, spread changes and market impact','Probability and allocation thresholds are predeclared research assumptions, not validated buy/sell rules','Two model families are selected on the chronological validation segment; holdout uses expanding refits with strictly earlier available labels','Small single-asset study and repeated local experiments cannot establish a profitable or statistically independent investment edge','No social/news/funding feature is backfilled without point-in-time availability; no leverage, shorting or real orders']}
