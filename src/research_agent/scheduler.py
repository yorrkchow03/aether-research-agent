"""Persistent local brief/monitor jobs invoking the same research harness."""
from __future__ import annotations
import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from pydantic import BaseModel,Field,model_validator
from typing import Literal
from research_data.http import utcnow,dumps
from research_data.normalize import timestamp,iso
from .feeds import scan_feed


class Subscription(BaseModel):
    id:str|None=Field(default=None,pattern=r'^[a-f0-9]{12}$')
    name:str=Field(default='ETH daily brief',min_length=1,max_length=80)
    asset:Literal['ETH']='ETH'
    enabled:bool=False
    mode:Literal['brief','monitor']='brief'
    cadence:Literal['daily','weekly','interval','once']='daily'
    timezone:str='Asia/Shanghai'
    hour:int=Field(default=9,ge=0,le=23)
    minute:int=Field(default=0,ge=0,le=59)
    weekday:int=Field(default=0,ge=0,le=6)
    interval_minutes:int=Field(default=15,ge=1,le=10080)
    start_at:str|None=None
    price_change_fraction:float=Field(default=.03,gt=0,le=1)

    @model_validator(mode='after')
    def valid(self):
        try:ZoneInfo(self.timezone)
        except Exception:raise ValueError('Unknown timezone') from None
        if self.cadence=='once' and not self.start_at:raise ValueError('A one-time brief needs a start time')
        if self.start_at:
            value=datetime.fromisoformat(self.start_at.replace('Z','+00:00'))
            if value.tzinfo is None:raise ValueError('Start time must include a timezone offset')
        if self.mode=='monitor' and self.cadence!='interval':raise ValueError('Change monitoring uses an interval schedule')
        return self


def next_due(config,now=None,first=False):
    c=Subscription.model_validate(config);now=timestamp(now or utcnow())
    if first and c.start_at and timestamp(c.start_at)>now:return iso(timestamp(c.start_at))
    if c.cadence=='once':return None if not first else iso(timestamp(c.start_at))
    if c.cadence=='interval':return iso(now+timedelta(minutes=c.interval_minutes))
    local=now.astimezone(ZoneInfo(c.timezone));candidate=local.replace(hour=c.hour,minute=c.minute,second=0,microsecond=0)
    if c.cadence=='weekly':candidate+=timedelta(days=(c.weekday-candidate.weekday())%7)
    if candidate<=local:candidate+=timedelta(days=7 if c.cadence=='weekly' else 1)
    return iso(candidate.astimezone(timezone.utc))


