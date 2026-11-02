from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from research_agent.knowledge import KnowledgeStore
from research_agent.experience_memory import ExperienceMemory
from research_agent.report_feedback import ReportFeedbackStore
from research_agent.reports import ReportDraft
from research_agent.review import check_review, evidence_text, review_targets


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / 'project'
        self.project.mkdir()
        self.knowledge = KnowledgeStore(self.project / 'runtime' / 'knowledge')
        self.records = {
            'page-original': {'record_id': 'page-original', 'dataset_id': 'live.webpage',
                              'text': 'An Ethereum update is planned.', 'values': {}, 'units': {},
                              'retrieved_at': '2026-01-01T00:00:00Z'},
            'page-correction': {'record_id': 'page-correction', 'dataset_id': 'live.webpage',
                                'text': 'The Ethereum update has been delayed.', 'values': {}, 'units': {},
                                'retrieved_at': '2026-01-01T00:00:00Z'},
        }
        self.original = self.save_report('report-original', ['page-original'])
        self.replacement = self.save_report('report-replacement', ['page-correction'])
        self.receipt = self.project / 'local' / 'report-quality' / 'fixture' / 'review.json'
        self.receipt.parent.mkdir(parents=True)
        self.receipt.write_text('{"finding":"The plan was superseded.","token":"fixture-secret"}')
        self.store = self.make_store()

    def make_store(self, experience=None, subdir='feedback'):
        with patch('research_agent.report_feedback.PROJECT_ROOT', self.project):
            return ReportFeedbackStore(self.project / 'runtime' / subdir,
                report_lookup=self.knowledge.report, evidence_lookup=lambda rid: copy.deepcopy(self.records[rid]),
                redact=lambda text: text.replace('fixture-secret', '[REDACTED]'), experience=experience)

    def save_report(self, report_id, evidence_ids, reviewed=True, **overrides):
        text = self.records[evidence_ids[0]]['text']
        draft = ReportDraft(title='Research brief', summary=text,
            observations=[{'statement': text, 'evidence_ids': evidence_ids}],
            limitations=['Public information may be incomplete.'])
        targets = review_targets(draft)
        response = {'verdicts': [{'claim_id': target['claim_id'], 'status': 'supported',
            'reason': 'The saved announcement supplies this statement.',
            'quotes': [{'record_id': evidence_ids[0], 'text': text}]} for target in targets]}
        review = check_review(response, targets, {rid: evidence_text(self.records[rid]) for rid in evidence_ids})
        review.update(reviewed_at='2026-01-01T00:10:00Z', reviewer_model='fixture-only')
        if not reviewed:
            review['status'] = 'needs_revision'
        body = {'report': draft.model_dump(), 'created_at': '2026-01-01T01:00:00Z', 'review': review,
                'sources': [], **overrides}
        self.knowledge.retain_report(report_id, 'fixture', body)
        return copy.deepcopy(body)

    @contextmanager
    def at(self, value):
        with patch('research_agent.report_feedback.utcnow', return_value=value):
            yield

    def submit(self, store=None, **kwargs):
        return (store or self.store).submit('report-original', 'The announced plan may be outdated.', **kwargs)

    def review(self, feedback, store=None, **kwargs):
        options = {'reviewer_kind': 'independent_model', 'receipt_path': self.receipt,
                   'evidence_ids': ['page-correction'], 'assessment': 'needs_correction',
                   'correction': 'State that the update was delayed and cite the revised announcement.'}
        options.update(kwargs)
        return (store or self.store).review(feedback['feedback_id'], **options)

    def test_submit_binds_a_real_report_target_without_rewriting_report(self):
        item = self.submit(claim_id='O1', category='outdated')
        self.assertEqual(item['status'], 'unverified')
        self.assertEqual(item['target']['claim_id'], 'O1')
        self.assertEqual(len(item['report_sha256']), 64)
        self.assertEqual(len(item['target_sha256']), 64)
        self.assertEqual(self.knowledge.report('report-original'), self.original)
        self.assertEqual(item['reviews'], [])

    def test_bad_reports_targets_categories_and_empty_comments_are_rejected(self):
        for values in ({'report_id': 'missing'}, {'report_id': '../report-original'},
                       {'claim_id': 'O999'}, {'category': 'financial_fact'}, {'note': '   '}):
            options = {'report_id': 'report-original', 'note': 'Inspect this claim.', **values}
            with self.assertRaises(ValueError):
                self.store.submit(**options)
        self.assertEqual(self.store.list_for_report('report-original'), [])

    def test_duplicate_submission_and_review_are_idempotent_and_tables_append_only(self):
        first = self.submit(); second = self.submit()
        self.assertEqual(first['feedback_id'], second['feedback_id'])
        self.assertTrue(second['duplicate'])
        reviewed = self.review(first); repeated = self.review(first)
        self.assertEqual(reviewed['events'], repeated['events'])
        self.assertTrue(repeated['duplicate'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM feedback').fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('DELETE FROM events')

    def test_note_and_immutable_receipt_copy_are_redacted_and_hash_bound(self):
        item = self.store.submit('report-original', 'fixture-secret requires checking.')
        reviewed = self.review(item)
        receipt = reviewed['latest_review']['receipt']
        saved = Path(receipt['path'])
        self.assertNotIn('fixture-secret', item['note'])
        self.assertNotIn('fixture-secret', saved.read_text())
        self.assertEqual(hashlib.sha256(saved.read_bytes()).hexdigest(), receipt['sha256'])
        self.receipt.write_text('{"finding":"later edited"}')
        self.assertIn('superseded', saved.read_text())
        self.assertEqual(reviewed['latest_review']['evidence'][0]['record_id'], 'page-correction')
        self.assertEqual(len(reviewed['latest_review']['evidence'][0]['sha256']), 64)
        self.assertIn('not human review', reviewed['scope'])

    def test_receipt_allowlist_rejects_secrets_outside_paths_binary_and_symlink_escape(self):
        item = self.submit()
        outside = self.project / 'private.json'; outside.write_text('{"private":"fixture"}')
        secret = self.receipt.parent / '.env.json'; secret.write_text('{"secret":"fixture"}')
        config = self.receipt.parent / 'config' / 'report.json'; config.parent.mkdir(); config.write_text('{}')
        linked = self.receipt.parent / 'linked.json'; linked.symlink_to(outside)
        binary = self.receipt.parent / 'bad.txt'; binary.write_bytes(b'\xff\xfe')
        wrong_type = self.receipt.with_suffix('.sqlite'); wrong_type.write_text('not a text receipt')
        for path in (outside, secret, config, linked, binary, wrong_type, self.project / 'missing.json'):
            with self.assertRaises(ValueError, msg=str(path)):
                self.review(item, receipt_path=path)
        self.assertEqual(self.store.list_for_report('report-original')[0]['status'], 'unverified')

    def test_a_session_review_receipt_is_allowed_but_other_workspace_files_are_not(self):
        item = self.submit()
        path = self.project / 'runtime' / 'sessions' / 'fixture' / 'workspace' / 'reviews' / 'review.md'
        path.parent.mkdir(parents=True); path.write_text('A source-backed fixture review.')
        self.assertEqual(self.review(item, receipt_path=path)['status'], 'reviewed')
        bad = path.parent.parent / 'notes' / 'review.md'; bad.parent.mkdir(); bad.write_text('Not a saved review.')
        with self.assertRaises(ValueError):
            self.review(item, receipt_path=bad)

    def test_missing_mismatched_or_future_evidence_cannot_create_a_review(self):
        item = self.submit()
        for ids in ([], ['missing']):
            with self.assertRaises(ValueError):
                self.review(item, evidence_ids=ids)
        self.records['wrong'] = {'record_id': 'another-record'}
        with self.assertRaises(ValueError):
            self.review(item, evidence_ids=['wrong'])
        self.records['future'] = {'record_id': 'future', 'retrieved_at': '2099-01-01T00:00:00Z'}
        with self.assertRaises(ValueError):
            self.review(item, evidence_ids=['future'])
        self.assertEqual(self.store.list_for_report('report-original')[0]['reviews'], [])

    def test_strict_cutoff_reconstructs_state_without_future_review_or_resolution(self):
        with self.at('2026-01-02T00:00:00Z'):
            item = self.submit()
        with self.at('2026-01-03T00:00:00.500000Z'):
            self.review(item)
        with self.at('2026-01-04T00:00:00Z'):
            self.store.resolve(item['feedback_id'], 'report-replacement')
        self.assertEqual(self.store.list_for_report('report-original', '2026-01-01T23:59:59Z'), [])
        before = self.store.list_for_report('report-original', '2026-01-03T00:00:00.100000Z')[0]
        self.assertEqual(before['status'], 'unverified')
        self.assertNotIn('was delayed', json.dumps(before))
        reviewed = self.store.list_for_report('report-original', '2026-01-03T08:00:00.600000+08:00')[0]
        self.assertEqual(reviewed['status'], 'reviewed')
        self.assertIsNone(reviewed['resolution'])
        self.assertEqual(self.store.list_for_report('report-original', '2026-01-04T00:00:00Z')[0]['status'], 'resolved')
        with self.assertRaises(ValueError):
            self.store.list_for_report('report-original', '2026-01-03')

    def test_unverified_or_inconclusive_feedback_cannot_be_resolved(self):
        item = self.submit()
        with self.assertRaises(ValueError):
            self.store.resolve(item['feedback_id'], 'report-replacement')
        with self.assertRaises(ValueError):
            self.review(item, correction=' ')
        self.review(item, assessment='needs_more_evidence', correction='')
        with self.assertRaises(ValueError):
            self.store.resolve(item['feedback_id'], 'report-replacement')

    def test_resolution_requires_all_review_evidence_and_a_real_complete_source_review(self):
        item = self.submit(); self.review(item)
        self.save_report('report-unreviewed', ['page-correction'], reviewed=False)
        self.save_report('report-unrelated', ['page-original'])
        forged = copy.deepcopy(self.replacement['review'])
        for verdict in forged['verdicts']:
            verdict['quotes'] = []
        self.save_report('report-noquotes', ['page-correction'], review=forged)
        future = copy.deepcopy(self.replacement['review']); future['reviewed_at'] = '2099-01-01T00:00:00Z'
        self.save_report('report-future-review', ['page-correction'], review=future)
        for replacement in ('report-original', 'missing', 'report-unreviewed', 'report-unrelated',
                            'report-noquotes', 'report-future-review'):
            with self.assertRaises(ValueError, msg=replacement):
                self.store.resolve(item['feedback_id'], replacement)
        self.assertEqual(self.store.list_for_report('report-original')[0]['status'], 'reviewed')

    def test_resolution_links_a_distinct_reviewed_replacement_and_preserves_original(self):
        item = self.submit(); self.review(item)
        result = self.store.resolve(item['feedback_id'], 'report-replacement')
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(result['resolution']['replacement_report_id'], 'report-replacement')
        self.assertTrue(result['resolution']['source_review_checked'])
        self.assertEqual(self.knowledge.report('report-original'), self.original)
        self.assertTrue(self.store.resolve(item['feedback_id'], 'report-replacement')['duplicate'])
        with self.assertRaises(ValueError):
            self.review(item)

    def test_saved_reviewer_context_is_checked_against_the_actual_draft_and_record(self):
        self.records['context-user'] = {'record_id': 'context-user',
            'dataset_id': 'local.user_research_parameters', 'profile': {'horizon_days': 30},
            'retrieved_at': '2026-01-01T00:00:00Z', 'scope': 'Fixture user research parameters.'}
        draft = ReportDraft.model_validate(self.replacement['report'])
        targets = review_targets(draft)
        for target in targets:
            if target['kind'] in ('summary', 'judgment') and 'context-user' not in target['evidence_ids']:
                target['evidence_ids'] = [*target['evidence_ids'], 'context-user']
        reviewed = copy.deepcopy(self.replacement['review'])
        reviewed['targets'] = targets
        reviewed['evidence_text'] = {rid: evidence_text(self.records[rid]) for rid in ('page-correction', 'context-user')}
        for verdict in reviewed['verdicts']:
            if verdict['claim_id'] == 'S1':
                verdict['quotes'].append({'record_id': 'context-user', 'text': 'Fixture user research parameters.'})
        self.save_report('report-with-context', ['page-correction'], review=reviewed)
        exported = copy.deepcopy(reviewed); exported.pop('targets'); exported.pop('evidence_text')
        self.save_report('report-exported-context', ['page-correction'], review=exported)
        for report_id in ('report-with-context', 'report-exported-context'):
            item = self.store.submit('report-original', 'Check task context for ' + report_id)
            self.review(item)
            self.assertEqual(self.store.resolve(item['feedback_id'], report_id)['status'], 'resolved')

    def test_saved_targets_cannot_change_claim_content_or_add_context_to_facts(self):
        self.records['context-user'] = {'record_id': 'context-user',
            'dataset_id': 'local.user_research_parameters', 'profile': {},
            'retrieved_at': '2026-01-01T00:00:00Z'}
        draft = ReportDraft.model_validate(self.replacement['report'])
        bad_content = copy.deepcopy(self.replacement['review'])
        bad_content['targets'] = review_targets(draft)
        bad_content['targets'][0]['statement'] = 'An unrelated assertion.'
        bad_context = copy.deepcopy(self.replacement['review'])
        bad_context['targets'] = review_targets(draft)
        next(target for target in bad_context['targets'] if target['claim_id'] == 'O1')['evidence_ids'].append('context-user')
        bad_snapshot = copy.deepcopy(self.replacement['review'])
        bad_snapshot['targets'] = review_targets(draft)
        bad_snapshot['evidence_text'] = {'page-correction': 'Forged source serialization.'}
        for report_id, review in (('report-bad-content', bad_content), ('report-bad-context', bad_context),
                                  ('report-bad-snapshot', bad_snapshot)):
            self.save_report(report_id, ['page-correction'], review=review)
        item = self.submit(); self.review(item)
        for report_id in ('report-bad-content', 'report-bad-context', 'report-bad-snapshot'):
            with self.assertRaises(ValueError, msg=report_id):
                self.store.resolve(item['feedback_id'], report_id)

    def test_tampered_saved_receipt_or_evidence_blocks_resolution(self):
        item = self.submit(); reviewed = self.review(item)
        saved = Path(reviewed['latest_review']['receipt']['path']); original = saved.read_bytes()
        saved.write_text('tampered receipt')
        with self.assertRaises(ValueError):
            self.store.resolve(item['feedback_id'], 'report-replacement')
        saved.write_bytes(original)
        self.records['page-correction']['text'] = 'Tampered source content'
        with self.assertRaises(ValueError):
            self.store.resolve(item['feedback_id'], 'report-replacement')

    def test_original_content_binding_is_checked_again_at_resolution(self):
        item = self.submit(); self.review(item)
        original_lookup = self.store.report_lookup
        rebound = copy.deepcopy(self.original); rebound['report']['summary'] = 'Changed original report.'
        self.store.report_lookup = lambda rid: rebound if rid == 'report-original' else original_lookup(rid)
        with self.assertRaises(ValueError):
            self.store.resolve(item['feedback_id'], 'report-replacement')
        self.assertEqual(self.knowledge.report('report-original'), self.original)

    def test_experience_delivery_failure_never_erases_the_review(self):
        class FailingMemory:
            def record_correction(self, *args, **kwargs):
                raise RuntimeError('fixture-secret memory unavailable')
        store = self.make_store(FailingMemory(), 'memory-failure')
        item = self.submit(store); result = self.review(item, store)
        self.assertEqual(result['status'], 'reviewed')
        self.assertEqual(len(result['reviews']), 1)
        self.assertEqual(result['experience']['kind'], 'experience_failed')
        self.assertNotIn('fixture-secret', result['experience']['error'])
        self.assertEqual(store.list_for_report('report-original')[0]['latest_review'], result['latest_review'])

    def test_only_source_backed_corrections_enter_experience_once(self):
        class Memory:
            def __init__(self):self.calls = []
            def record_correction(self, *args, **kwargs):
                self.calls.append((args, kwargs));return {'correction_id': 'correction-fixture'}
        memory = Memory(); store = self.make_store(memory, 'memory-success')
        item = self.submit(store)
        self.review(item, store, assessment='not_reproduced', correction='')
        self.assertEqual(memory.calls, [])
        result = self.review(item, store); self.review(item, store)
        self.assertEqual(len(memory.calls), 1)
        self.assertEqual(memory.calls[0][1]['kind'], 'source_correction')
        self.assertEqual(memory.calls[0][1]['evidence_ids'], ['page-correction'])
        self.assertEqual(result['experience']['correction_id'], 'correction-fixture')

    def test_real_experience_store_receives_and_recalls_source_linked_correction(self):
        memory = ExperienceMemory(self.project / 'runtime' / 'experience',
            evidence_lookup=lambda rid: copy.deepcopy(self.records[rid]))
        store = self.make_store(memory, 'real-experience-feedback')
        item = self.submit(store); reviewed = self.review(item, store)
        self.assertEqual(reviewed['experience']['kind'], 'experience_recorded')
        recalled = memory.recall('Report correction')
        self.assertEqual(len(recalled['results']), 1)
        self.assertEqual(recalled['results'][0]['kind'], 'source_correction')
        self.assertEqual(recalled['results'][0]['affected_outputs'], ['report-original'])
        self.assertIn('not a financial fact', recalled['results'][0]['scope'])


if __name__ == '__main__':
    unittest.main()
