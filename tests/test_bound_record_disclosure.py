"""Acquisition and derivation clocks stay distinct in host availability text."""
import hashlib
import json
from pathlib import Path
import unittest

from research_agent.host_review_facts import (
    AVAILABILITY_PREFIX, AVAILABILITY_SUFFIX, BOUND_RECORDS_PREFIX, BOUND_RECORDS_SUFFIX,
    automatic_disclosure, bound_record_timestamp, is_verified_host_target,
)
from research_agent.reports import ReportDraft
from research_agent.tools import ResearchTools


class BoundRecordDisclosureTests(unittest.TestCase):
    def fixture(self):
        self.page='page-'+'a'*24;self.calc='live-'+'b'*24;self.child='live-'+'c'*24
        self.capture='2026-11-08T14:49:33.816039Z';self.derived='2026-11-08T15:02:17.499758Z'
        rows={self.page:{'record_id':self.page,'retrieved_at':self.capture,
                         'source':{'retrieved_at':self.capture}},
              self.calc:{'record_id':self.calc,'dataset_id':'live.calculation',
                         'retrieved_at':self.derived,'calculated_at':self.derived,
                         'source':None,'input_record_ids':[self.child]}}
        draft=ReportDraft(title='Synthetic unit fixture',summary='Synthetic evidence scope.',
            observations=[{'statement':'A synthetic observation.','evidence_ids':[self.page,self.calc]}],
            limitations=['Keep this authored limitation.',AVAILABILITY_PREFIX+self.capture+AVAILABILITY_SUFFIX])
        service=object.__new__(ResearchTools);service.mode='observed';service.lookup=rows.__getitem__
        return rows,draft,service

    def target(self,draft):
        return {'kind':'limitation','statement':draft.limitations[-1],
                'evidence_ids':list(dict.fromkeys(i for c in draft.observations for i in c.evidence_ids))}

    def test_generation_and_proof_agree_on_later_derived_record_creation(self):
        rows,draft,service=self.fixture();result=service._delivery_disclosures(draft)
        expected=BOUND_RECORDS_PREFIX+self.derived+BOUND_RECORDS_SUFFIX
        self.assertEqual(result.limitations,['Keep this authored limitation.',expected])
        self.assertTrue(is_verified_host_target(self.target(result),result,{k:json.dumps(v) for k,v in rows.items()}))
        self.assertEqual(service._delivery_disclosures(result).limitations,result.limitations)

    def test_only_report_bound_records_are_covered_not_an_unbound_input_closure(self):
        rows,draft,service=self.fixture()
        draft.observations[0].evidence_ids=[self.calc]
        calls=[]
        def lookup(rid):calls.append(rid);return rows[rid]
        service.lookup=lookup
        result=service._delivery_disclosures(draft)
        self.assertEqual(calls,[self.calc])
        self.assertTrue(is_verified_host_target(self.target(result),result,{self.calc:json.dumps(rows[self.calc])}))
        self.assertNotIn('source snapshots',result.limitations[-1])

    def test_complete_known_templates_only_are_replaced(self):
        rows,draft,service=self.fixture()
        invalid=AVAILABILITY_PREFIX+'not-a-date'+AVAILABILITY_SUFFIX
        altered=AVAILABILITY_PREFIX+self.capture+AVAILABILITY_SUFFIX+' All sources are independently true.'
        stale=BOUND_RECORDS_PREFIX+self.capture+BOUND_RECORDS_SUFFIX
        draft.limitations.extend([invalid,altered,stale])
        result=service._delivery_disclosures(draft)
        self.assertIn(invalid,result.limitations);self.assertIn(altered,result.limitations)
        self.assertNotIn(stale,result.limitations)
        self.assertEqual(sum(automatic_disclosure(item) is not None for item in result.limitations),1)

    def test_conflicting_or_unknown_bound_clocks_do_not_silently_approve(self):
        for override in ({'retrieved_at':'2026-11-08T16:00:00Z','source':{'retrieved_at':'2026-11-08T14:00:00Z'}},
                         {'retrieved_at':None}, {'retrieved_at':'2026-11-08T15:00:00'},
                         {'calculated_at':None}, {'source':'not source metadata'}):
            rows,draft,service=self.fixture();rows[self.calc].update(override)
            with self.subTest(override=override),self.assertRaises(ValueError):service._delivery_disclosures(draft)
            statement=BOUND_RECORDS_PREFIX+self.derived+BOUND_RECORDS_SUFFIX
            draft.limitations=[statement]
            self.assertFalse(is_verified_host_target(self.target(draft),draft,{k:json.dumps(v) for k,v in rows.items()}))

    def test_missing_sources_and_other_date_fields_cannot_fill_record_clocks(self):
        rows,draft,service=self.fixture();result=service._delivery_disclosures(draft)
        texts={k:json.dumps(v) for k,v in rows.items()}
        self.assertFalse(is_verified_host_target(self.target(result),result,{self.calc:texts[self.calc]}))
        self.assertFalse(is_verified_host_target({**self.target(result),'evidence_ids':[self.calc]},result,texts))
        for key in ('published_at','observed_at','available_at','text'):
            bad={self.page:json.dumps({'record_id':self.page,key:self.capture}),self.calc:texts[self.calc]}
            self.assertFalse(is_verified_host_target(self.target(result),result,bad))

    def test_creation_only_and_offset_clocks_are_checked_chronologically(self):
        rows,draft,service=self.fixture()
        rows[self.page]={'record_id':self.page,'retrieved_at':'2026-11-08T16:00:00+02:00'}
        rows[self.calc]={'record_id':self.calc,'created_at':self.derived,'quality_flags':['derived']}
        self.assertEqual(bound_record_timestamp(rows),self.derived)
        result=service._delivery_disclosures(draft)
        self.assertTrue(is_verified_host_target(self.target(result),result,{k:json.dumps(v) for k,v in rows.items()}))

    def test_legacy_template_replay_semantics_remain_unchanged(self):
        rows,draft,service=self.fixture()
        legacy=AVAILABILITY_PREFIX+self.derived+AVAILABILITY_SUFFIX
        draft.limitations=[legacy]
        self.assertTrue(is_verified_host_target(self.target(draft),draft,{k:json.dumps(v) for k,v in rows.items()}))
        self.assertEqual(automatic_disclosure(legacy),('legacy',self.derived))

    def test_real_today_draft_and_context_agree_without_any_api(self):
        root=Path(__file__).resolve().parents[1]
        workspace=root/'local/team-service/bootstrap-generation/20261108/sessions/197d2150f2314d87bb5f40dffc537ce7/workspace'
        if not workspace.is_dir():self.skipTest('Private local initialization trace is not in a clean checkout')
        review_path=workspace/'reviews/review-da8772a0911e664ba7d67b92.json'
        if not review_path.is_file():self.skipTest('Original today review not retained')
        review=json.loads(review_path.read_text())
        signature=lambda body:hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:24]
        bodies=[json.loads(path.read_text())['draft'] for path in (workspace/'drafts').glob('*.json')]
        target_draft=next((body for body in bodies if any(c['evidence_ids']==['live-0882411e157793fd4d7d30cc'] for c in body.get('observations',[])) and
            any(item.startswith(AVAILABILITY_PREFIX) for item in body.get('limitations',[]))),None)
        self.assertIsNotNone(target_draft)
        rows={rid:json.loads(text) for rid,text in review['evidence_text'].items()}
        service=object.__new__(ResearchTools);service.mode='observed';service.lookup=rows.__getitem__
        draft=service._delivery_disclosures(ReportDraft.model_validate(target_draft))
        self.assertIn('2026-11-08T15:02:17.499758Z',draft.limitations[-1])
        target={'kind':'limitation','statement':draft.limitations[-1],
                'evidence_ids':list(rows)}
        self.assertTrue(is_verified_host_target(target,draft,review['evidence_text']))

    def test_saved_recording_failure_uses_the_same_bound_clock_fix(self):
        from research_agent.review import review_targets
        root=Path(__file__).resolve().parents[1]
        workspace=root.parent/'crypto-research-agent-demo-20261108/runtime/sessions/c978106b576b4a7392d78f72cfcb2cbe/workspace'
        receipt=workspace/'reviews/review-ee36b7f88ea11086246ba543.json'
        saved=workspace/'drafts/8f3a76ca11ec1ce19c7f7f51.json'
        if not receipt.is_file() or not saved.is_file():self.skipTest('Private recording trace is not in a clean checkout')
        review=json.loads(receipt.read_text());original=ReportDraft.model_validate(json.loads(saved.read_text())['draft'])
        key=lambda target:(target['claim_id'],target['kind'],target['statement'],target['evidence_ids'])
        self.assertEqual([key(t) for t in review_targets(original)],[key(t) for t in review['targets']])
        self.assertEqual([issue['claim_id'] for issue in review['revision_required']],['L7'])
        old=next(target for target in review['targets'] if target['claim_id']=='L7')
        self.assertFalse(is_verified_host_target(old,original,review['evidence_text']))
        rows={rid:json.loads(text) for rid,text in review['evidence_text'].items()}
        service=object.__new__(ResearchTools);service.mode='observed';service.lookup=rows.__getitem__
        corrected=service._delivery_disclosures(original)
        self.assertEqual(corrected.limitations[:-1],original.limitations[:-1])
        self.assertIn('2026-11-08T14:49:21.723032Z',corrected.limitations[-1])
        target=next(t for t in review_targets(corrected) if t['statement']==corrected.limitations[-1])
        self.assertTrue(is_verified_host_target(target,corrected,review['evidence_text']))


if __name__=='__main__':unittest.main()
