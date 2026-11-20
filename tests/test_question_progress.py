import json
import unittest

from research_agent.question_progress import describe_attempt, finish_attempt, progress_hint, repeat_decision


def read(record='page-a', query=''):
    return describe_attempt('read_evidence', {'record_id': record, 'query': query})


def original(record='page-a', start=0, end=200, digest='A'):
    return {'record': {'record_id': record, 'source_slices': [{'start': start, 'end': end, 'sha256': digest}]}}


class QuestionProgressTests(unittest.TestCase):
    def test_first_failure_and_changed_inputs_remain_eligible(self):
        first, variant = read(query='date'), read(query='precise date')
        outcome = finish_attempt(first, {'error': 'Original  unavailable'}, status='error')
        same = finish_attempt(variant, {'status': 'error', 'error': 'Original unavailable'})
        self.assertIsNone(repeat_decision(first, [outcome]))
        self.assertIsNone(repeat_decision(read(query='effective date'), [outcome, same]))
        self.assertIsNone(repeat_decision(read('page-b', query='date'), [outcome, same]))
        self.assertEqual(repeat_decision(first, [outcome, same])['reason'], 'repeated_error')

    def test_a_different_span_counts_as_new_evidence(self):
        first = read(query='date')
        variant = read(query='precise date')
        first_receipt = finish_attempt(first, original())
        same = finish_attempt(variant, original())
        newer = finish_attempt(variant, original(start=100, end=300, digest='B'))
        self.assertEqual(repeat_decision(first, [first_receipt, same])['reason'], 'repeated_evidence')
        self.assertIsNone(repeat_decision(first, [first_receipt, newer]))
        self.assertIsNone(repeat_decision(first, [first_receipt, same, newer]))

    def test_empty_recall_variants_with_same_scope_and_different_windows(self):
        args = {'kind': 'news', 'start': '2026-09-01', 'end': '2026-10-01'}
        first = describe_attempt('recall_memory', {**args, 'query': 'date'})
        variant = describe_attempt('recall_memory', {**args, 'query': 'upgrade date'})
        new_window = describe_attempt('recall_memory', {**args, 'query': 'date', 'end': '2026-11-01'})
        receipts = [finish_attempt(item, {'results': []}) for item in (first, variant)]
        self.assertEqual(repeat_decision(first, receipts)['reason'], 'repeated_empty_result')
        self.assertEqual(progress_hint(receipts)['labels'], ['Search for date', 'Search for upgrade date'])
        self.assertIsNone(repeat_decision(describe_attempt('recall_memory', {**args, 'query': 'ACTUAL date'}), receipts))
        self.assertIsNone(repeat_decision(new_window, receipts))
        unknown = finish_attempt(first, 'plain text with no trusted result envelope')
        self.assertEqual(unknown['outcome'], 'unknown')
        self.assertIsNone(repeat_decision(first, [*receipts, unknown]))

    def test_web_receipts_ignore_minted_ids_and_acquisition_clocks(self):
        attempt = describe_attempt('search_web', {'query': 'official upgrade date'})
        first = {'record_id': 'web-a', 'url': 'https://example.com/upgrade#v1',
                 'excerpt': 'A date is pending.', 'retrieved_at': '2026-10-01'}
        same = {**first, 'record_id': 'web-b', 'retrieved_at': '2026-10-02'}
        updated = {**same, 'excerpt': 'The date was confirmed.'}
        receipts = [finish_attempt(attempt, {'results': [item]}) for item in (first, same, updated)]
        self.assertIsNotNone(repeat_decision(attempt, receipts[:2]))
        self.assertIsNone(repeat_decision(attempt, receipts))

    def test_public_capture_detects_reused_provider_bytes(self):
        attempt = describe_attempt('request_public_api', {'provider': 'test', 'path': '/rates', 'params': {'days': 2}})
        first = {'record_id': 'live-a', 'dataset_id': 'public.rates', 'source': {'url': 'https://example.com/rates', 'sha256': 'A'}}
        same = {**first, 'record_id': 'live-b', 'cache_hit': True}
        changed = {**same, 'source': {**first['source'], 'sha256': 'B'}}
        receipts = [finish_attempt(attempt, item) for item in (first, same, changed)]
        self.assertIsNotNone(repeat_decision(attempt, receipts[:2]))
        self.assertIsNone(repeat_decision(attempt, receipts))

    def test_incomplete_reader_navigation_is_not_proof_of_repetition(self):
        attempt = describe_attempt('task', {'description': '[Q1] Compare upgrade dates', 'subagent_type': 'evidence-reader'})
        finding = {'finding': 'An announcement gives a proposed date.',
                   'citations': [{'evidence_id': 'page-a', 'start': 100, 'end': 150, 'sha256': 'A'}]}
        complete = {'status': 'completed', 'findings': [finding], 'reader_id': 'reader-1'}
        same = {**complete, 'reader_id': 'reader-2', 'artifact': '/different/run'}
        receipts = [finish_attempt(attempt, [{'type': 'text', 'text': json.dumps(item)}]) for item in (complete, same)]
        self.assertIsNotNone(repeat_decision(attempt, receipts))
        incomplete = finish_attempt(attempt, {'status': 'incomplete', 'findings': [], 'opened_source_count': 4})
        omitted = finish_attempt(attempt, {**same, 'omitted_findings': 1})
        self.assertIsNone(repeat_decision(attempt, [incomplete, incomplete]))
        self.assertIsNone(repeat_decision(attempt, [omitted, omitted]))

    def test_shell_and_submission_are_not_blocked(self):
        shell = describe_attempt('execute', {'command': 'cat generated.txt'})
        error = finish_attempt(shell, 'No such file', status='error')
        variant = describe_attempt('execute', {'command': 'head generated.txt'})
        receipts = [error, finish_attempt(variant, 'No such file', status='error')]
        self.assertEqual(progress_hint(receipts)['reason'], 'repeated_error')
        self.assertIsNone(repeat_decision(shell, receipts))
        self.assertIsNone(describe_attempt('publish_saved_report', {'draft_id': 'same'}))
        self.assertIsNone(describe_attempt('task', {'description': 'other', 'subagent_type': 'general-purpose'}))

    def test_display_labels_keep_query_navigation_without_urls_secrets_or_commands(self):
        read_label = read('page-' + 'a' * 24)['label']
        self.assertEqual(read_label, 'Open page-' + 'a' * 24)
        search = describe_attempt('search_web', {'query': 'Upgrade date https://example.com/?secret=x'})
        self.assertEqual(search['label'], 'Search for Upgrade date [URL]')
        relative = describe_attempt('search_web', {'query': 'Upgrade /api/detail?session=value'})
        self.assertEqual(relative['label'], 'Search for Upgrade [URL]')
        secret = describe_attempt('search_web', {'query': 'Find records for Authorization: bearer test-secret'})
        self.assertEqual(secret['label'], 'Search for evidence')
        self.assertEqual(describe_attempt('execute', {'command': 'print private'})['label'], 'Run a workspace command')
        api = describe_attempt('request_public_api', {'path': '/public?token=secret'})
        self.assertEqual(api['label'], 'Read a public API response')


if __name__ == '__main__':
    unittest.main()
