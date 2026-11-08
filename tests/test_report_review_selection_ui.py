"""Independent review selection, safe defaults, and truthful delivery labels."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from fixtures import project_fixture
from research_agent.runtime import AgentRuntime
from research_agent.server import create_app


WEB = Path(__file__).resolve().parents[1] / 'src/research_agent/web'


class ReportReviewRequestTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.runtime = AgentRuntime(root=project_fixture(Path(temp.name)/'project'))
        self.app = create_app(self.runtime)
        self.sid = self.runtime.create_session()['session_id']
        self.headers = {'X-Local-Token': self.app.state.csrf_token}
        self.route = '/api/sessions/' + self.sid + '/messages'

    def request(self, payload):
        with TestClient(self.app, base_url='http://127.0.0.1:8765') as client, \
             patch.object(self.runtime, 'start', return_value={'state':'synthetic_not_started'}) as start:
            response = client.post(self.route, json=payload, headers=self.headers)
        return response, start

    def test_source_assistance_does_not_silently_change_reviewer(self):
        response, start = self.request({'prompt':'Create an ETH report', 'deliverable':'report', 'jev_assistance':True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(start.call_args.kwargs['report_review_provider'], 'generation_model')
        self.assertIs(start.call_args.kwargs['jev_assistance'], True)

    def test_explicit_jev_reviewer_can_be_used_without_source_reranking(self):
        response, start = self.request({'prompt':'Create an ETH report', 'deliverable':'report',
                                       'jev_assistance':False, 'report_review_provider':'jev'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(start.call_args.kwargs['report_review_provider'], 'jev')
        self.assertIs(start.call_args.kwargs['jev_assistance'], False)

    def test_invalid_provider_is_rejected_before_execution(self):
        for provider in ('doubao', 'auto', '', True, 1, None):
            with self.subTest(provider=provider):
                response, start = self.request({'prompt':'Create an ETH report', 'report_review_provider':provider})
                self.assertEqual(response.status_code, 422)
                start.assert_not_called()

    def test_refresh_cannot_become_a_report_with_any_reviewer(self):
        response, start = self.request({'prompt':'Refresh ETH sources', 'purpose':'market_refresh',
                                       'deliverable':'report', 'report_review_provider':'jev'})
        self.assertEqual(response.status_code, 400)
        start.assert_not_called()


@unittest.skipUnless(sys.platform == 'darwin', 'Uses the built-in macOS JavaScript runtime, no downloaded dependencies')
class ReportReviewFrontendTests(unittest.TestCase):
    def run_js(self, checks):
        script = (WEB/'dashboard.js').read_text()
        source = script.split('const finite=',1)[0]
        source += script[script.index('function chatReportLinks('):script.index('function chatTurn(')]
        source += script[script.index('const researchStages='):script.index('async function refreshSession(')]
        mocks = '''
          const saved = {};
          const controls = {'chat':{innerHTML:''},'jev-assistance':{},'report-review-provider':{}};
          for(const id of ['job-status','job-progress-link','job-description','job-previous',
                           'job-elapsed','stop-job','job-progress-label'])
            controls[id]={dataset:{},textContent:'',setAttribute:(key,value)=>{}};
          const document = {body:{dataset:{}},getElementById:id=>controls[id]};
          const localStorage = {getItem:key=>saved[key]??null,setItem:(key,value)=>saved[key]=value};
        '''
        code = 'function run(){\n' + mocks + source + '\n' + checks + '\n}'
        result = subprocess.run(['/usr/bin/osascript','-l','JavaScript','-'],input=code,
                                text=True,capture_output=True,check=True)
        return json.loads(result.stdout)

    def test_independent_preferences_and_disabled_execution_controls(self):
        result = self.run_js('''
          state.modelConfigured=true;
          saveJevPreference(true);saveReportReviewPreference('jev');
          saveJevPreference(false);
          const independent=state.reportReviewProvider==='jev'&&!state.jevAssistance;
          state.busy=true;configureJevControl();
          const disabled=controls['jev-assistance'].disabled&&controls['report-review-provider'].disabled;
          saveReportReviewPreference('unknown');
          return JSON.stringify({independent,disabled,fallback:state.reportReviewProvider,
                                 savedSource:saved['aether-jev-assistance']});
        ''')
        self.assertEqual(result, {'independent':True,'disabled':True,'fallback':'generation_model','savedSource':'false'})

    def test_notice_can_never_be_labelled_sources_checked(self):
        result = self.run_js('''
          return JSON.stringify({
            gap:reportDeliveryBadge('evidence_gap','reviewed'),
            minimal:reportDeliveryBadge('minimal','reviewed'),
            legacy:reportDeliveryBadge(undefined,'reviewed'),
            unreviewed:reportDeliveryBadge('full','needs_revision'),
            actual:reportReviewLabel('generation_model','doubao-seed-2.1-pro-260915'),
            jev:reportReviewLabel('jev','jev-1.14.0')
          });
        ''')
        self.assertEqual(result['gap'], {'label':'Evidence gap','tone':'warn'})
        self.assertEqual(result['minimal']['label'], 'Limited report')
        self.assertEqual(result['legacy']['label'], 'Sources checked')
        self.assertEqual(result['unreviewed']['label'], 'Archived report')
        self.assertEqual(result['actual'], 'Review: doubao-seed-2.1-pro-260915')
        self.assertEqual(result['jev'], 'Review: jev-1.14.0')

    def test_progress_and_report_links_use_saved_actual_reviewer_not_current_preference(self):
        result = self.run_js('''
          state.reportReviewProvider='generation_model';
          const job={job_id:'synthetic',state:'running',deliverable:'report',
                     report_review_provider:'jev',report_review_model:'jev-1.14.0',
                     created_at:'2026-01-01T00:00:00Z',published_reports:['/reports/research-aaaaaaaaaaaa.md']};
          renderResearchProgress(job,{job_id:'synthetic',stage:'verification'},true,false);
          const reviewProgress=controls['job-description'].textContent;
          const normalLinks=chatReportLinks(job);
          const gapLinks=chatReportLinks({...job,report_delivery_kind:'evidence_gap'});
          const limitedLinks=chatReportLinks({...job,report_delivery_kind:'minimal'});
          renderResearchProgress({...job,deliverable:'answer'},
                                 {job_id:'synthetic',stage:'verification'},true,false);
          return JSON.stringify({reviewProgress,normalLinks,gapLinks,limitedLinks,
                                 answerProgress:controls['job-description'].textContent});
        ''')
        self.assertIn('Review: jev-1.14.0', result['reviewProgress'])
        self.assertIn('Review: jev-1.14.0', result['normalLinks'])
        self.assertIn('Evidence gap saved', result['gapLinks'])
        self.assertNotIn('Review:', result['gapLinks'])
        self.assertIn('Your limited report is ready', result['limitedLinks'])
        self.assertNotIn('Jev', result['answerProgress'])
        self.assertNotIn('jev', result['answerProgress'])

    def test_whole_dashboard_script_parses(self):
        script = (WEB/'dashboard.js').read_text()
        result = subprocess.run(['/usr/bin/osascript','-l','JavaScript','-'],
            input='function run(){new Function('+json.dumps(script)+');return "parsed"}',
            text=True,capture_output=True,check=True)
        self.assertEqual(result.stdout.strip(), 'parsed')


if __name__ == '__main__':
    unittest.main()
