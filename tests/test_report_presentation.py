"""Report overview, exact chart projection and original-document publication gates."""

from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from fixtures import fixture_run
from research_agent.host_review_facts import BOUND_RECORDS_PREFIX, BOUND_RECORDS_SUFFIX
from research_agent.report_presentation import (
    build_report_presentation, document_review_targets, presentation_evidence_ids, report_timeline,
    report_watch_items,
)
from research_agent.report_recovery import ARCHIVE_LIMITATION, omit_rejected_optional_items, prepare_report_documents
from research_agent.review import review_targets
from research_agent.reports import ReportDraft, render_report, validate_report
from research_agent.tools import ResearchTools


START = "2026-10-19T00:00:00Z"
END = "2026-10-19T04:00:00Z"
AS_OF = "2026-10-21T00:00:00Z"
CREATED = "2026-10-21T01:00:00Z"
SERIES = "series-" + "1" * 24
DOC = "page-" + "2" * 24
LIVE = "live-" + "3" * 24
QUOTE = "The testnet upgrade was postponed."


class ReportPresentationTests(unittest.TestCase):
    def setUp(self):
        original = b"<article>The testnet upgrade was postponed.</article>"
        digest = sha256(original).hexdigest()
        self.raw = {digest: original}
        doc_url = "https://example.org/upgrade"
        self.doc = {
            "record_id": DOC, "url": doc_url, "title": "Protocol schedule update",
            "published_at": "2026-10-19T02:00:00Z", "retrieved_at": "2026-10-20T10:00:00Z",
            "text": QUOTE + " The maintainer will publish another schedule.",
            "quality_flags": ["captured_original_page"],
            "source": {"publisher_id": "publisher", "name": "Test publisher", "url": doc_url, "sha256": digest},
        }
        dataset = {
            "dataset_id": "market.bitstamp.eth.hourly", "asset": "ETH", "frequency": "1h",
            "units": {"close_usd": "USD"}, "provider": "bitstamp",
        }
        records = [
            {"record_id": f"fixture-{hour}", "dataset_id": dataset["dataset_id"],
             "observed_at": f"2026-10-19T{hour:02d}:00:00Z", "period_end": f"2026-10-19T{hour+1:02d}:00:00Z",
             "retrieved_at": self.doc["retrieved_at"], "values": {"close_usd": price},
             "units": {"close_usd": "USD"}, "sampling": "1h", "asset": "ETH", "provider": "bitstamp",
             "source": {"name": "Bitstamp", "url": "https://example.org/prices", "sha256": "a" * 64}}
            for hour, price in [(0, "2000.0100"), (2, "2002.0500"), (3, "2003.5000"), (4, "9999")]
        ]
        self.collection = {
            "record_id": SERIES, "dataset_id": dataset["dataset_id"], "collection_kind": "library_series",
            "query": {"dataset": dataset, "query_result_fully_materialized": True}, "records": records,
            "input_record_ids": [row["record_id"] for row in records],
        }
        self.records = {DOC: self.doc, SERIES: self.collection}
        self.payload = {
            "title": "Ether research", "summary": "A testnet upgrade was postponed.",
            "scope": {"assets": ["ETH"], "start": START, "end": END},
            "observations": [{"statement": QUOTE, "evidence_ids": [DOC]}],
            "charts": [{"evidence_id": SERIES, "metric": "close_usd"}],
            "documents": [{"evidence_id": DOC, "quote": QUOTE,
                           "relevance": {"statement": "The publisher postponed the testnet upgrade.",
                                         "evidence_ids": [DOC]}}],
            "limitations": ["The dataset misses one hourly point."],
        }

    def raw_reader(self, row):
        return self.raw[row["source"]["sha256"]]

    def draft(self, payload=None):
        return ReportDraft.model_validate(payload or self.payload)

    def present(self, payload=None):
        return build_report_presentation(
            self.draft(payload), self.records.__getitem__, user_prompt="What changed in <ETH>?",
            created_at=CREATED, evidence_as_of=AS_OF, raw_reader=self.raw_reader,
        )

    def test_builds_overview_exact_points_gaps_and_inspectable_document(self):
        draft = self.draft()
        projection = self.present()
        overview = projection["overview"]
        self.assertEqual(overview["user_prompt"], "What changed in <ETH>?")
        self.assertEqual((overview["created_at"], overview["evidence_as_of"]), (CREATED, AS_OF))
        self.assertEqual(overview["document_ids"], [DOC])
        chart = projection["charts"][0]
        self.assertEqual((chart["asset"], chart["provider"], chart["frequency"], chart["unit"]),
                         ("ETH", "bitstamp", "1h", "USD"))
        self.assertEqual([point["value"] for point in chart["points"]], ["2000.0100", "2002.0500", "2003.5000"])
        self.assertEqual([point["gap_before"] for point in chart["points"]], [False, True, False])
        self.assertEqual(chart["coverage"]["internal_gap_count"], 1)
        self.assertEqual(chart["points"][-1]["at"], END)
        self.assertFalse(chart["coverage"]["ends_before_window"])
        document = projection["documents"][0]
        self.assertEqual((document["quote"], document["publisher"]), (QUOTE, "Test publisher"))
        self.assertEqual(presentation_evidence_ids(draft), [SERIES, DOC])
        self.assertEqual(document_review_targets(draft)[0]["evidence_ids"], [DOC])
        checked = validate_report(draft, self.records.__getitem__, AS_OF, raw_reader=self.raw_reader)
        self.assertEqual((checked["charts_checked"], checked["chart_points_checked"], checked["featured_documents_checked"]),
                         (1, 3, 1))
        markdown = render_report(draft, END, checked, [], presentation=projection)
        self.assertLess(markdown.index("## Summary"), markdown.index("## Key data charts"))
        self.assertLess(markdown.index("## Key events"), markdown.index("Captured publication"))
        self.assertEqual([line for line in markdown.splitlines() if line.startswith('## ')],
            ["## Summary", "## Key data charts", "## Key metric changes", "## Key events", "## Research judgment",
             "## What to watch next", "## Limits and unknowns", "## Sources, review and downloads"])
        self.assertIn("What changed in &lt;ETH&gt;?", markdown)
        self.assertIn(f"../records/{SERIES}.json", markdown)
        self.assertIn(f"../records/{DOC}.json", markdown)

    def test_wrong_metric_returns_exact_saved_keys_without_aliasing(self):
        for metric in ('close', 'close_USD'):
            payload=deepcopy(self.payload)
            payload['charts'][0]['metric']=metric
            with self.assertRaisesRegex(ValueError, 'case-sensitive.*close_usd.*publish_saved_report'):
                self.present(payload)
        self.assertEqual(self.present()['charts'][0]['metric'], 'close_usd')

    def test_original_task_is_preserved_only_inside_collapsed_provenance(self):
        projection=self.present()
        prompt='Weekly ETH question.\nBENCHMARK-CONFIG <script>tool arguments</script>\nDo not trim me.  '
        projection['overview']['user_prompt']=prompt
        before=deepcopy(projection);digest=sha256(prompt.encode()).hexdigest()
        markdown=render_report(self.draft(),END,{},[],presentation=projection)
        start=markdown.index('<details>\n<summary>Source checks and technical provenance</summary>')
        self.assertNotIn('BENCHMARK-CONFIG',markdown[:start])
        self.assertEqual(markdown.count(escape(prompt)),1)
        self.assertIn('### Original task question',markdown[start:])
        self.assertNotIn('<details open',markdown)
        self.assertIn('main in-window evidence',markdown)
        self.assertIn('Captured publication',markdown)
        self.assertIn("does not imply an issuer's first-hand statement",markdown[start:])
        self.assertEqual(projection,before)
        self.assertEqual(sha256(projection['overview']['user_prompt'].encode()).hexdigest(),digest)

    def test_invalid_media_is_rejected_before_publication(self):
        original = deepcopy(self.collection)
        self.collection["query"]["query_result_fully_materialized"] = False
        with self.assertRaisesRegex(ValueError, "partial page"):
            self.present()
        self.collection.update(deepcopy(original))
        self.collection["records"][1]["units"]["close_usd"] = "BTC"
        with self.assertRaisesRegex(ValueError, "unit.*dataset contract"):
            self.present()
        self.collection.update(deepcopy(original))
        self.collection["records"][1]["values"]["close_usd"] = "NaN"
        with self.assertRaisesRegex(ValueError, "non-finite"):
            self.present()
        self.collection.update(deepcopy(original))
        self.collection["records"][1]["available_at"] = CREATED
        with self.assertRaisesRegex(ValueError, "acquired after"):
            self.present()

    def test_featured_document_requires_original_quote_url_and_clock(self):
        source = deepcopy(self.doc)
        self.doc["source_kind"] = "feed_item"
        with self.assertRaisesRegex(ValueError, "captured original"):
            self.present()
        self.doc.pop("source_kind")
        self.doc["text"] = "Publisher did not provide the quoted statement."
        with self.assertRaisesRegex(ValueError, "quote.*not present"):
            self.present()
        self.doc.update(deepcopy(source))
        self.doc["source"]["url"] = "javascript:alert(1)"
        with self.assertRaisesRegex(ValueError, "valid saved source URL"):
            self.present()
        self.doc.update(deepcopy(source))
        self.doc["published_at"] = END
        with self.assertRaisesRegex(ValueError, "publication time.*role"):
            self.present()
        payload = deepcopy(self.payload)
        payload["documents"][0]["role"] = "later_update"
        self.assertEqual(self.present(payload)["documents"][0]["role"], "later_update")
        self.doc["published_at"] = CREATED
        with self.assertRaisesRegex(ValueError, "published after"):
            self.present(payload)
        payload["documents"][0]["relevance"]["evidence_ids"] = [SERIES]
        with self.assertRaisesRegex(ValidationError, "cite its exact saved original"):
            self.draft(payload)

    def test_live_publish_omits_only_ineligible_cards_and_reviews_the_rest(self):
        payload = deepcopy(self.payload)
        original = payload['documents'][0]
        original['quote'] = 'A sentence that the publisher never wrote.'
        bad_role, bad_raw, good = ['page-' + digit * 24 for digit in '456']
        for rid in (bad_role, bad_raw, good):
            card = deepcopy(original)
            card.update(evidence_id=rid, quote=QUOTE)
            card['relevance']['evidence_ids'] = [rid]
            payload['documents'].append(card)
            self.records[rid] = {**deepcopy(self.doc), 'record_id': rid}
        self.records[bad_role]['published_at'] = END
        self.records[good]['text'] = QUOTE.replace('upgrade was', 'upgrade\nwas')
        draft = self.draft(payload)
        submitted = draft.model_dump()
        review = Mock(return_value={'review_id': 'review-fixture', 'status': 'reviewed',
                                    'claims_checked': 2, 'reviewer_model': 'fixture'})
        events = Mock()

        with TemporaryDirectory() as tmp, patch('research_agent.tools.utcnow', return_value=AS_OF):
            root = Path(tmp)
            raw_path = root / 'source.html'
            raw_path.write_bytes(self.raw[self.doc['source']['sha256']])
            for rid in (DOC, bad_role, good):
                row = self.records[rid]
                row['raw_path'] = str(raw_path)
                row['source']['retrieved_at'] = row['retrieved_at']
            self.records.update({row['record_id']: row for row in self.collection['records']})
            service = ResearchTools(fixture_run(root / 'project'), root / 'workspace', AS_OF,
                                    allow_live=True, reviewer=SimpleNamespace(review=review), event_sink=events)
            service.report_user_prompt = 'What changed in the reporting window?'
            service.lookup = self.records.__getitem__
            saved = service.publish(draft)
            bundle = json.loads((service.workspace / saved['structured_path'].lstrip('/')).read_text())

            review.assert_called_once()
            reviewed = review.call_args.args[0]
            self.assertEqual(reviewed.observations, draft.observations)
            self.assertEqual(reviewed.charts, draft.charts)
            self.assertEqual([item.evidence_id for item in reviewed.documents], [good])
            self.assertEqual(reviewed.model_dump(), bundle['report'])
            self.assertEqual(bundle['presentation']['charts'][0]['coverage']['point_count'], 3)
            self.assertEqual(bundle['presentation']['overview']['document_ids'], [good])
            self.assertEqual(bundle['quote_bindings'][0]['path'], '/documents/0/quote')
            self.assertEqual(bundle['quote_bindings'][0]['quote'], self.records[good]['text'])

            omissions = bundle['document_omissions']
            events.assert_any_call('report_documents_omitted', omissions)
            self.assertEqual([row['evidence_id'] for row in omissions['documents']], [DOC, bad_role, bad_raw])
            self.assertEqual([row['path'] for row in omissions['documents']], ['/documents/0', '/documents/1', '/documents/2'])
            for row, reason in zip(omissions['documents'], ('Quote does not match', 'publication time', 'original saved response')):
                self.assertIn(reason, row['reason'])
            retained_path = service.workspace / 'drafts' / (omissions['source_draft_id'] + '.json')
            self.assertEqual(json.loads(retained_path.read_text())['draft'], submitted)
            self.assertEqual(draft.model_dump(), submitted)

            bad_claim = deepcopy(payload)
            bad_claim['observations'][0].update(metric='made_up', value='1', unit='USD')
            with self.assertRaisesRegex(ValueError, 'Numeric unit or cited field mismatch at /observations/0'):
                service.publish(bad_claim)
            bad_chart = deepcopy(payload)
            bad_chart['charts'][0]['metric'] = 'made_up'
            with self.assertRaisesRegex(ValueError, 'Chart metric needs one exact saved unit'):
                service.publish(bad_chart)
            review.assert_called_once()

    def test_live_tool_recovers_misnested_title_and_malformed_card_with_original_receipt(self):
        payload = deepcopy(self.payload)
        payload['scope']['title'] = payload.pop('title')
        payload['documents'][0]['relevance'].update(value='123', unit='ETH')
        payload['key_changes'] = deepcopy(payload['observations'])
        with self.assertRaises(ValidationError):
            ReportDraft.model_validate(payload)
        with TemporaryDirectory() as tmp, patch('research_agent.tools.utcnow', return_value=AS_OF):
            root = Path(tmp)
            self.records.update({r['record_id']: r for r in self.collection['records']})
            self.doc['source']['retrieved_at'] = self.doc['retrieved_at']
            events = Mock()
            service = ResearchTools(fixture_run(root/'project'), root/'workspace', AS_OF, allow_live=True, event_sink=events)
            service.report_user_prompt = 'Report on this week.'
            service.lookup = self.records.__getitem__
            tool = next(t for t in service.as_tools() if t.name == 'publish_report')
            result = json.loads(tool.invoke({'draft': payload}))
            saved = json.loads((service.workspace/result['structured_path'].lstrip('/')).read_text())
            self.assertEqual(saved['report']['title'], payload['scope']['title'])
            self.assertEqual(saved['report']['observations'][0]['statement'], payload['observations'][0]['statement'])
            self.assertEqual(saved['report']['key_changes'][0]['statement'], payload['key_changes'][0]['statement'])
            self.assertEqual(saved['report']['charts'], payload['charts'])
            self.assertEqual(saved['report']['documents'], [])
            receipt = saved['input_adjustments']
            self.assertEqual(receipt['changes'][0], {'action':'move','from':'/draft/scope/title','to':'/draft/title'})
            self.assertEqual(receipt['changes'][1]['evidence_id'], DOC)
            self.assertEqual(receipt['changes'][1]['errors'][0]['path'], '/draft/documents/0/relevance')
            self.assertEqual(json.loads((service.workspace/receipt['original_path'].lstrip('/')).read_text())['draft'], payload)
            self.assertTrue(any(event.args[0] == 'report_input_adjusted' for event in events.call_args_list))

            mixed = deepcopy(payload)
            mixed['title'] = 'A different report title'
            with self.assertRaises(ValidationError) as error:
                tool.invoke({'draft': mixed})
            self.assertIn(('draft','scope','title'), {e['loc'] for e in error.exception.errors()})
            mixed = deepcopy(payload)
            mixed['observations'][0].update(value='1', unit='ETH')
            with self.assertRaises(ValidationError) as error:
                tool.invoke({'draft': mixed})
            self.assertIn(('draft','observations',0), {e['loc'] for e in error.exception.errors()})

    def test_card_only_numeric_mismatch_is_recorded_without_rewriting_fact_or_unit(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root/'source.html'
            raw.write_bytes(self.raw[self.doc['source']['sha256']])
            self.doc['raw_path'] = str(raw)
            self.doc['source']['retrieved_at'] = self.doc['retrieved_at']
            payload = deepcopy(self.payload)
            payload['documents'][0]['relevance'].update(metric='eth_holdings', value='42', unit='ETH held')
            original = self.draft(payload)
            reduced, _, omitted = prepare_report_documents(original, self.records.__getitem__, evidence_as_of=AS_OF)
            self.assertEqual(omitted[0]['evidence_id'], DOC)
            self.assertIn('Numeric unit or cited field mismatch', omitted[0]['reason'])
            self.assertEqual(reduced.documents, [])
            self.assertEqual(reduced.observations, original.observations)
            self.assertEqual(reduced.charts, original.charts)
            self.assertEqual(original.documents[0].relevance.unit, 'ETH held')

    def test_semantic_rejections_on_optional_items_trigger_fresh_review_before_publication(self):
        for provider in ('generation_model', 'jev'):
            with self.subTest(provider=provider), TemporaryDirectory() as tmp, patch('research_agent.tools.utcnow', return_value=AS_OF):
                root = Path(tmp)
                raw_path = root / 'original.html'
                raw_path.write_bytes(self.raw[self.doc['source']['sha256']])
                self.doc['raw_path'] = str(raw_path)
                self.doc['source']['retrieved_at'] = self.doc['retrieved_at']
                self.records.update({row['record_id']: row for row in self.collection['records']})
                payload = deepcopy(self.payload)
                payload['questions_to_watch'] = ['Which decision will be announced next?', 'When will the mainnet delay expire?']
                payload['limitations'].extend([payload['limitations'][0], 'Further observations depend on the next release.'])
                payload['key_changes'] = deepcopy(payload['observations'])
                drafts = []

                def review(draft, lookup):
                    drafts.append(draft.model_dump())
                    targets = review_targets(draft)
                    failed = {'M2', 'L1', 'DOC1'} if len(drafts) == 1 else set()
                    verdicts = [{'claim_id': t['claim_id'], 'status': 'insufficient' if t['claim_id'] in failed else 'supported',
                                 'reason': 'Synthetic source comparison.'} for t in targets]
                    return {'review_id': 'review-fixture-' + str(len(drafts)), 'reviewer_model': provider,
                            'reviewer_provider': provider, 'review_stage': 'full_review', 'batches': [{'status': 'validated'}],
                            'status': 'needs_revision' if failed else 'reviewed', 'claims_checked': len(targets),
                            'verdicts': verdicts, 'targets': targets,
                            'revision_required': [v for v in verdicts if v['claim_id'] in failed]}

                events = Mock()
                service = ResearchTools(fixture_run(root / 'project'), root / 'workspace', AS_OF, allow_live=True,
                                        reviewer=SimpleNamespace(review=review), event_sink=events)
                service.report_user_prompt = 'Report on this week.'
                service.lookup = self.records.__getitem__
                result = service.publish(payload)
                bundle = json.loads((service.workspace / result['structured_path'].lstrip('/')).read_text())
                self.assertEqual(len(drafts), 2)
                original, final = drafts
                self.assertEqual(final, bundle['report'])
                self.assertEqual(final['key_changes'], original['key_changes'])
                self.assertEqual(final['charts'], original['charts'])
                self.assertEqual(final['observations'], original['observations'])
                self.assertEqual(final['summary'], original['summary'])
                self.assertEqual(final['questions_to_watch'], [original['questions_to_watch'][0]])
                self.assertEqual(final['limitations'], original['limitations'][1:])
                self.assertEqual(final['documents'], [])
                self.assertEqual(bundle['review']['review_id'], 'review-fixture-2')
                receipt = bundle['review_omissions']
                self.assertEqual(receipt['review_id'], 'review-fixture-1')
                self.assertEqual({i['path'] for i in receipt['items']},
                                 {'/questions_to_watch/1', '/limitations/0', '/documents/0'})
                archived = json.loads((service.workspace/'drafts'/(receipt['source_draft_id']+'.json')).read_text())
                self.assertEqual(archived['draft'], original)
                events.assert_any_call('report_review_items_omitted', {**receipt, 'revised_draft_id': service.latest_report_draft_id})

    def test_optional_review_edits_reject_mixed_core_host_and_unassessed_issues(self):
        draft = self.draft()
        draft.questions_to_watch = ['When will the upgrade be rescheduled?']
        draft.limitations = [ARCHIVE_LIMITATION, BOUND_RECORDS_PREFIX + AS_OF + BOUND_RECORDS_SUFFIX]
        targets = review_targets(draft)
        review = {'status': 'needs_revision', 'review_stage': 'full_review', 'targets': targets,
                  'verdicts': [{'claim_id': t['claim_id']} for t in targets]}
        for ids in (['M1', 'O1'], ['M1', 'S1.1'], ['L1'], ['L2']):
            with self.subTest(ids=ids):
                issues = [{'claim_id': cid, 'reason': 'Synthetic issue.'} for cid in ids]
                self.assertIsNone(omit_rejected_optional_items(draft, {**review, 'revision_required': issues}))
        issues = [{'claim_id': 'M1', 'reason': 'Synthetic issue.'}]
        for evidence in ({'review_stage': 'scope_critic'}, {'reviewer_provider': 'jev', 'unassessed_targets': issues},
                         {'reviewer_provider': 'jev', 'batches': [{'status': 'invalid_or_failed'}]}):
            with self.subTest(evidence=evidence):
                self.assertIsNone(omit_rejected_optional_items(draft, {**review, **evidence, 'revision_required': issues}))
        draft.limitations = ['The remaining uncertainty.']
        only_limit = {'claim_id': 'L1', 'reason': 'Synthetic issue.'}
        self.assertIsNone(omit_rejected_optional_items(draft, {**review, 'targets': review_targets(draft),
                                                               'revision_required': [only_limit]}))

    def test_single_disputed_clause_cannot_erase_a_material_caveat(self):
        from research_agent.review_feedback import review_failure_feedback

        draft = self.draft()
        caveat = 'Only one venue was observed. Reporting from all other venues has been suspended.'
        draft.limitations = [caveat, 'A separate limitation about the observation interval.']
        targets = review_targets(draft)
        issue = {'claim_id': 'L1', 'status': 'insufficient', 'reason': 'Other venues were not checked.',
                 'correction': 'Remove the unsupported suspension claim.'}
        review = {'review_id': 'review-fixture', 'review_stage': 'full_review', 'status': 'needs_revision',
                  'targets': targets, 'verdicts': [{'claim_id': t['claim_id']} for t in targets],
                  'revision_required': [issue]}
        self.assertIsNone(omit_rejected_optional_items(draft, review))
        self.assertEqual(draft.limitations[0], caveat)
        feedback = review_failure_feedback(review, draft)['issues'][0]
        self.assertEqual(feedback['claim_path'], '/limitations/0')
        self.assertIn('preserving independent', feedback['edit_rule'])

    def test_frozen_and_unscoped_publish_keep_strict_quote_binding(self):
        for current, scoped in ((False, True), (True, False)):
            with self.subTest(current=current, scoped=scoped), TemporaryDirectory() as tmp:
                payload = deepcopy(self.payload)
                if not scoped:
                    payload.pop('scope')
                payload['documents'][0]['quote'] = 'A fabricated excerpt.'
                root = Path(tmp)
                service = ResearchTools(fixture_run(root / 'project'), root / 'workspace', AS_OF, allow_live=current)
                service.lookup = self.records.__getitem__
                with self.assertRaisesRegex(ValueError, 'Quote does not match'):
                    service.publish(payload)

    def test_document_preflight_keeps_scope_duplicate_and_operational_errors_visible(self):
        draft = self.draft()
        with self.assertRaisesRegex(ValueError, 'Report window ends'):
            prepare_report_documents(draft, self.records.__getitem__, evidence_as_of=START)
        draft.documents.append(draft.documents[0])
        with self.assertRaisesRegex(ValueError, 'Duplicate featured document'):
            prepare_report_documents(draft, self.records.__getitem__, evidence_as_of=AS_OF)
        with self.assertRaises(PermissionError):
            prepare_report_documents(self.draft(), Mock(side_effect=PermissionError), evidence_as_of=AS_OF)
        self.doc['raw_path'] = 'synthetic-path.html'
        with patch('research_agent.report_presentation.Path.read_bytes', side_effect=PermissionError):
            with self.assertRaisesRegex(ValueError, 'needs its original saved response'):
                prepare_report_documents(self.draft(), self.records.__getitem__, evidence_as_of=AS_OF)

    def test_omissions_refresh_the_stale_only_disclosure(self):
        with TemporaryDirectory() as tmp, patch('research_agent.tools.utcnow', return_value=AS_OF):
            root = Path(tmp)
            service = ResearchTools(fixture_run(root / 'project'), root / 'workspace', AS_OF, allow_live=True)
            service.report_user_prompt = 'What is my saved research context?'
            payload = deepcopy(self.payload)
            payload['observations'][0]['evidence_ids'] = [service.context_record['record_id']]
            payload['charts'] = []
            payload['limitations'] = [BOUND_RECORDS_PREFIX + AS_OF + BOUND_RECORDS_SUFFIX]
            service.lookup = {**self.records, service.context_record['record_id']: service.context_record}.__getitem__

            saved = service.publish(payload)
            self.assertEqual(saved['delivery_kind'], 'full')
            self.assertEqual(service.last_report['draft']['limitations'], ['No original document cards are included in this report.'])

    def test_coinbase_live_candles_are_checked_against_raw_source(self):
        hours = [0, 1, 2, 3]
        points = [
            {"observed_at": f"2026-10-19T{hour:02d}:00:00Z", "period_end": f"2026-10-19T{hour+1:02d}:00:00Z",
             "close_usd": str(2000 + hour), "volume_asset": str(10 + hour)}
            for hour in hours
        ]
        raw = json.dumps([
            [int(datetime.fromisoformat(point["observed_at"]).timestamp()), 1998, 2005, 1999, 2000 + hour, 10 + hour]
            for hour, point in zip(hours, points)
        ]).encode()
        digest = sha256(raw).hexdigest()
        self.raw[digest] = raw
        live = {
            "record_id": LIVE, "dataset_id": "live.hourly_candles.eth", "metric": "hourly_candles",
            "provider": "coinbase", "instrument": "ETH-USD", "quote_currency": "USD", "asset": "ETH",
            "schema_version": "market-record-v1", "units": {"close_usd": "USD"}, "series": points,
            "retrieved_at": AS_OF, "source": {"name": "Coinbase", "url": "https://example.org/candles", "sha256": digest},
        }
        self.records[LIVE] = live
        payload = deepcopy(self.payload)
        payload["charts"] = [{"evidence_id": LIVE, "metric": "volume_asset"}]
        chart = self.present(payload)["charts"][0]
        self.assertEqual([point["value"] for point in chart["points"]], ["10", "11", "12", "13"])
        self.assertEqual(chart["unit"], "ETH")
        live["series"][1]["volume_asset"] = "30"
        with self.assertRaisesRegex(ValueError, "differs from the saved raw"):
            self.present(payload)
        self.raw[digest] = b"tampered"
        with self.assertRaisesRegex(ValueError, "raw capture hash mismatch"):
            self.present(payload)

    def public_coinbase_fixture(self, *, hostname="api.exchange.coinbase.com",
                                query="granularity=3600", replace=None):
        path = "/products/ETH-USD/candles"
        url = f"https://{hostname}{path}?{query}"
        received = "2026-10-19T04:30:00Z"
        rows = [
            [int(datetime.fromisoformat(f"2026-10-19T{hour:02d}:00:00+00:00").timestamp()),
             1998, 2005, 1999, 2000 + hour, 10 + hour]
            for hour in reversed(range(5))
        ]
        if replace is not None:
            rows[0] = replace(rows)
        raw = json.dumps(rows).encode()
        digest = sha256(raw).hexdigest()
        self.raw[digest] = raw
        record_id = "live-" + sha256((url + digest + received).encode()).hexdigest()[:24]
        row = {
            "record_id": record_id, "dataset_id": "live.api.coinbase",
            "retrieved_at": received, "observed_at": received, "available_at": received,
            "source": {"source_id": record_id, "name": "coinbase" + path, "url": url,
                       "sha256": digest, "retrieved_at": received},
        }
        self.records[record_id] = row
        draft = deepcopy(self.payload)
        draft["scope"]["end"] = "2026-10-19T05:00:00Z"
        draft["charts"] = [{"evidence_id": record_id, "metric": "close"}]
        return draft, row

    def test_public_coinbase_chart_reconstructs_only_completed_saved_candles(self):
        draft, _ = self.public_coinbase_fixture()
        chart = self.present(draft)["charts"][0]
        self.assertEqual((chart["asset"], chart["frequency"], chart["unit"], chart["title"]),
                         ("ETH", "1h", "USD", "Closing price"))
        self.assertEqual([point["value"] for point in chart["points"]], ["2000", "2001", "2002", "2003"])
        self.assertEqual(chart["points"][-1]["at"], END)
        self.assertTrue(chart["coverage"]["ends_before_window"])

    def test_public_coinbase_chart_uses_the_explicit_capture_range(self):
        draft, _ = self.public_coinbase_fixture(
            query="granularity=3600&start=2026-10-19T01%3A00%3A00Z&end=2026-10-19T03%3A00%3A00Z")
        draft["charts"][0]["metric"] = "volume_asset"
        chart = self.present(draft)["charts"][0]
        self.assertEqual(chart["unit"], "ETH")
        self.assertEqual([point["value"] for point in chart["points"]], ["11", "12"])
        self.assertTrue(chart["coverage"]["starts_after_window"])

    def test_public_coinbase_date_only_query_is_utc_without_relaxing_other_clocks(self):
        query = "start=2026-10-19&end=2026-10-20&granularity=3600"
        draft, row = self.public_coinbase_fixture(query=query)
        chart = self.present(draft)["charts"][0]
        self.assertEqual([point["value"] for point in chart["points"]], ["2000", "2001", "2002", "2003"])
        self.assertEqual(chart["points"][0]["observed_at"], START)
        self.assertEqual(chart["sources"][0]["url"], row["source"]["url"])
        naive, _ = self.public_coinbase_fixture(query=query.replace("2026-10-19", "2026-10-19T00:00:00"))
        with self.assertRaisesRegex(ValueError, "report clock must include a time zone"):
            self.present(naive)
        draft["scope"]["start"] = "2026-10-19"
        with self.assertRaisesRegex(ValidationError, "A report clock must be an ISO timestamp with a time zone"):
            self.present(draft)

    def test_public_coinbase_chart_requires_bound_origin_and_exact_raw_layout(self):
        for options, reason in (
            ({"hostname": "example.org"}, "bound Coinbase Exchange"),
            ({"query": "granularity=3600&granularity=86400"}, "explicit hourly or daily"),
            ({"replace": lambda rows: rows[0][:-1]}, "Invalid raw Coinbase"),
            ({"replace": lambda rows: rows[1]}, "duplicate raw candle"),
            ({"replace": lambda rows: [rows[0][0] + 1, *rows[0][1:]]}, "Invalid raw Coinbase"),
        ):
            with self.subTest(reason=reason):
                draft, _ = self.public_coinbase_fixture(**options)
                with self.assertRaisesRegex(ValueError, reason):
                    self.present(draft)
        draft, row = self.public_coinbase_fixture()
        row["source"]["source_id"] = LIVE
        with self.assertRaisesRegex(ValueError, "bound Coinbase Exchange"):
            self.present(draft)
        draft, row = self.public_coinbase_fixture()
        self.raw[row["source"]["sha256"]] = b"[]"
        with self.assertRaisesRegex(ValueError, "raw capture hash mismatch"):
            self.present(draft)

    def test_legacy_report_loads_and_has_no_projection(self):
        payload = {key: value for key, value in self.payload.items() if key not in ("scope", "charts", "documents")}
        draft = self.draft(payload)
        self.assertIsNone(self.present(payload))
        self.assertNotIn("charts_checked", validate_report(draft, self.records.__getitem__))
        markdown = render_report(draft, END, {}, [])
        self.assertNotIn("## Report overview", markdown)
        self.assertIn("## Key events", markdown)
        self.assertNotIn("## Main facts", markdown)

    def test_timeline_groups_sources_and_preserves_unmatched_facts(self):
        other = "page-" + "3"*24
        orphan = "page-" + "4"*24
        scope = {"assets":["ETH"],"start":"2026-11-11T00:00:00Z","end":"2026-11-18T00:00:00Z"}
        draft = {"scope":scope,"key_changes":[
            {"statement":"16 Nov: A company disclosed a purchase.","evidence_ids":[DOC]},
            {"statement":"11–17 Nov: A working group met.","evidence_ids":[other]},
        ],"observations":[
            {"statement":"A company disclosed a purchase.","evidence_ids":[DOC]},
            {"statement":"The purchase comprised 250 ETH.","evidence_ids":[DOC]},
            {"statement":"A proposal remained open.","evidence_ids":[orphan]},
        ]}
        presentation = {"documents":[{"evidence_id":DOC,"title":"Company filing","published_at":"2026-11-17T04:00:00Z"}]}
        rows = report_timeline(draft,presentation,lambda rid:{"published_at":"2026-11-12T18:00:00Z"})
        self.assertEqual([row["date"] for row in rows],["2026-11-11","2026-11-12","2026-11-16"])
        self.assertEqual([row["date_kind"] for row in rows],["event","publication","event"])
        self.assertEqual(rows[0]["date_label"],"11–17 Nov")
        self.assertEqual(rows[2]["display_statement"],"A company disclosed a purchase.")
        self.assertEqual([fact["statement"] for fact in rows[2]["facts"]],["The purchase comprised 250 ETH."])
        self.assertEqual([doc["title"] for doc in rows[2]["documents"]],["Company filing"])
        self.assertEqual(report_watch_items(["Check vote?","check  vote.","Check filings","Check status","Ignore"]),
                         ["Check vote?","Check filings","Check status"])


if __name__ == "__main__":
    unittest.main()
