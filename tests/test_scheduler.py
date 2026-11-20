import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from research_agent.scheduler import Scheduler,Subscription,next_due,scheduled_report_request


class Runtime:
    def __init__(self):
        self.lock=threading.RLock();self.active={};self.jobs={};self.started=0
        self.requests = []
        self.knowledge=SimpleNamespace(search_news=lambda *a,**k:[])
        self.live_data=SimpleNamespace(get=lambda *a:{'record':{'values':{'price_usd':'100'}}})
    def create_session(self,**kw):return {'session_id':'fixture'+str(self.started),'external_run_id':kw.get('external_run_id')}
    def start(self,sid,prompt,deliverable,**kwargs):
        self.requests.append((sid, prompt, deliverable, kwargs))
        self.started+=1;job={'job_id':'j'+str(self.started),'state':'running'};self.jobs[sid]=job;self.active[sid]=job
        return job
    def snapshot(self,sid):return {'jobs':[self.jobs[sid]]}


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.rt=Runtime()
        self.scheduler=Scheduler(self.rt,Path(self.tmp.name),feed_scanner=lambda _:None)
    def due(self,config):
        row=self.scheduler.save(config)
        with self.scheduler.db() as c:c.execute('UPDATE subscriptions SET next_run=? WHERE id=?',('2026-01-01T00:00:00Z',row['id']))
        return row
    def test_local_calendar_is_timezone_aware(self):
        config=Subscription(enabled=True).model_dump()
        self.assertEqual(next_due(config,'2026-01-01T00:30:00Z'),'2026-01-01T01:00:00Z')
        self.assertEqual(next_due(config,'2026-01-01T02:00:00Z'),'2026-01-02T01:00:00Z')
        with self.assertRaises(ValueError):Subscription(cadence='once',start_at='2026-01-01T00:00:00')
    def test_restart_preserves_run_and_duplicate_tick_does_not_start_twice(self):
        self.due({'enabled':True,'cadence':'interval','interval_minutes':15})
        self.scheduler.tick();self.scheduler.tick();self.assertEqual(self.rt.started,1)
        restored=Scheduler(self.rt,Path(self.tmp.name),feed_scanner=lambda _:None)
        restored.tick();self.assertEqual(self.rt.started,1);self.assertEqual(restored.runs()[0]['state'],'running')
    def test_once_disables_after_completion_and_retains_result(self):
        sub=self.due({'enabled':True,'cadence':'once','start_at':'2026-01-01T00:00:00Z'})
        self.scheduler.tick();sid=next(iter(self.rt.jobs));self.rt.jobs[sid]['state']='completed';self.rt.active.clear()
        self.scheduler.tick();self.assertFalse(self.scheduler.list()[0]['enabled']);self.assertIsNone(self.scheduler.list()[0]['next_run_at'])
        self.assertEqual(self.scheduler.runs()[0]['state'],'completed');self.assertEqual(self.rt.started,1)

    def test_daily_request_uses_the_subscription_calendar_across_dst(self):
        config = Subscription(timezone='America/New_York').model_dump()
        prompt = scheduled_report_request(config, '2026-03-08T13:00:00Z')
        self.assertEqual(
            prompt,
            'Create and publish a concise English ETH daily report for UTC '
            '2026-03-07T14:00:00Z (inclusive) to 2026-03-08T13:00:00Z (exclusive).',
        )

    def test_weekly_request_preserves_window_and_job_lineage_on_retry(self):
        self.due({'enabled': True, 'cadence': 'weekly'})
        self.rt.active['other-job'] = {}
        self.scheduler.tick()
        self.assertEqual(self.scheduler.list()[0]['next_run_at'], '2026-01-01T00:00:00Z')
        self.rt.active.clear()

        self.scheduler.tick()
        sid, prompt, deliverable, options = self.rt.requests[0]
        scope = ('English ETH weekly report for UTC 2025-12-25T00:00:00Z (inclusive) '
                 'to 2026-01-01T00:00:00Z (exclusive).')
        self.assertEqual(prompt, f'Create and publish a concise {scope}')
        self.assertEqual(deliverable, 'report')
        self.assertEqual(options['schedule_run_id'], self.scheduler.runs()[0]['id'])

        failed_job = self.rt.jobs[sid]
        failed_job.update(state='failed', error='fixture timeout')
        self.rt.active.clear()
        self.scheduler.tick()
        with self.scheduler.db() as connection:
            connection.execute('UPDATE subscriptions SET next_run=?', ('2026-01-02T00:00:00Z',))
        self.scheduler.tick()

        retried_sid, retried_prompt, retried_deliverable, retried_options = self.rt.requests[1]
        self.assertEqual((retried_sid, retried_deliverable), (sid, 'report'))
        self.assertEqual(retried_prompt, f'Continue preparing and publish the unfinished {scope}')
        self.assertEqual(retried_options['continued_from'], failed_job['job_id'])

    def test_periodic_retry_reuses_earliest_window_and_latest_saved_job(self):
        sub = self.due({'enabled': True, 'cadence': 'daily'})
        sid = 'saved-session'
        attempts = (
            ('first', '2025-12-30T00:00:00Z', 'original-job'),
            ('second', '2025-12-30T00:01:00Z', 'last-saved-job'),
            ('third', '2025-12-30T00:02:00Z', None),
        )
        with self.scheduler.db() as connection:
            connection.executemany(
                "INSERT INTO runs(id,subscription_id,session_id,scheduled_for,job_id,state,created_at) "
                "VALUES(?,?,?,?,?,'failed',?)",
                [(run_id, sub['id'], sid, scheduled_for, job_id, scheduled_for)
                 for run_id, scheduled_for, job_id in attempts],
            )
            connection.execute('UPDATE subscriptions SET baseline=? WHERE id=?',
                               (json.dumps({'retry_session_id': sid}), sub['id']))

        self.scheduler.tick()

        retried_sid, prompt, deliverable, options = self.rt.requests[0]
        self.assertEqual((retried_sid, deliverable), (sid, 'report'))
        self.assertEqual(
            prompt,
            'Continue preparing and publish the unfinished English ETH daily report for UTC '
            '2025-12-29T00:00:00Z (inclusive) to 2025-12-30T00:00:00Z (exclusive).',
        )
        self.assertEqual(options['continued_from'], 'last-saved-job')

    def test_failed_once_has_one_bounded_retry(self):
        self.due({'enabled':True,'cadence':'once','start_at':'2026-01-01T00:00:00Z'})
        self.scheduler.tick();sid=next(iter(self.rt.jobs));self.rt.jobs[sid].update(state='failed',error='fixture timeout');self.rt.active.clear()
        self.scheduler.tick();sub=self.scheduler.list()[0];self.assertEqual(sub['failures'],1);self.assertIsNotNone(sub['next_run_at'])
        with self.scheduler.db() as c:c.execute('UPDATE subscriptions SET next_run=?',('2026-01-02T00:00:00Z',))
        self.scheduler.tick();sid=list(self.rt.jobs)[-1];self.rt.jobs[sid].update(state='failed',error='fixture timeout');self.rt.active.clear()
        self.scheduler.tick();self.assertEqual(self.rt.started,2);self.assertFalse(self.scheduler.list()[0]['enabled'])

    def test_failure_before_job_start_retries_once_instead_of_stranding_once(self):
        self.due({'enabled':True,'cadence':'once','start_at':'2026-01-01T00:00:00Z'})
        with patch.object(self.rt,'create_session',side_effect=RuntimeError('fixture storage interruption')):
            self.scheduler.tick();self.assertEqual(self.scheduler.list()[0]['failures'],1)
            self.assertIsNotNone(self.scheduler.list()[0]['next_run_at'])
            with self.scheduler.db() as c:c.execute('UPDATE subscriptions SET next_run=?',('2026-01-02T00:00:00Z',))
            self.scheduler.tick()
        self.assertFalse(self.scheduler.list()[0]['enabled']);self.assertEqual(len(self.scheduler.runs()),2)
    def test_unchanged_monitor_does_not_call_model_and_busy_run_is_deferred(self):
        self.due({'enabled':True,'mode':'monitor','cadence':'interval'})
        self.scheduler.tick();self.assertEqual(self.rt.started,0)
        with self.scheduler.db() as c:c.execute('UPDATE subscriptions SET next_run=?',('2026-01-02T00:00:00Z',))
        self.scheduler.tick();self.assertEqual(self.rt.started,0)
        sub=self.due({'enabled':False});self.scheduler.run_now(sub['id']);self.rt.active['manual']={}
        self.scheduler.tick();self.assertEqual(self.rt.started,0)
        self.rt.active.clear()
        with self.scheduler.db() as c:c.execute('UPDATE subscriptions SET next_run=? WHERE id=?',('2026-01-03T00:00:00Z',sub['id']))
        self.scheduler.tick();self.assertEqual(self.rt.started,1)


if __name__=='__main__':unittest.main()
