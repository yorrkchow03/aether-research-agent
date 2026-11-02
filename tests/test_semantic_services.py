"""Provider construction only; all constructors mocked, no config or API I/O."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from research_agent.semantic_services import build_judgment_service


class SemanticServiceFactoryTests(unittest.TestCase):
    def setUp(self):
        self.root=Path('/synthetic/project');self.state=self.root/'runtime'

    def test_default_preserves_legacy_constructor_and_object(self):
        service=Mock()
        with (patch('research_agent.semantic_services.EvidenceJudgmentService',return_value=service) as factory,
              patch('research_agent.semantic_services.SelectiveJudgmentService') as selective):
            result=build_judgment_service(self.root,self.state)
        self.assertIs(result,service)
        factory.assert_called_once_with(self.root,self.state/'evidence-judgments',provider='jev',timeout_seconds=12,max_attempts=1)
        selective.assert_not_called();service.assess.assert_not_called()

    def test_selective_does_not_change_non_jev_provider(self):
        for provider in ('doubao','lexical'):
            with (self.subTest(provider=provider),patch('research_agent.semantic_services.EvidenceJudgmentService') as factory,
                  patch('research_agent.semantic_services.SelectiveJudgmentService') as selective):
                result=build_judgment_service(self.root,self.state,provider=provider,policy='selective')
                self.assertIs(result,factory.return_value)
                factory.assert_called_once_with(self.root,self.state/'evidence-judgments',provider=provider,timeout_seconds=12,max_attempts=1)
                selective.assert_not_called()

    def test_selective_pins_providers_and_injects_only_classification_shared_stores(self):
        instances=[]
        def provider(root,path,**options):
            item=SimpleNamespace(root=root,path=path,options=options,assess=Mock());instances.append(item);return item
        with (patch('research_agent.semantic_services.EvidenceJudgmentService',side_effect=provider) as factory,
              patch('research_agent.semantic_services.SelectiveJudgmentService') as router):
            result=build_judgment_service(self.root,self.state,policy='selective')
        self.assertIs(result,router.return_value);self.assertEqual(factory.call_count,4)
        self.assertEqual([r.options['provider'] for r in instances],['jev','doubao','jev','doubao'])
        self.assertEqual([r.options['requested_model'] for r in instances],['jev-1.14.0','doubao-seed-2.1-pro']*2)
        for item in instances:
            self.assertEqual(item.options['timeout_seconds'],30);self.assertEqual(item.options['max_attempts'],1)
            self.assertEqual(item.options['minimum_confidence'],.8);item.assess.assert_not_called()
        self.assertEqual(instances[0].path.parent,instances[1].path.parent)
        self.assertEqual(instances[2].path,self.state/'evidence-judgments-selective/classification-jev')
        self.assertEqual(instances[3].path,self.state/'evidence-judgments-selective/classification-pro')
        router.assert_called_once_with(instances[0],instances[1],classification_primary=instances[2],classification_pro=instances[3],
            primary_limit=12,pro_limit=16,minimum_confidence=.8)

    def test_every_job_gets_new_dynamic_store_but_same_classification_stores(self):
        with (patch('research_agent.semantic_services.EvidenceJudgmentService') as factory,
              patch('research_agent.semantic_services.SelectiveJudgmentService')):
            build_judgment_service(self.root,self.state,policy='selective')
            build_judgment_service(self.root,self.state,policy='selective')
        calls=factory.call_args_list
        self.assertNotEqual(calls[0].args[1],calls[4].args[1]);self.assertNotEqual(calls[1].args[1],calls[5].args[1])
        self.assertEqual(calls[2].args[1],calls[6].args[1]);self.assertEqual(calls[3].args[1],calls[7].args[1])
        self.assertEqual(len(calls[0].args[1].parent.name),32)

    def test_unknown_policy_rejected_before_any_provider_construction(self):
        with (patch('research_agent.semantic_services.EvidenceJudgmentService') as factory,
              patch('research_agent.semantic_services.SelectiveJudgmentService') as router):
            with self.assertRaises(ValueError):build_judgment_service(self.root,self.state,policy='typo')
        factory.assert_not_called();router.assert_not_called()

    def test_string_paths_work_without_reading_env_or_touching_real_state(self):
        with patch('research_agent.semantic_services.EvidenceJudgmentService') as factory:
            build_judgment_service(str(self.root),str(self.state),provider='doubao')
        self.assertEqual(factory.call_args.args,(self.root,self.state/'evidence-judgments'))


if __name__=='__main__':unittest.main()
