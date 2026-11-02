from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from research_agent.context_runtime import BoundedJudgmentSidecar, SummaryUsageRecorder, public_intent_descriptor
from research_agent.tools import ResearchTools
from research_agent.library_router import LibraryRouter, open_library
from research_agent.settings import Settings
from research_agent.terminal import ResearchShell


class RuntimeEvidenceTests(unittest.TestCase):
    def test_terminal_workspace_host_paths_and_virtual_paths_are_the_same_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);workspace=root/'workspace';workspace.mkdir()
            (workspace/'notes').mkdir()
            shell=ResearchShell(workspace,Settings())
            target=workspace/'notes/recipe.py'
            result=shell.write(str(target),'print("fixture")\n')
            self.assertFalse(result.error)
            self.assertTrue(target.is_file())
            self.assertEqual(shell._resolve_path('/notes/recipe.py'),target.resolve())
            self.assertEqual(shell._resolve_path(str(target)),target.resolve())
            outside=root/'outside';outside.mkdir()
            (workspace/'notes/link').symlink_to(outside,target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'escapes'):
                shell._resolve_path(str(workspace/'notes/link/secret'))
            self.assertNotEqual(shell._resolve_path(str(outside/'secret')),outside/'secret')

    def test_query_window_and_legacy_evidence_resolution_are_separate(self):
        def fake(name, ids):
            return SimpleNamespace(root=Path(name), contains=lambda rid: rid in ids,
                evidence=lambda rid: {'record_id':rid,'origin':name},
                exclusion_reasons=lambda rid: [], coverage=lambda: {'window':name})
        router=LibraryRouter(fake('halfyear',{'new'}),[fake('month',{'old'})])
        self.assertEqual(router.coverage(),{'window':'halfyear'})
        self.assertEqual(router.evidence('old')['origin'],'month')
        self.assertTrue(router.contains('new'))
        with self.assertRaises(KeyError):router.evidence('missing')

    def test_absent_default_and_explicit_bad_library_are_not_equivalent(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(open_library(directory))
            with self.assertRaises(ValueError):open_library(directory,'data/not-built')
            with self.assertRaises(ValueError):open_library(directory,'../outside')

    def test_sidecar_request_failure_or_bound_preserves_unknown(self):
        calls=[]
        def assess(task,**kwargs):
            calls.append(task)
            raise TimeoutError('fixture')
        sidecar=BoundedJudgmentSidecar(SimpleNamespace(assess=assess),limit=1)
        self.assertEqual(sidecar.assess('support',question='x',evidence=[])['status'],'error')
        blocked=sidecar.assess('support',question='x',evidence=[])
        self.assertEqual(blocked['status'],'unavailable')
        self.assertIsNone(blocked['label'])
        self.assertEqual(calls,['support'])

    def test_summary_usage_is_separate_and_truncation_is_rejected(self):
        events=[];callback=SummaryUsageRecorder(lambda kind,data:events.append((kind,data)))
        callback.on_chat_model_start({},[],run_id='fixture')
        result=LLMResult(generations=[[ChatGeneration(message=AIMessage(content='summary',
            response_metadata={'finish_reason':'length','model_name':'fixture'}))]])
        with self.assertRaisesRegex(RuntimeError,'summary was truncated'):
            callback.on_llm_end(result,run_id='fixture')
        self.assertEqual(events[-1][0],'context_summary_finished')
        self.assertIsNone(events[-1][1]['usage'])

    def test_retrieval_descriptor_never_forwards_personal_free_text(self):
        question='Our ETH cost basis is 3200 and risk tolerance is low; my 5 ETH wallet matters.'
        sent=public_intent_descriptor(question,[{'asset':'ETH'}])
        for private in ('3200','5 ETH','wallet','cost basis','risk tolerance'):
            self.assertNotIn(private,sent)
        for private in ('我有 5 个以太坊，这周是否该卖出？',
                        'I hold five ether and have a two-week horizon.',question):
            self.assertFalse(ResearchTools._public_semantic_question(private))
        self.assertTrue(ResearchTools._public_semantic_question('Hoodi upgrade postponement'))

    def test_permission_failure_disables_optional_sidecar_without_false_label(self):
        calls=[]
        def assess(task,**kwargs):
            calls.append(task);return {'status':'error','http_status':402,'label':None}
        sidecar=BoundedJudgmentSidecar(SimpleNamespace(assess=assess))
        self.assertEqual(sidecar.assess('support',evidence=[])['status'],'error')
        self.assertEqual(sidecar.assess('support',evidence=[])['status'],'unavailable')
        self.assertEqual(len(calls),1)

    def test_abstention_is_not_a_provider_failure(self):
        provider=SimpleNamespace(assess=lambda *args,**kwargs:{'status':'abstained','label':None})
        sidecar=BoundedJudgmentSidecar(provider,limit=5)
        for _ in range(4):self.assertEqual(sidecar.assess('relevance',evidence=[])['status'],'abstained')
        self.assertIsNone(sidecar.disabled_reason)

    def test_active_sdk_cannot_invent_frozen_candidate_documents(self):
        service=ResearchTools.__new__(ResearchTools)
        service.knowledge=object();service.mode='observed'
        for key in ('candidates','relations','dependencies'):
            with self.assertRaisesRegex(ValueError,'frozen candidate'):
                service.news_pack(**{key:[{'record_id':'page-fake','text':'invented'}]})

    def test_evidence_feature_settings_validate_modes_without_exposing_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'.env').write_text('ARK_API_KEY=fixture-private\nAETHER_JUDGMENT_MODE=shadow\n')
            settings=Settings.load(root)
            self.assertTrue(settings.memory_enabled)
            self.assertEqual(settings.public()['judgment_mode'],'shadow')
            self.assertNotIn('fixture-private',str(settings.public()))
            (root/'.env').write_text('AETHER_JUDGMENT_MODE=approve-everything\n')
            with self.assertRaises(ValueError):Settings.load(root)
