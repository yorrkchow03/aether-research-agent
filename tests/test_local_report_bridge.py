"""Exercise production Aether's local report bridge using deterministic test replies.

This is a smoke test for routing and publication gates, not an evaluation run.
Only synthetic sources and temporary job directories are used.
"""

from contextlib import contextmanager, ExitStack
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import time
import traceback
import unittest
from unittest.mock import patch

from research_agent.bridge import write_reply
from research_agent.periodic_workflow import PERIOD_STAGES
from research_agent.review import validate_saved_report_review
from research_agent.runtime import AgentRuntime
from research_agent.settings import BRIDGE_MODE
from fixtures import project_fixture


SOURCE_ID = 'page-' + 'd' * 24
QUOTE = 'The synthetic Ethereum fixture recorded that testing remained in progress.'
REPORT_PROMPT = (
    'Please write and save an ETH research report for 2026-10-01 to 2026-10-08 UTC. '
    'The start is inclusive and the end is exclusive. Use local archived evidence only. Do not use the internet.'
)
START = '2026-10-01T00:00:00Z'
END = '2026-10-08T00:00:00Z'
SOURCE = {
    'record_id': SOURCE_ID,
    'title': 'SYNTHETIC Ethereum testing notice',
    'text': QUOTE,
    'source_kind': 'page',
    'dataset_id': 'live.webpage',
    'published_at': '2026-10-02T00:00:00Z',
    'retrieved_at': '2026-10-02T00:00:00Z',
    'url': 'https://example.invalid/synthetic-ethereum-notice',
}
DRAFT = {
    'title': 'SYNTHETIC ETH test report',
    'summary': QUOTE,
    'scope': {'assets': ['ETH'], 'start': START, 'end': END},
    'observations': [{'statement': QUOTE, 'evidence_ids': [SOURCE_ID]}],
    'limitations': ['This report covers the synthetic evidence supplied for this test.'],
}


@contextmanager
def network_tripwire():
    attempts = []

    def denied(*args, **kwargs):
        location = ' → '.join(frame.name for frame in traceback.extract_stack(limit=12)[:-1])
        attempts.append(location)
        raise AssertionError('Smoke test attempted a provider/network operation')

    async def async_denied(*args, **kwargs):
        denied()

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def local_only(sock, address):
        if sock.family != socket.AF_UNIX:
            denied()
        return original_connect(sock, address)

    def local_only_ex(sock, address):
        if sock.family != socket.AF_UNIX:
            denied()
        return original_connect_ex(sock, address)

    with ExitStack() as stack:
        for name in ('research_agent.model.create_ark_model',
                     'research_agent.runtime.create_ark_model',
                     'research_agent.review.create_ark_model',
                     'research_agent.evidence_judgments.create_ark_model',
                     'httpx.Client.send'):
            stack.enter_context(patch(name, side_effect=denied))
        stack.enter_context(patch('httpx.AsyncClient.send', new=async_denied))
        stack.enter_context(patch.object(socket.socket, 'connect', new=local_only))
        stack.enter_context(patch.object(socket.socket, 'connect_ex', new=local_only_ex))
        yield attempts


def read(path):
    return json.loads(path.read_text())


def get_payload(packet):
    for message in packet['messages']:
        if message['type'] in ('human', 'user') and isinstance(message['data']['content'], str):
            try:
                body = json.loads(message['data']['content'])
            except ValueError:
                continue
            if isinstance(body, dict) and 'targets' in body:
                return body
    raise AssertionError('Reviewer packet did not contain the production review input')


def reply_full_review(packet):
    payload = get_payload(packet)
    rows = []
    for target in payload['targets']:
        row = {'claim_id': target['claim_id'], 'reason': 'Deterministic synthetic smoke control.'}
        if target['kind'] == 'limitation':
            row.update(status='conditional', scope_alignment='not_applicable',
                       premise_assessment='none', quotes=[])
        else:
            witnesses = [
                {'source_id': alias, 'excerpt_id': excerpt}
                for alias in target['evidence_ids']
                for excerpt, literal in payload['quote_index'][alias].items()
                if QUOTE in literal
            ]
            if not witnesses:
                raise AssertionError(f'Synthetic fact not in target-bound quote index: {target["claim_id"]}')
            row.update(status='supported', scope_alignment='aligned', quotes=witnesses[:1])
            if target.get('premise_check_required'):
                row['premise_assessment'] = 'supported'
        rows.append(row)
    result = {'verdicts': rows}
    if 'critic_findings' in payload:
        result['critic_assessments'] = []
    return result


