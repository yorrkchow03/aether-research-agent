"""Dataset freeze, source-span/provenance and split isolation contracts."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json,unittest

from research_agent.semantic_benchmark_dataset import (
    FAMILIES,case_payload,controlled,freeze_dataset,load_dataset,saved_excerpt,
    validate_dataset,
)


class SemanticDatasetTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.original={'record_id':'page-'+'a'*24,'title':'Ethereum proposal','text':'Context. Ethereum developers propose a new test. No execution is reported.',
            'published_at':'2026-09-01T00:00:00Z','retrieved_at':'2026-10-27T00:00:00Z',
            'asset':'ETH','assets':['ETH'],'source_kind':'official_release',
            'source':{'name':'Publisher','url':'https://example.invalid/source','sha256':'a'*64}}
        self.originals={self.original['record_id']:deepcopy(self.original)}
        self.sources={'s':saved_excerpt(self.original)}
        self.case={'case_id':'c1','task':'classification','question':'Classify this proposed development.',
            'source_keys':['s'],'split':'development','event_family':'example_event',
            'language':'English','evidence_language':'English','controlled':False,
            'expected':{'asset':['ETH'],'topic':['network_upgrade'],'event_status':['planned']},
            'label_rationale':{'asset':'Ethereum named.','topic':'Protocol development.','event_status':'Only proposed.'}}

    def test_round_trip_hashes_and_provider_input_excludes_gold_tags(self):
        output=self.root/'dataset'
        manifest=freeze_dataset(output,sources=self.sources,originals=self.originals,cases=[self.case])
        loaded=load_dataset(output);self.assertEqual(loaded['manifest'],manifest)
        payload=case_payload(loaded['cases'][0],loaded['sources'])
        self.assertEqual(set(payload),{'task','question','evidence'})
        self.assertNotIn('asset',payload['evidence'][0]);self.assertNotIn('assets',payload['evidence'][0])
        reviewer=json.loads((output/'reviewer_input.json').read_text())
        self.assertEqual(set(reviewer['cases'][0]),{'case_id','task','question','evidence'})
        self.assertNotIn('expected',reviewer['cases'][0]);self.assertNotIn('split',reviewer['cases'][0])

    def test_changed_frozen_payload_and_overwrite_are_rejected(self):
        output=self.root/'dataset';freeze_dataset(output,sources=self.sources,originals=self.originals,cases=[self.case])
        with self.assertRaises(ValueError):freeze_dataset(output,sources=self.sources,originals=self.originals,cases=[self.case])
        (output/'cases.json').write_text('[]')
        with self.assertRaises(ValueError):load_dataset(output)

    def test_span_hash_and_full_original_binding_are_checked(self):
        damaged=deepcopy(self.sources);damaged['s']['source_spans'][0]['sha256']='bad'
        with self.assertRaises(ValueError):validate_dataset(damaged,self.originals,[self.case])
        damaged=deepcopy(self.originals);damaged[self.original['record_id']]['text']='Changed original'
        with self.assertRaises(ValueError):validate_dataset(self.sources,damaged,[self.case])

    def test_adapter_truncation_never_silently_changes_judged_evidence(self):
        damaged=deepcopy(self.sources);damaged['s']['source_record']['text']='x'*12001
        with self.assertRaises(ValueError):validate_dataset(damaged,self.originals,[self.case])

    def test_source_or_family_overlap_between_splits_is_rejected(self):
        other=deepcopy(self.case);other.update(case_id='c2',split='confirmation',event_family='different_name')
        with self.assertRaises(ValueError):validate_dataset(self.sources,self.originals,[self.case,other])
        different=deepcopy(self.original);different['record_id']='page-'+'b'*24
        originals={**self.originals,different['record_id']:different};sources={**self.sources,'t':saved_excerpt(different)}
        other.update(source_keys=['t'],event_family=self.case['event_family'])
        with self.assertRaises(ValueError):validate_dataset(sources,originals,[self.case,other])

    def test_each_question_dimension_has_predeclared_valid_options(self):
        damaged=deepcopy(self.case);damaged['expected'].pop('event_status')
        with self.assertRaises(ValueError):validate_dataset(self.sources,self.originals,[damaged])
        damaged=deepcopy(self.case);damaged['expected']['topic']=['not_a_category']
        with self.assertRaises(ValueError):validate_dataset(self.sources,self.originals,[damaged])

    def test_controlled_statement_cannot_impersonate_publisher_capture(self):
        fixture=controlled(self.original,text='A controlled contrary statement.',tag='Synthetic counterexample')
        self.assertTrue(fixture['source_record']['record_id'].startswith('fixture-'))
        self.assertIsNone(fixture['source_record']['published_at'])
        sources={**self.sources,'t':fixture}
        case=deepcopy(self.case);case.update(task='relation',source_keys=['s','t'],controlled=True,expected={'relation':['contradicts']})
        self.assertTrue(validate_dataset(sources,self.originals,[case]))
        damaged=deepcopy(sources);damaged['t']['source_record']['published_at']='2026-09-01T00:00:00Z'
        with self.assertRaises(ValueError):validate_dataset(damaged,self.originals,[case])

    def test_relation_order_and_distinct_source_identity_are_required(self):
        case=deepcopy(self.case);case.update(task='relation',source_keys=['s'],expected={'relation':['insufficient']})
        with self.assertRaises(ValueError):validate_dataset(self.sources,self.originals,[case])
        case['source_keys']=['s','s']
        with self.assertRaises(ValueError):validate_dataset(self.sources,self.originals,[case])

    def test_real_design_uses_distinct_event_families_and_earlier_development_period(self):
        self.assertEqual(len(FAMILIES),12);self.assertEqual(len({f['id'] for f in FAMILIES}),12)
        self.assertEqual(sum(f['split']=='development' for f in FAMILIES),4)
        self.assertFalse(any('zkapi' in f['id'] or 'hoodi_upgrade' in f['id'] for f in FAMILIES))


if __name__=='__main__':unittest.main()
