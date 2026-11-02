"""Offline factory, scope, accounting and publication boundaries for reports.

No provider client is invoked. Real sandbox execution belongs to the explicit
host smoke check, since nested macOS sandbox creation may be denied in CI.
"""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx

from research_agent.report_study_runtime import (
    FrozenResearchShell, FrozenResearchTools, HttpMeter, ReportStudyRuntime, StudyReviewer,
)
from research_agent.runtime import AgentRuntime
from research_agent.settings import Settings
from research_agent.tools import ResearchTools
from research_agent.terminal import ResearchShell
from research_agent.reports import ReportDraft
from research_agent.knowledge import KnowledgeStore


class ReportStudyRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.run = self.root/'run'; self.run.mkdir()
        (self.run/'manifest.json').write_text(json.dumps({'datasets': [], 'sources': []}))
        self.workspace = self.root/'workspace'; self.workspace.mkdir()
        self.source_id = 'page-'+'a'*24
        self.row = {'record_id': self.source_id, 'text': 'An upgrade remains proposed.',
            'source': {'url': 'https://example.invalid/proposal', 'sha256': 'a'*64}}

    def service(self, **options):
        return FrozenResearchTools(self.run, self.workspace, '2026-10-28T00:00:00Z',
            allow_live=True, source_ids=[self.source_id], **options)

    def draft(self):
        return ReportDraft(title='Upgrade state', summary='The upgrade remains a proposal.',
            observations=[{'statement': 'A proposal exists.', 'evidence_ids': [self.source_id]}],
            limitations=['A proposal does not establish execution.'])

    def test_lookup_and_calculation_lineage_keep_declared_source_scope(self):
        service = self.service()
        with patch.object(ResearchTools, 'lookup', return_value=self.row):
            self.assertEqual(service.lookup(self.source_id), self.row)
            with self.assertRaises(ValueError):service.lookup('page-'+'b'*24)
        calculation = {'record_id': 'calc-'+'c'*24, 'input_record_ids': ['page-'+'b'*24]}
        with patch.object(ResearchTools, 'lookup', return_value=calculation):
            with self.assertRaises(ValueError):service.lookup(calculation['record_id'])

    def test_registered_live_calculation_is_usable_with_its_frozen_source(self):
        knowledge=KnowledgeStore(self.root/'knowledge')
        source={**deepcopy(self.row),'retrieved_at':'2026-10-27T00:00:00Z',
            'values':{'count':'2'},'units':{'count':'count'}}
        knowledge.register(source)
        shell=SimpleNamespace(replay_recipe=Mock(return_value=SimpleNamespace(
            exit_code=0,truncated=False,output='{"values":{"double":4},"units":{"double":"count"}}')))
        service=self.service(knowledge=knowledge,shell=shell)
        (self.workspace/'notes').mkdir(exist_ok=True)
        recipe=self.workspace/'notes/count.py'
        recipe.write_text('import json,sys\nr=json.load(open(sys.argv[1]))\nprint(json.dumps({"values":{"double":2*int(r["values"]["count"])},"units":{"double":"count"}}))\n')
        registered=service.register_calculation('/notes/count.py',[self.source_id],'Double the declared fixture count.')
        self.assertTrue(registered['record_id'].startswith('live-'))
        row=service.lookup(registered['record_id'])
        self.assertEqual(row['dataset_id'],'live.calculation')
        self.assertEqual(row['values']['double'],'4')
        self.assertEqual(row['input_record_ids'],[self.source_id])
        self.assertTrue(row['recipe_sha256'])
        self.assertEqual(Path(row['recipe_path']).read_text(),recipe.read_text())
        shell.replay_recipe.assert_called_once()

    def test_derived_live_calculation_cannot_smuggle_an_unlisted_leaf_source(self):
        outside='page-'+'b'*24; derived='live-'+'c'*24
        records={outside:dict(self.row,record_id=outside),derived:{'record_id':derived,
            'dataset_id':'live.calculation','recipe_sha256':'c'*64,
            'input_record_ids':[outside]}}
        knowledge=SimpleNamespace(contains=lambda rid:rid in records,
            get=lambda rid:deepcopy(records[rid]))
        service=self.service();service.knowledge=knowledge
        with self.assertRaisesRegex(ValueError,'outside'):
            service.lookup(derived)

    def test_derived_live_calculation_cycle_fails_with_explicit_lineage_error(self):
        first,second='live-'+'c'*24,'live-'+'d'*24
        records={first:{'record_id':first,'dataset_id':'live.calculation','recipe_sha256':'c'*64,'input_record_ids':[second]},
            second:{'record_id':second,'dataset_id':'live.calculation','recipe_sha256':'d'*64,'input_record_ids':[first]}}
        knowledge=SimpleNamespace(contains=lambda rid:rid in records,
            get=lambda rid:deepcopy(records[rid]))
        service=self.service();service.knowledge=knowledge
        # Exercise the actual parent lookup, whose recursive dispatch used to
        # reset the frozen lineage path before the explicit cycle check.
        with self.assertRaisesRegex(ValueError,'cycle'):
            service.lookup(first)

    def test_live_prefix_and_claimed_metadata_alone_do_not_make_a_derived_record(self):
        for changes in ({'dataset_id':'live.external'}, {'recipe_sha256':''}, {'input_record_ids':[]}):
            with self.subTest(changes=changes):
                rid='live-'+'e'*24
                record={'record_id':rid,'dataset_id':'live.calculation','recipe_sha256':'e'*64,
                    'input_record_ids':[self.source_id],**changes}
                knowledge=SimpleNamespace(contains=lambda query:query==rid,get=lambda query:record)
                service=self.service();service.knowledge=knowledge
                with self.assertRaisesRegex(ValueError,'outside'):service.lookup(rid)

    def test_all_external_capture_and_series_sdk_operations_are_blocked(self):
        service = self.service()
        for name in ('online_search', 'read_page', 'public_api', 'fetch_live', 'chain', 'market', 'indicators', 'events'):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):getattr(service, name)()
        for operation in ('fetch', 'api', 'search', 'page', 'market', 'library-series',
                'archive-chain', 'portfolio', 'publish_report'):
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):service.sdk_call(operation, {})
        self.assertEqual(service.sdk_call('sources', {})['source_ids'], [self.source_id])
        self.assertNotIn(str(self.root), json.dumps(service.catalog()))

    def test_active_tool_catalog_uses_the_frozen_source_contract(self):
        service = self.service(live_data=SimpleNamespace(root=self.root/'live'), web_search=SimpleNamespace())
        tools = {item.name: item for item in service.as_tools()}
        result = json.loads(tools['list_data_sources'].invoke({}))
        self.assertEqual(result['source_ids'], [self.source_id])
        for name in ('request_public_api', 'search_web', 'fetch_webpage'):
            if name in tools:
                # The schema may remain for harness compatibility, but the
                # actual implementation must reject before external capture.
                with self.assertRaises(ValueError):
                    if name == 'request_public_api':tools[name].invoke({'provider': 'coinbase', 'path': '/products/ETH-USD/stats'})
                    elif name == 'search_web':tools[name].invoke({'query': 'upgrade'})
                    else:tools[name].invoke({'url': 'https://example.invalid'})

    def test_memory_sdk_cannot_return_an_unlisted_source(self):
        knowledge = SimpleNamespace(search_research=Mock(return_value=[{
            'report_id': 'unrelated-report', 'summary': 'Private prior result',
            'evidence_ids': ['page-'+'b'*24]}]))
        service = self.service(knowledge=None)
        service.knowledge = knowledge
        unknown = {'results': [dict(self.row, record_id='page-'+'b'*24)], 'source_ids': ['page-'+'b'*24]}
        for operation in ('memory-news', 'memory-research'):
            with self.subTest(operation=operation), patch.object(ResearchTools, 'news_pack', return_value=deepcopy(unknown)):
                try:result = service.sdk_call(operation, {'query': 'upgrade'})
                except ValueError:continue
                self.assertNotIn('page-'+'b'*24, json.dumps(result))
                self.assertNotIn('Private prior result', json.dumps(result))

    def test_frozen_evidence_sdk_reads_saved_source_without_a_live_library(self):
        service = self.service()
        with patch.object(service, 'evidence', return_value={'record': self.row}) as evidence:
            result=service.sdk_call('library-evidence', {'record_id': self.source_id})
        evidence.assert_called_once_with(record_id=self.source_id)
        self.assertEqual(result['record']['record_id'],self.source_id)

    def test_support_runs_before_required_review_and_unknown_does_not_block(self):
        service = self.service(judgment_mode='assist'); order = []
        def support(_):
            order.append('optional_support')
            return {'results': [{'status': 'unavailable', 'label': None}]}
        def mandatory(_, draft):
            order.append('mandatory_source_and_numeric_review')
            return {'status': 'saved'}
        with (patch('research_agent.report_study_runtime.validate_report', side_effect=lambda *a, **k: order.append('source_identity_validation')),
                patch.object(service, 'semantic_support', side_effect=support),
                patch.object(ResearchTools, 'publish', autospec=True, side_effect=mandatory)):
            result = service.publish(self.draft())
        self.assertEqual(result['status'], 'saved')
        self.assertEqual(order, ['source_identity_validation', 'optional_support', 'mandatory_source_and_numeric_review'])

    def test_known_unsupported_claim_requests_revision_before_review(self):
        for label in ('insufficient', 'contradicted'):
            with self.subTest(label=label):
                service = self.service(judgment_mode='assist')
                with (patch('research_agent.report_study_runtime.validate_report'),
                        patch.object(service, 'semantic_support', return_value={'results': [{'status': 'ok', 'label': label}]}),
                        patch.object(ResearchTools, 'publish') as publish):
                    with self.assertRaises(ValueError):service.publish(self.draft())
                    publish.assert_not_called()

    def test_shadow_does_not_add_a_prepublication_gate(self):
        service = self.service(judgment_mode='shadow')
        with (patch('research_agent.report_study_runtime.validate_report'),
                patch.object(service, 'semantic_support') as support,
                patch.object(ResearchTools, 'publish', return_value={'status': 'saved'})):
            self.assertEqual(service.publish(self.draft())['status'], 'saved')
            support.assert_not_called()

    def fake_model(self):
        copied = SimpleNamespace(chat=SimpleNamespace(completions=object()))
        root = SimpleNamespace(with_options=Mock(return_value=copied))
        return SimpleNamespace(streaming=True, root_client=root, client=None,
            http_client=SimpleNamespace(event_hooks={'request': [], 'response': []}))

    def test_meter_captures_only_safe_http_metadata_and_disables_retries(self):
        meter = HttpMeter(self.root/'meter.json'); model = self.fake_model(); root_client = model.root_client
        self.assertIs(meter.attach(model, 'author'), model)
        req = httpx.Request('POST', 'https://example.invalid/api/plan/v3/chat/completions?api_key=SYNTHETIC_QUERY_SECRET',
            headers={'Authorization': 'Bearer SYNTHETIC_HEADER_SECRET'}, content=b'SYNTHETIC_BODY_SECRET')
        model.http_client.event_hooks['request'][0](req)
        model.http_client.event_hooks['response'][0](httpx.Response(200, request=req))
        saved = (self.root/'meter.json').read_text()
        for secret in ('SYNTHETIC_QUERY_SECRET', 'SYNTHETIC_HEADER_SECRET', 'SYNTHETIC_BODY_SECRET'):
            self.assertNotIn(secret, saved)
        self.assertEqual(meter.rows[0]['http_status'], 200)
        self.assertEqual(meter.rows[0]['stage'], 'author')
        root_client.with_options.assert_called_once_with(timeout=60, max_retries=0)
        self.assertFalse(model.streaming)

    def test_meter_never_persists_a_credential_embedded_in_an_unexpected_path(self):
        meter = HttpMeter(self.root/'meter.json'); model = self.fake_model()
        meter.attach(model, 'author')
        req = httpx.Request('POST', 'https://example.invalid/SYNTHETIC_PATH_SECRET/messages')
        model.http_client.event_hooks['request'][0](req)
        self.assertNotIn('SYNTHETIC_PATH_SECRET', (self.root/'meter.json').read_text())

    def test_default_runtime_factory_hooks_keep_the_original_implementations(self):
        runtime = AgentRuntime.__new__(AgentRuntime); runtime.settings = Settings(); runtime.root = self.root; runtime.state_dir = self.root/'state'
        with patch('research_agent.runtime.create_ark_model', return_value='author') as create:
            self.assertEqual(runtime.build_author_model(runtime.settings, 'report'), 'author')
            create.assert_called_once_with(runtime.settings, max_output_tokens=8192)
        with patch('research_agent.runtime.ResearchShell', return_value='shell') as shell:
            self.assertEqual(runtime.build_shell(self.workspace, None), 'shell')
            shell.assert_called_once_with(self.workspace, runtime.settings, None)
        with patch('research_agent.runtime.ResearchTools', return_value='tools') as tools:
            self.assertEqual(runtime.build_research_tools(1, flag=True), 'tools')
            tools.assert_called_once_with(1, flag=True)
        with patch('research_agent.evidence_judgments.EvidenceJudgmentService', return_value='judge') as judge:
            self.assertEqual(runtime.build_judgment_service(), 'judge')
            self.assertEqual(judge.call_args.kwargs['max_attempts'], 1)

    def test_study_factory_configuration_is_bounded_without_reading_env(self):
        def initial(runtime, root, state_dir=None, review_request_limit=None):
            runtime.root=Path(root); runtime.state_dir=Path(state_dir); runtime.settings=Settings()
            runtime.software_dir=self.root/'frozen-software'
            runtime.knowledge=SimpleNamespace(library='would-be-live-library');runtime.library='would-be-live-library'
        with patch.object(AgentRuntime, '__init__', initial):
            for arm, provider, mode in (('original_off', 'jev', 'off'), ('pro_checks', 'doubao', 'assist'), ('jev_routed', 'jev', 'assist')):
                with self.subTest(arm=arm):
                    runtime=ReportStudyRuntime(self.root,self.root/'state',arm=arm,source_ids=[self.source_id])
                    self.assertEqual(runtime.settings.max_model_calls, 12)
                    self.assertEqual(runtime.settings.judgment_provider, provider)
                    self.assertEqual(runtime.settings.judgment_mode, mode)
                    self.assertIsNone(runtime.library); self.assertIsNone(runtime.knowledge.library)
                    self.assertFalse(runtime.settings.search_enabled)
        with self.assertRaises(ValueError):ReportStudyRuntime(self.root,self.root/'state',arm='unknown',source_ids=[])

    def test_study_reviewer_keeps_mandatory_review_request_budget(self):
        original=SimpleNamespace(settings=Settings(),emit=None,user_context_id='context-current',
            diagnostic_dir=None,cancel=None,research_cutoff='2026-10-28T00:00:00Z',max_requests=2)
        meter=SimpleNamespace(attach=Mock(return_value='metered-model'))
        reviewer=StudyReviewer(original,meter)
        with patch('research_agent.review.EvidenceReviewer.build_model', return_value='model'):
            self.assertEqual(reviewer.build_model(),'metered-model')
        meter.attach.assert_called_once_with('model','mandatory_source_review')
        self.assertEqual(reviewer.max_requests,2)

    def test_missing_macos_sandbox_has_no_unrestricted_fallback(self):
        original=Path.is_file
        with patch.object(Path,'is_file',lambda p: False if str(p)=='/usr/bin/sandbox-exec' else original(p)):
            with self.assertRaises(ValueError):FrozenResearchShell(self.workspace,Settings(),project_root=self.root)

    def test_frozen_shell_wraps_commands_in_prepared_sandbox_profile(self):
        original=Path.is_file
        with patch.object(Path,'is_file',lambda p: True if str(p)=='/usr/bin/sandbox-exec' else original(p)):
            shell=FrozenResearchShell(self.workspace,Settings(),project_root=self.root)
        with patch.object(ResearchShell,'execute',return_value='wrapped') as execute:
            self.assertEqual(shell.execute('python -c "print(2)"',timeout=5),'wrapped')
        command=execute.call_args.args[0]
        self.assertIn('/usr/bin/sandbox-exec -p ',command)
        self.assertNotIn(' -f ',command)
        self.assertEqual(execute.call_args.kwargs['timeout'],5)
        profile=shell.profile.read_text()
        self.assertIn('(deny network*)',profile)
        self.assertIn(str(self.root/'.env'),profile)
        self.assertEqual(shell._env['PYTHONDONTWRITEBYTECODE'],'1')
        self.assertNotIn('remote unix-socket',profile)
        target=self.root/'job-owned.sock';shell.attach_research_sdk(target)
        attached=shell.profile.read_text()
        self.assertIn('(path-literal '+json.dumps(str(target.resolve()))+')',attached)
        self.assertNotIn('(allow network-outbound (remote unix-socket))',attached)
        shell.profile.write_text('(version 1)\n(allow default)\n')
        with patch.object(ResearchShell,'execute',return_value='still-guarded') as guarded:
            self.assertEqual(shell.execute('python -c "print(3)"'),'still-guarded')
        actual=guarded.call_args.args[0]
        self.assertIn('(deny network*)',actual)
        self.assertIn('(path-literal '+json.dumps(str(target.resolve()))+')',actual)


if __name__ == '__main__':unittest.main()