class LocalReportSmoke(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix='aether-bridge-smoke-')
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        self.product = project_fixture(self.path/'synthetic-project')
        self.answered = set()

    def new_runtime(self, *, current, review_request_limit=4):
        # No environment credentials are loaded into this project fixture.
        options = {'enable_local_report_bridge': True} if current else {}
        runtime = AgentRuntime(root=self.product, state_dir=self.path/'runtime',
                               model_mode=BRIDGE_MODE, review_request_limit=review_request_limit, **options)
        self.assertFalse(runtime.settings.api_key)
        session = runtime.create_session(model_mode=BRIDGE_MODE,
                                         research_mode='current' if current else 'historical')
        sid = session['session_id']
        self.addCleanup(runtime.stop, sid)
        self.workspace = runtime.sessions_dir/sid/'workspace'
        return runtime, sid

    def pending(self, directory, expected_role):
        limit = time.monotonic() + 20
        while time.monotonic() < limit:
            for request in sorted((directory/'bridge').rglob('*.request.json')):
                if request in self.answered:
                    continue
                try:
                    packet = read(request)
                except (OSError, ValueError):
                    continue
                self.assertEqual(packet.get('role'), expected_role)
                self.assertEqual(packet['job_id'], directory.name)
                return request, packet
            status = read(directory/'status.json')
            if status['state'] in ('failed', 'cancelled', 'completed', 'interrupted'):
                self.fail(f'Job ended before the expected {expected_role!r} packet: {status.get("error")}')
            time.sleep(.03)
        self.fail(f'No pending packet for role {expected_role!r}')

    def respond(self, pair, *, content='', tool=None, arguments=None):
        path, packet = pair
        names = [item['function']['name'] for item in packet['tools']]
        calls = []
        if tool:
            self.assertIn(tool, names)
            calls.append({'name': tool, 'args': arguments if arguments is not None else {}})
        write_reply(path, {'content': content, 'tool_calls': calls})
        self.answered.add(path)

    def finish(self, directory, runtime, sid):
        limit = time.monotonic() + 12
        while time.monotonic() < limit:
            status = read(directory/'status.json')
            if status['state'] in ('failed', 'cancelled', 'completed', 'interrupted') and sid not in runtime.active:
                return status
            time.sleep(.03)
        self.fail('The job did not reach a terminal state')

    def test_legacy_historical_session_keeps_its_original_seam(self):
        with network_tripwire() as network:
            runtime, sid = self.new_runtime(current=False)
            job = runtime.start(sid, 'SYNTHETIC historical bridge smoke')
            directory = runtime.sessions_dir/sid/'jobs'/job['job_id']
            packet = self.pending(directory, None)
            self.assertEqual(packet[0].parent, directory/'bridge')
            self.assertEqual(packet[1]['model_mode'], BRIDGE_MODE)
            allowed = {item['function']['name'] for item in packet[1]['tools']}
            self.assertIn('read_file', allowed)
            self.assertNotIn('get_research_leads', allowed)
            self.assertNotIn('task', allowed)
            self.respond(packet, content='SYNTHETIC historical protocol complete.')
            status = self.finish(directory, runtime, sid)
            self.assertEqual(status['state'], 'completed', status.get('error'))
            self.assertEqual(network, [])

    def begin_current_report(self, *, review_request_limit=4):
        runtime, sid = self.new_runtime(current=True, review_request_limit=review_request_limit)
        runtime.knowledge.register(SOURCE)
        preference = 'Use short sections in synthetic ETH reports.'
        runtime.user_research_memory.record(
            'local-owner', sid, event_id='smoke-saved-preference', observed_at=START,
            slot='smoke-eth-format', topic='ETH report', kind='preference',
            operation='remember', text=preference, verbatim=preference, asset='ETH', task='report',
        )
        job = runtime.start(sid, REPORT_PROMPT, deliverable='report',
                            request_purpose='chat', jev_assistance=False)
        directory = runtime.sessions_dir/sid/'jobs'/job['job_id']
        self.assertEqual(job['periodic_scope'], {'assets': ['ETH'], 'start': START, 'end': END})

        def author(tool, args=None):
            self.respond(self.pending(directory, 'author'), tool=tool, arguments=args)

        def stages(active):
            return {'todos': [
                {'content': value, 'status': 'completed' if n < active else 'in_progress' if n == active else 'pending'}
                for n, value in enumerate(PERIOD_STAGES)
            ]}

        author('read_file', {'file_path': '/skills/research/SKILL.md'})
        author('get_research_leads')
        author('write_todos', stages(0))
        author('task', {'subagent_type': 'research-scout', 'description': 'Find saved public ETH originals.'})
        author('update_research_question', {'question_id': 'Q1',
               'question': 'What status does the saved Ethereum testing notice report?',
               'candidate_ids': [SOURCE_ID]})
        author('task', {'subagent_type': 'evidence-reader',
               'description': f'[Q1] Inspect the saved original {SOURCE_ID} for reported testing status.'})
        reader = self.pending(directory, 'reader')
        self.respond(reader, tool='read_evidence', arguments={'record_id': SOURCE_ID})
        self.respond(self.pending(directory, 'reader'), tool='ReaderResult',
                     arguments={'findings': [{'finding': QUOTE,
                                             'citations': [{'evidence_id': SOURCE_ID, 'quote': QUOTE}]}]})

        author('write_todos', stages(2))
        author('save_report_draft', {'draft': DRAFT})
        saved_request = self.pending(directory, 'author')
        messages = saved_request[1]['messages']
        draft = next(json.loads(m['data']['content']) for m in reversed(messages)
                     if m['type'] == 'tool' and m['data'].get('name') == 'save_report_draft')
        self.assertEqual(draft['status'], 'draft_saved', draft)
        self.respond(saved_request, tool='publish_saved_report', arguments={'draft_id': draft['draft_id']})
        return runtime, sid, directory

    def check_saved_report(self, directory, runtime, sid, delivery):
        status = self.finish(directory, runtime, sid)
        self.assertEqual(status['state'], 'completed', status.get('error'))
        self.assertEqual(status.get('report_delivery_kind'), delivery)
        report_path = self.workspace/status['published_reports'][0].lstrip('/')
        report = read(report_path.with_suffix('.json'))
        self.assertEqual(report['review']['status'], 'reviewed')
        self.assertEqual(report['review']['reviewer_model'], 'codex-conversation')
        self.assertEqual(report['validation']['status'], 'passed')
        self.assertEqual(read(report_path.with_suffix('.audit.json'))['verification']['status'], 'passed')
        review_id = report['review']['review_id']
        detailed_review = read(self.workspace/'reviews'/f'{review_id}.json')
        self.assertEqual(validate_saved_report_review(detailed_review, report['report'])['status'], 'reviewed')
        events = [json.loads(line) for line in (directory/'events.jsonl').read_text().splitlines()]

        self.assertTrue(any(event['kind'] == 'periodic_leads_read' and event.get('user_lead_count') == 1
                            for event in events))
        self.assertTrue(any(event['kind'] == 'reader_finished' and event.get('validated_finding_count') == 1
                            for event in events))
        self.assertTrue(any(event['kind'] == 'research_question_recorded' and event.get('question_id') == 'Q1'
                            for event in events))
        self.assertFalse(any(event['kind'] == 'news_rerank_judgment' for event in events))
        return events


    def test_opt_in_current_report_uses_native_reader_and_validated_reviewer(self):
        with network_tripwire() as network:
            runtime, sid, directory = self.begin_current_report()
            critic = self.pending(directory, 'reviewer')
            self.assertEqual(get_payload(critic[1])['stage'], 'scope_critic')
            self.assertEqual(list((self.workspace/'reports').glob('*.md')), [])
            self.respond(critic, content=json.dumps({'findings': []}))

            full = self.pending(directory, 'reviewer')
            self.assertEqual(get_payload(full[1])['stage'], 'full_review')
            self.respond(full, content=json.dumps({'verdicts': [], 'critic_assessments': []}))
            repair = self.pending(directory, 'reviewer')
            self.assertEqual(list((self.workspace/'reports').glob('*.md')), [])
            self.respond(repair, content=json.dumps(reply_full_review(repair[1])))

            events = self.check_saved_report(directory, runtime, sid, 'full')
            self.assertTrue(any(event['kind'] == 'review_invalid' for event in events))
            self.assertEqual(network, [])

    def test_reduced_report_fallback_has_its_own_local_reviewer_and_gate(self):
        with network_tripwire() as network:
            runtime, sid, directory = self.begin_current_report(review_request_limit=3)
            critic = self.pending(directory, 'reviewer')
            self.assertEqual(get_payload(critic[1])['stage'], 'scope_critic')
            self.respond(critic, content=json.dumps({'findings': []}))

            full = self.pending(directory, 'reviewer')
            payload = get_payload(full[1])
            self.assertEqual(payload['stage'], 'full_review')
            control = reply_full_review(full[1])
            summary_ids = {row['claim_id'] for row in payload['targets'] if row['kind'] == 'summary'}
            self.assertTrue(summary_ids)
            for verdict in control['verdicts']:
                if verdict['claim_id'] in summary_ids:
                    verdict.update(status='insufficient', scope_alignment='ambiguous', quotes=[],
                                   reason='Synthetic negative control for the summary only.',
                                   correction='Use only the supported observation in the reduced report.')
            self.respond(full, content=json.dumps(control))

            fallback = self.pending(directory, 'reviewer')
            self.assertEqual(list((self.workspace/'reports').glob('*.md')), [])
            self.assertNotIn('critic_findings', get_payload(fallback[1]))
            self.respond(fallback, content=json.dumps(reply_full_review(fallback[1])))

            events = self.check_saved_report(directory, runtime, sid, 'minimal')
            self.assertTrue(any(event['kind'] == 'minimal_delivery_started' for event in events))
            self.assertEqual(network, [])


if __name__ == '__main__':
    unittest.main()