class Scheduler:
    def __init__(self,runtime,root,feed_scanner=scan_feed):
        self.runtime=runtime;self.path=Path(root)/'scheduler.sqlite';self.path.parent.mkdir(parents=True,exist_ok=True)
        self.feed_scanner=feed_scanner;self.stop_event=threading.Event();self.thread=None;self.lock=threading.RLock()
        with self.db() as c:c.executescript('''
            CREATE TABLE IF NOT EXISTS subscriptions(id TEXT PRIMARY KEY,config TEXT NOT NULL,next_run TEXT,
                last_checked TEXT,last_success TEXT,last_status TEXT,failures INTEGER NOT NULL DEFAULT 0,baseline TEXT);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,subscription_id TEXT NOT NULL,scheduled_for TEXT NOT NULL,
                state TEXT NOT NULL,session_id TEXT,job_id TEXT,created_at TEXT NOT NULL,finished_at TEXT,error TEXT,
                UNIQUE(subscription_id,scheduled_for));
        ''')

    @contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=15);c.row_factory=sqlite3.Row;c.execute('PRAGMA journal_mode=WAL')
        try:
            with c:yield c
        finally:c.close()

    def list(self):
        with self.db() as c:rows=c.execute('SELECT * FROM subscriptions ORDER BY rowid DESC').fetchall()
        return [{**json.loads(r['config']),'next_run_at':r['next_run'],'last_checked_at':r['last_checked'],
                 'last_success_at':r['last_success'],'last_status':r['last_status'],'failures':r['failures']} for r in rows]

    def save(self,body):
        config=Subscription.model_validate(body).model_dump();sid=config['id'] or uuid.uuid4().hex[:12];config['id']=sid
        due=next_due(config,first=True) if config['enabled'] else None
        with self.db() as c:
            c.execute('INSERT INTO subscriptions(id,config,next_run,last_status) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config,next_run=excluded.next_run,last_status=excluded.last_status',
                      (sid,dumps(config),due,'scheduled' if due else 'disabled'))
        return next(r for r in self.list() if r['id']==sid)

    def delete(self,sid):
        with self.db() as c:c.execute('DELETE FROM subscriptions WHERE id=?',(sid,))
        return {'status':'deleted'}

    def runs(self,limit=30):
        with self.db() as c:return [dict(r) for r in c.execute('SELECT * FROM runs ORDER BY created_at DESC LIMIT ?',(limit,)).fetchall()]

    def run_now(self,sid):
        with self.db() as c:
            if not c.execute('SELECT 1 FROM subscriptions WHERE id=?',(sid,)).fetchone():raise ValueError('Subscription not found')
            c.execute('UPDATE subscriptions SET next_run=?,last_status=? WHERE id=?',(utcnow(),'manual_run_requested',sid))
        # Tick also accepts a requested one-off on a disabled subscription.
        return {'status':'queued'}

    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop_event.clear();self.thread=threading.Thread(target=self._loop,name='research-scheduler',daemon=True);self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=2)

    def _loop(self):
        while not self.stop_event.is_set():
            try:self.tick()
            except Exception as exc:
                (self.path.parent/'scheduler-error.json').write_text(dumps({'at':utcnow(),'error':str(exc)[:300]}))
            self.stop_event.wait(2)

    def _observe(self):
        with self.db() as c:rows=c.execute("SELECT * FROM runs WHERE state IN ('starting','running')").fetchall()
        for row in rows:
            try:
                snap=self.runtime.snapshot(row['session_id']);job=next(j for j in snap['jobs'] if j['job_id']==row['job_id'] or (not row['job_id'] and j.get('schedule_run_id')==row['id']))
                state=job['state']
            except (ValueError,KeyError,FileNotFoundError,StopIteration):job={'error':'Saved job is unavailable after restart'};state='interrupted'
            if state not in ('completed','failed','cancelled','interrupted'):continue
            with self.db() as c:
                c.execute('UPDATE runs SET state=?,finished_at=?,error=? WHERE id=?',(state,utcnow(),job.get('error'),row['id']))
                sub=c.execute('SELECT * FROM subscriptions WHERE id=?',(row['subscription_id'],)).fetchone()
                if not sub:continue
                config=json.loads(sub['config']);failures=0 if state=='completed' else sub['failures']+1
                retry=config['enabled'] and state in ('failed','interrupted') and failures<2
                due=iso(timestamp(utcnow())+timedelta(minutes=1)) if retry else sub['next_run']
                if config['cadence']=='once' and not retry:config['enabled']=False;due=None
                baseline=sub['baseline'];saved_baseline=json.loads(baseline or '{}')
                if retry:saved_baseline['retry_session_id']=row['session_id']
                else:saved_baseline.pop('retry_session_id',None)
                baseline=dumps(saved_baseline)
                if state=='completed' and config['mode']=='monitor':
                    old=json.loads(baseline or '{}');old['news_hash']=self._news_hash();baseline=dumps(old)
                c.execute('UPDATE subscriptions SET config=?,next_run=?,last_success=?,last_status=?,failures=?,baseline=? WHERE id=?',
                          (dumps(config),due,utcnow() if state=='completed' else sub['last_success'],state,failures,baseline,sub['id']))

    def _news_hash(self):
        rows=self.runtime.knowledge.search_news('',limit=30,material_only=True)
        return hashlib.sha256(dumps(sorted((r['url'],r['version_id']) for r in rows)).encode()).hexdigest()

    def tick(self):
        with self.lock:
            self._observe();now=utcnow()
            with self.db() as c:due=c.execute('SELECT * FROM subscriptions WHERE next_run IS NOT NULL AND next_run<=?',(now,)).fetchall()
            for sub in due:
                config=json.loads(sub['config'])
                if not config['enabled'] and not (sub['last_status'] or '').startswith('manual_'):continue
                with self.runtime.lock:busy=bool(self.runtime.active)
                if busy:
                    status='waiting_for_current_research' if config['enabled'] else 'manual_waiting_for_current_research'
                    with self.db() as c:c.execute('UPDATE subscriptions SET next_run=?,last_status=? WHERE id=?',(iso(timestamp(now)+timedelta(minutes=1)),status,sub['id']))
                    continue
                baseline=json.loads(sub['baseline'] or '{}');reason='Scheduled research brief'
                if config['mode']=='monitor' and not baseline.get('retry_session_id'):
                    price=None;feed_gap=None
                    try:price=float(self.runtime.live_data.get('price')['record']['values']['price_usd'])
                    except Exception as exc:feed_gap='Price unavailable: '+str(exc)[:150]
                    try:self.feed_scanner(self.runtime.knowledge)
                    except Exception as exc:feed_gap='Feed unavailable: '+str(exc)[:150]
                    current={'price':price,'news_hash':self._news_hash()};changed=bool(baseline) and (
                        current['news_hash']!=baseline.get('news_hash') or (price and baseline.get('price') and abs(price/baseline['price']-1)>=config['price_change_fraction']))
                    if not changed:
                        with self.db() as c:c.execute('UPDATE subscriptions SET next_run=?,last_checked=?,last_status=?,baseline=? WHERE id=?',
                                                     (next_due(config),now,feed_gap or 'unchanged',dumps(baseline or current),sub['id']))
                        continue
                    baseline=current;reason='New saved publisher evidence or configured price change since the monitoring baseline'
                with self.runtime.lock:busy=bool(self.runtime.active)
                if busy:continue
                run_id=uuid.uuid4().hex[:12]
                with self.db() as c:
                    if c.execute('SELECT 1 FROM runs WHERE subscription_id=? AND scheduled_for=?',(sub['id'],sub['next_run'])).fetchone():continue
                    c.execute('INSERT INTO runs(id,subscription_id,scheduled_for,state,created_at) VALUES(?,?,?,?,?)',(run_id,sub['id'],sub['next_run'],'starting',now))
                    c.execute('UPDATE subscriptions SET next_run=?,last_checked=?,last_status=?,baseline=? WHERE id=?',
                              (next_due(config) if config['enabled'] else None,now,'starting',dumps(baseline),sub['id']))
                try:
                    retry_sid=baseline.pop('retry_session_id',None)
                    session={'session_id':retry_sid} if retry_sid else self.runtime.create_session(title=config['name'],research_mode='current',preferences='Scheduled personal research. No order execution.',external_run_id=run_id)
                    with self.db() as c:c.execute('UPDATE runs SET session_id=? WHERE id=?',(session['session_id'],run_id))
                    prompt=(f'{reason}. Create and publish a concise English ETH research brief with fresh actual data and material recent news. '
                            'Recall previous research and source versions to explain what changed. Choose relevant metrics, tools and calculations yourself. '
                            'Read important original announcements when accessible; note gaps and continue. Distinguish facts, cautious interpretations, planned '
                            'events and confirmed events. Record meaningful source-backed events when useful. Avoid unsupported price forecasts or invented user holdings. '
                            'Prefer 3–5 important observations and at most two careful interpretations; do not expand a routine brief into a broad survey. '
                            'Save a short source-reviewed report with publish_report; no paper simulation or purchasing decision is required for a routine brief.')
                    if retry_sid:prompt=('Continue the unfinished scheduled brief in this session from saved work and the most recent draft. Correct the last source-check errors, remove unsupported assertions, and publish a concise source-reviewed report. Do not repeat the full search or recalculate unchanged inputs; refresh only if required for an explicit current claim. Preserve data timestamps. '+prompt)
                    job=self.runtime.start(session['session_id'],prompt,'report',schedule_run_id=run_id)
                    with self.db() as c:c.execute("UPDATE runs SET state='running',session_id=?,job_id=? WHERE id=?",(session['session_id'],job['job_id'],run_id))
                except Exception as exc:
                    with self.db() as c:
                        c.execute("UPDATE runs SET state='failed',finished_at=?,error=? WHERE id=?",(utcnow(),str(exc)[:300],run_id))
                        failures=sub['failures']+1;retry=config['enabled'] and failures<2
                        due=iso(timestamp(utcnow())+timedelta(minutes=1)) if retry else next_due(config) if config['enabled'] else None
                        if config['cadence']=='once' and not retry:config['enabled']=False;due=None
                        c.execute("UPDATE subscriptions SET config=?,next_run=?,failures=?,last_status='failed_to_start' WHERE id=?",(dumps(config),due,failures,sub['id']))
