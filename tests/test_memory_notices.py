"""Real feedback-store joins using synthetic reports and local receipt files."""
from copy import deepcopy
import hashlib,json
from pathlib import Path
import tempfile,unittest
from unittest.mock import patch

from research_agent.knowledge import KnowledgeStore
from research_agent.report_feedback import ReportFeedbackStore
from research_agent.reports import ReportDraft
from research_agent.review import review_targets,check_review,evidence_text
from research_agent.memory_notices import attach_report_notices,original_evidence_ids


class MemoryNoticeTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.knowledge=KnowledgeStore(self.root/'knowledge')
        self.records={'page-old':{'record_id':'page-old','text':'The ETH fixture is planned.','retrieved_at':'2026-01-01T00:00:00Z'},
            'page-new':{'record_id':'page-new','text':'The ETH fixture has been postponed.','retrieved_at':'2026-01-01T00:00:00Z'}}
        self.old=self.save('old','page-old','2026-01-01T01:00:00Z')
        self.new=self.save('new','page-new','2026-01-04T01:00:00Z')
        self.receipt=self.root/'local/report-quality/test/review.json';self.receipt.parent.mkdir(parents=True)
        self.receipt.write_text('{"finding":"This is synthetic source-backed review."}')
        with patch('research_agent.report_feedback.PROJECT_ROOT',self.root):
            self.feedback=ReportFeedbackStore(self.root/'feedback',report_lookup=self.knowledge.report,evidence_lookup=self.records.__getitem__)
        self.knowledge.report_feedback=self.feedback

    def save(self,rid,eid,when):
        text=self.records[eid]['text'];d=ReportDraft(title='ETH synthetic report '+rid,summary=text,
            observations=[{'statement':text,'evidence_ids':[eid]}],limitations=['Synthetic only'])
        targets=review_targets(d)
        review=check_review({'verdicts':[{'claim_id':t['claim_id'],'status':'supported','reason':'Fixture',
            'quotes':[{'record_id':eid,'text':text}]} for t in targets]},targets,{eid:evidence_text(self.records[eid])})
        review.update(reviewed_at='2026-01-01T00:10:00Z')
        body={'report':d.model_dump(),'created_at':when,'review':review}
        self.knowledge.retain_report(rid,'fixture',body)
        return body

    def flag(self):
        with patch('research_agent.report_feedback.utcnow',return_value='2026-01-02T00:00:00Z'):
            return self.feedback.submit('old','The old ETH plan may be outdated.',claim_id='O1',category='outdated')

    def review(self,item,**kwargs):
        params={'reviewer_kind':'independent_model','receipt_path':self.receipt,'evidence_ids':['page-new'],
            'assessment':'needs_correction','correction':'Read the newer original, which postpones this plan.'}
        params.update(kwargs)
        with patch('research_agent.report_feedback.utcnow',return_value='2026-01-03T00:00:00Z'):
            return self.feedback.review(item['feedback_id'],**params)

    def result(self,cutoff='2026-01-06T00:00:00Z'):
        return next(row for row in self.knowledge.search_research('ETH',cutoff=cutoff) if row.get('report_id')=='old')

    def test_review_is_attached_to_old_claim_without_rewriting_original(self):
        original=deepcopy(self.knowledge.report('old'));self.review(self.flag())
        row=self.result();self.assertEqual(row['summary'],original['report']['summary'])
        self.assertEqual(row['original_source_evidence_ids'],['page-old'])
        self.assertEqual(row['memory_status'],'affected_claims_require_recheck')
        notice=row['memory_notices'][0]
        self.assertEqual(notice['claim_id'],'O1');self.assertEqual(notice['reviewer_kind'],'independent_model')
        self.assertIn('page-new',notice['review_evidence_ids'])
        self.assertIn('not independently',notice['scope'])
        self.assertEqual(self.knowledge.report('old'),original)

    def test_unreviewed_user_concern_is_only_counted_not_factual_instruction(self):
        self.flag();row=self.result();self.assertEqual(row['unreviewed_concern_count'],1)
        self.assertEqual(row['memory_notices'],[]);self.assertEqual(row['notice_context'],'')
        self.assertNotIn('may be outdated',json.dumps(row))

    def test_future_review_does_not_change_past_result_or_leak_replacement(self):
        self.flag();before=self.result('2026-01-02T12:00:00Z');self.review(self.feedback.list_for_report('old')[0])
        after=self.result('2026-01-02T12:00:00Z');self.assertEqual(before,after)
        self.assertNotIn('page-new',json.dumps(after));self.assertNotIn('postpones',json.dumps(after))

    def test_resolution_links_reviewed_update_only_after_its_known_time(self):
        item=self.review(self.flag())
        with patch('research_agent.report_feedback.utcnow',return_value='2026-01-05T00:00:00Z'):
            self.feedback.resolve(item['feedback_id'],'new')
        earlier=self.result('2026-01-04T12:00:00Z');self.assertEqual(earlier['next_report_ids'],[])
        row=self.result();self.assertEqual(row['next_report_ids'],['new'])
        self.assertEqual(row['memory_status'],'has_source_reviewed_updates')
        self.assertEqual(row['memory_notices'][0]['status'],'source_reviewed_update_available')
        self.assertEqual(row['summary'],self.old['report']['summary'])

    def test_callback_ignoring_cutoff_cannot_inject_future_review(self):
        self.review(self.flag());complete=self.feedback.list_for_report('old')
        self.knowledge.notice_provider=lambda *a,**k:complete
        row=self.result('2026-01-02T12:00:00Z');self.assertEqual(row['memory_notices'],[])
        self.assertNotIn('page-new',json.dumps(row))

    def test_notice_report_binding_mismatch_is_a_gap_not_changed_summary(self):
        self.review(self.flag());items=self.feedback.list_for_report('old');items[0]['report_sha256']='0'*64
        self.knowledge.notice_provider=lambda *a,**k:items
        row=self.result();self.assertEqual(row['memory_notices'],[])
        self.assertEqual(row['notice_gaps'][0]['reason'],'notice_report_binding_mismatch')
        self.assertEqual(row['summary'],self.old['report']['summary'])

    def test_unavailable_optional_notices_preserve_original_research(self):
        def broken(*a,**k):raise OSError('Synthetic failure')
        self.knowledge.notice_provider=broken
        row=self.result();self.assertEqual(row['memory_status'],'notice_lookup_unavailable')
        self.assertEqual(row['original_source_evidence_ids'],['page-old'])

    def test_cutoff_filters_future_reports_answers_and_library_before_join(self):
        from research_agent.answers import AnswerDraft
        draft=AnswerDraft(facts=[{'statement':'ETH fixture','evidence_ids':['page-old']}]).model_dump()
        self.knowledge.retain_answer('future-answer','fixture',{'draft':draft,'created_at':'2026-01-10T00:00:00Z','review':{'status':'reviewed'}})
        calls=[]
        class Library:
            def search_outputs(self,query,**kwargs):
                calls.append(kwargs)
                return {'results':[{'output_id':'future-lib','kind':'answer','created_at':'2026-01-12T00:00:00Z','summary':'Future secret'}]}
        self.knowledge.library=Library()
        rows=self.knowledge.search_research('ETH',cutoff='2026-01-02T00:00:00Z')
        self.assertEqual([r.get('report_id') for r in rows],['old'])
        self.assertTrue(calls[0]['end'].startswith('2026-01-02T00:00:00.000001'))
        self.assertNotIn('future',json.dumps(rows).casefold())

    def test_citations_include_key_changes_and_exclude_review_quotes_context(self):
        body={'report':{'observations':[{'evidence_ids':['page-a','context-user']}],
            'key_changes':[{'evidence_ids':['page-b']}],'advice':{'context_evidence_ids':['context-user'],'rationale':[{'evidence_ids':['page-c']}]},
            'review':{'quotes':[{'evidence_ids':['unrelated']}]}},'review':{'evidence_ids':['review-secret']}}
        self.assertEqual(original_evidence_ids(body),['page-a','page-b','page-c'])

    def test_long_notice_preview_is_marked_as_incomplete(self):
        self.review(self.flag(),correction='Do not infer dates. '+('Qualified condition. '*140))
        row=self.result();self.assertTrue(row['memory_notices'][0]['correction_preview_only'])
        self.assertIn('Preview only',row['notice_context'])

if __name__=='__main__':unittest.main()
