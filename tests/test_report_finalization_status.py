"""A poller must not observe failure while reserved report review is running."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from research_agent.runtime import AgentRuntime
from research_agent.settings import BRIDGE_MODE,Settings


class ReportFinalizationStatusTests(unittest.TestCase):
    def run_blocked_finalizer(self,outcome):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);session=root/'sessions'/('a'*32);job=session/'jobs'/('b'*32)
            job.mkdir(parents=True);(session/'workspace').mkdir()
            meta={'session_id':'a'*32,'model_mode':BRIDGE_MODE,'model_id':'offline-fixture',
                'data_run':str(root/'run'),'cutoff':'2026-11-08T00:00:00Z','mode':'observed'}
            (session/'session.json').write_text(json.dumps(meta))
            initial={'job_id':'b'*32,'state':'running','deliverable':'report'}
            (job/'status.json').write_text(json.dumps(initial))
            runtime=AgentRuntime.__new__(AgentRuntime);runtime.settings=Settings(memory_enabled=False)
            runtime.root=root;runtime.state_dir=root/'state';runtime.lock=threading.RLock()
            runtime.active={'a'*32:'b'*32};runtime.audit_builder=None;runtime.software_fingerprint='offline-fixture'
            cancel=threading.Event();entered=threading.Event();release=threading.Event()
            service=SimpleNamespace(published_reports=[],last_report=None,run_metadata={})
            def build(*args,**kwargs):
                emit=args[4]
                def finalize(**options):
                    emit('review_started',{'model':'offline-review-fixture','attempt':1})
                    entered.set()
                    if not release.wait(3):raise TimeoutError('Offline finalizer fixture was not released')
                    if cancel.is_set():raise RuntimeError('Synthetic cancellation during final review')
                    if outcome=='failed':raise RuntimeError('Synthetic final delivery failure')
                    service.published_reports.append('reports/research-fixture.json')
                    service.last_report={'path':service.published_reports[-1],'delivery_kind':'minimal',
                        'draft':{'title':'Synthetic limited report','summary':'Fixture for lifecycle only.'}}
                    emit('review_finished',{'model':'offline-review-fixture'})
                service.finalize_minimal_delivery=finalize
                return service
            runtime.build_research_tools=build
            # Stop after tool construction, before opening the graph's SQLite
            # checkpoint. This is an explicit synthetic author failure; the
            # actual production exception/final-delivery path remains in use.
            with (patch('research_agent.runtime.ConversationModel',return_value=object()),
                  patch('research_agent.runtime.install_skills'),
                  patch('research_agent.runtime.sqlite3.connect',side_effect=RuntimeError('Synthetic author budget exhausted'))):
                thread=threading.Thread(target=runtime._run,args=(session,job,'Synthetic report request',cancel))
                thread.start()
                try:
                    self.assertTrue(entered.wait(3),'Reserved finalizer did not start')
                    during=json.loads((job/'status.json').read_text())
                    self.assertEqual(during['state'],'running')
                    self.assertEqual(during['phase'],'finalizing_report')
                    self.assertNotIn('finished_at',during);self.assertNotIn('error',during)
                    self.assertIn('a'*32,runtime.active)
                    if outcome=='cancelled':cancel.set()
                finally:
                    release.set();thread.join(3)
                self.assertFalse(thread.is_alive())
            finished=json.loads((job/'status.json').read_text())
            self.assertEqual(finished['state'],outcome)
            self.assertIn('finished_at',finished);self.assertNotIn('phase',finished)
            self.assertNotIn('a'*32,runtime.active)
            return finished

    def test_final_delivery_stays_running_until_report_is_committed(self):
        result=self.run_blocked_finalizer('completed')
        self.assertEqual(result['report_delivery_kind'],'minimal')
        self.assertIn('Synthetic author budget exhausted',result['delivery_warning'])
        self.assertNotIn('error',result)

    def test_failed_final_delivery_becomes_terminal_only_after_review_finishes(self):
        result=self.run_blocked_finalizer('failed')
        self.assertIn('Synthetic final delivery failure',result['final_delivery_error'])

    def test_cancellation_during_final_delivery_is_terminal_after_cleanup(self):
        result=self.run_blocked_finalizer('cancelled')
        self.assertIn('cancellation',result['error'])
        self.assertNotIn('published_reports',result)


if __name__=='__main__':unittest.main()
