from datetime import datetime
from hashlib import sha256
import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from fixtures import fixture_run
from research_agent.minimal_delivery import (
    evidence_gap_notice, minimal_report, notice_review, omit_single_reviewed_event, prepare_factual_minimal,
    saved_market_fallback, verify_notice_review,
)
from research_agent.reports import ReportDraft
from research_agent.review import review_targets,ReviewBudgetExhausted
from research_agent.tools import ResearchTools


class MinimalDeliveryTests(unittest.TestCase):
    def draft_and_review(self):
        draft=ReportDraft(title='An unsupported headline',summary='An unsupported summary.',
            observations=[{'statement':'A source-backed observation.','evidence_ids':['r']},
                          {'statement':'An unsupported conclusion.','evidence_ids':['r']}],
            limitations=['An unsupported limitation.'])
        targets=review_targets(draft)
        review={'targets':targets,'verdicts':[{'claim_id':'O1','status':'supported','scope_alignment':'aligned',
            'quotes':[{'record_id':'r','text':'A source-backed observation.'}]}],
            'revision_required':[{'claim_id':'O2'}]}
        return draft,review

    def test_keeps_exact_supported_atoms_and_discards_all_dependent_prose(self):
        draft,review=self.draft_and_review();reduced,ids=minimal_report(draft,review)
        self.assertEqual(ids,['O1']);self.assertEqual(reduced.observations,[draft.observations[0]])
        self.assertNotIn('unsupported',reduced.title);self.assertNotIn('unsupported',reduced.summary)
        self.assertFalse(reduced.interpretations or reduced.forecasts or reduced.advice)

    def test_verified_events_survive_metric_cap_and_keep_their_section(self):
        draft,review=self.draft_and_review()
        body=draft.model_dump()
        body['key_changes']=[
            {'statement':'The testnet release was deferred.','evidence_ids':['release']},
            {'statement':'The mainnet release was deferred.','evidence_ids':['release']},
        ]
        draft=ReportDraft.model_validate(body)
        review['targets']=review_targets(draft)
        review['verdicts'].extend([
            {'claim_id':'K1','status':'supported','scope_alignment':'aligned',
             'quotes':[{'record_id':'release','text':'The testnet release was deferred.'}]},
            {'claim_id':'K2','status':'insufficient','scope_alignment':'aligned','quotes':[]},
        ])
        reduced,ids=minimal_report(draft,review,maximum=1)
        self.assertEqual(ids,['O1','K1'])
        self.assertEqual(reduced.key_changes,[draft.key_changes[0]])
        review['verdicts']=review['verdicts'][1:]
        reduced,ids=minimal_report(draft,review)
        self.assertEqual(ids,['K1'])
        self.assertFalse(reduced.observations)
        self.assertEqual(reduced.key_changes,[draft.key_changes[0]])
        self.assertEqual(ReportDraft.model_validate(reduced.model_dump()),reduced)

    def test_unassessed_ambiguous_changed_or_unbound_atoms_never_survive(self):
        for mutation in ('no_verdict', 'unassessed', 'ambiguous', 'not_applicable',
                         'changed', 'unbound', 'counterquote', 'inexact', 'blocked', 'host_issue'):
            draft,review=self.draft_and_review()
            if mutation=='no_verdict':review['verdicts']=[]
            if mutation=='unassessed':review['unassessed_targets']=[{'claim_id':'O1'}]
            if mutation=='ambiguous':review['verdicts'][0]['scope_alignment']='ambiguous'
            if mutation=='not_applicable':review['verdicts'][0]['scope_alignment']='not_applicable'
            if mutation=='changed':draft.observations[0].statement='Changed after review.'
            if mutation=='unbound':review['verdicts'][0]['quotes'][0]['record_id']='elsewhere'
            if mutation=='counterquote':review['verdicts'][0]['quotes'][0]['purpose']='counterevidence'
            if mutation=='inexact':review['evidence_text']={'r':'An unrelated saved excerpt.'}
            if mutation=='blocked':review['revision_required'].append({'claim_id':'O1'})
            if mutation=='host_issue':review['host_issues']=[{'claim_id':'O1'}]
            with self.subTest(mutation=mutation):self.assertIsNone(minimal_report(draft,review)[0])

    def reviewed_report(self, *, observations, events, **fields):
        draft = ReportDraft(title='Draft headline', summary='Draft summary.',
            observations=observations, key_changes=events, limitations=['Draft limitations.'], **fields)
        targets = review_targets(draft)
        review = {'targets': targets, 'verdicts': [
            {'claim_id': target['claim_id'], 'status': 'supported', 'scope_alignment': 'aligned',
             'quotes': [{'record_id': target['evidence_ids'][0], 'text': target['statement']}]}
            for target in targets if target['kind'] == 'observation'
        ]}
        return draft, review

    def test_preserves_three_metrics_and_five_events_inside_the_total_cap(self):
        records = {'metrics': {'record_id': 'metrics', 'values': {}, 'units': {'*': 'USD'}}}
        observations = []
        for index in range(1, 4):
            metric = f'metric_{index}'
            records['metrics']['values'][metric] = str(index)
            observations.append({'statement': f'Metric {index} was {index} USD.',
                'evidence_ids': ['metrics'], 'metric': metric, 'value': str(index), 'unit': 'USD'})
        events = []
        for index in range(1, 6):
            record_id = f'event-{index}'
            records[record_id] = {'record_id': record_id}
            events.append({'statement': f'Saved event {index}.', 'evidence_ids': [record_id]})
        draft, review = self.reviewed_report(observations=observations, events=events)

        reduced, ids = minimal_report(draft, review, lookup=records.__getitem__)
        self.assertEqual(ids, ['O1', 'O2', 'O3', 'K1', 'K2', 'K3', 'K4', 'K5'])
        self.assertEqual(reduced.observations, draft.observations)
        self.assertEqual(reduced.key_changes, draft.key_changes)
        self.assertIsNot(reduced.key_changes[0], draft.key_changes[0])

        records['metrics']['values']['metric_2'] = '99'
        reduced, ids = minimal_report(draft, review, lookup=records.__getitem__,
            claim_filter=lambda claim: claim.evidence_ids != ['event-5'])
        self.assertEqual(ids, ['O1', 'O3', 'K1', 'K2', 'K3', 'K4'])
        self.assertEqual(reduced.observations, [draft.observations[0], draft.observations[2]])
        self.assertEqual(reduced.key_changes, draft.key_changes[:4])

    def test_cap_leaves_room_for_events_even_when_many_observations_pass(self):
        draft, review = self.reviewed_report(
            observations=[{'statement': f'Observation {i}', 'evidence_ids': ['r']} for i in range(9)],
            events=[{'statement': f'Event {i}', 'evidence_ids': ['r']} for i in range(5)])
        reduced, ids = minimal_report(draft, review)
        self.assertEqual(ids, ['O1', 'O2', 'O3', 'O4', 'K1', 'K2', 'K3', 'K4'])
        self.assertEqual(reduced.observations, draft.observations[:4])
        self.assertEqual(reduced.key_changes, draft.key_changes[:4])

    def test_prepares_exact_unreviewed_subset_after_a_dated_claim_is_blocked(self):
        draft = ReportDraft(title='Draft headline', summary='Unresolved draft conclusion.',
            observations=[{'statement': 'An observation for audit.', 'evidence_ids': ['r']}],
            key_changes=[{'statement': 'A dated claim with a mismatched total.', 'evidence_ids': ['etf']},
                         {'statement': 'An event with missing evidence.', 'evidence_ids': ['missing']}],
            limitations=['Original draft commentary.'])
        kwargs = {'lookup': {'r': {'record_id': 'r'}, 'etf': {'record_id': 'etf'}}.__getitem__,
            'evidence_as_of': '2026-10-12T00:00:00Z',
            'claim_filter': lambda claim: claim.evidence_ids != ['etf']}
        prepared, ids = prepare_factual_minimal(draft, **kwargs)
        self.assertEqual(ids, ['O1'])
        self.assertEqual(prepared.delivery_kind, 'minimal')
        self.assertEqual(prepared.observations, draft.observations)
        self.assertFalse(prepared.key_changes)
        self.assertNotEqual(prepared.summary, draft.summary)
        self.assertEqual(minimal_report(draft, {'targets': review_targets(draft)}, **kwargs), (None, []))

    def test_retains_only_exact_chart_specs_that_project_from_current_raw_source(self):
        chart_id = 'live-' + 'a' * 24
        start = '2026-10-11T00:00:00Z'
        end = '2026-10-11T02:00:00Z'
        cutoff = '2026-10-12T00:00:00Z'
        points = [
            {'observed_at': f'2026-10-11T{hour:02d}:00:00Z',
             'period_end': f'2026-10-11T{hour+1:02d}:00:00Z', 'close_usd': str(2000 + hour)}
            for hour in range(2)
        ]
        raw = json.dumps([
            [int(datetime.fromisoformat(point['observed_at']).timestamp()), 1998, 2005, 1999, 2000 + hour, 10]
            for hour, point in enumerate(points)
        ]).encode()
        chart_source = {'record_id': chart_id, 'dataset_id': 'live.hourly_candles.eth',
            'metric': 'hourly_candles', 'provider': 'coinbase', 'instrument': 'ETH-USD',
            'quote_currency': 'USD', 'asset': 'ETH', 'schema_version': 'market-record-v1',
            'units': {'close_usd': 'USD'}, 'series': points, 'retrieved_at': cutoff,
            'source': {'name': 'Coinbase', 'url': 'https://example.org/candles', 'sha256': sha256(raw).hexdigest()}}
        records = {'r': {'record_id': 'r'}, chart_id: chart_source}
        charts = [{'evidence_id': chart_id, 'metric': key} for key in ('close_USD', 'close_usd', 'close_usd')]
        draft, review = self.reviewed_report(
            observations=[{'statement': 'A supported observation.', 'evidence_ids': ['r']}],
            events=[], scope={'assets': ['ETH'], 'start': start, 'end': end}, charts=charts)

        reduced, ids = minimal_report(draft, review, lookup=records.__getitem__,
            evidence_as_of=cutoff, raw_reader=lambda _: raw)
        self.assertEqual(ids, ['O1'])
        self.assertEqual(reduced.charts, [draft.charts[1]])
        self.assertIsNot(reduced.charts[0], draft.charts[1])
        self.assertEqual(reduced.scope, draft.scope)

        reduced, ids = minimal_report(draft, review, lookup=records.__getitem__,
            evidence_as_of=end, raw_reader=lambda _: raw)
        self.assertFalse(reduced.charts)
        self.assertEqual(ids, ['O1'])
        reduced, ids = minimal_report(draft, review, lookup=records.__getitem__,
            evidence_as_of=cutoff, raw_reader=lambda _: b'different bytes')
        self.assertFalse(reduced.charts)
        self.assertEqual(ids, ['O1'])

    def test_notice_cannot_carry_a_fact_and_receipt_is_bound(self):
        notice=evidence_gap_notice();receipt=notice_review(notice);verify_notice_review(receipt,notice)
        self.assertEqual(receipt['status'],'notice');self.assertEqual(receipt['claims_checked'],0)
        for field,value in [('summary','ETH will rise.'),('observations',[{'statement':'A fact','evidence_ids':['r']}])]:
            body=notice.model_dump();body[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):ReportDraft.model_validate(body)
        receipt['status']='reviewed'
        with self.assertRaises(ValueError):verify_notice_review(receipt,notice)

    def test_saved_market_fallback_requires_exact_window_and_last_close(self):
        from research_agent.reports import validate_report

        rid = 'live-' + 'a'*24
        start, end, cutoff = ('2026-11-15T00:00:00Z', '2026-11-18T00:00:00Z', '2026-11-18T09:00:00Z')
        points = [{'observed_at':f'2026-11-{day}T00:00:00Z',
                   'period_end':f'2026-11-{day+1}T00:00:00Z','close_usd':str(1800+day)}
                  for day in range(15,18)]
        raw = json.dumps([
            [int(datetime.fromisoformat(point['observed_at']).timestamp()), 1700, 1900, 1800, 1800+day, 10]
            for day, point in zip(range(15,18), points)
        ]).encode()
        canonical = {'record_id':rid, 'dataset_id':'live.candles.eth', 'metric':'candles',
            'provider':'coinbase', 'instrument':'ETH-USD', 'asset':'ETH', 'quote_currency':'USD',
            'schema_version':'market-record-v1', 'observed_at':end, 'retrieved_at':cutoff,
            'units':{'close_usd':'USD'}, 'values':{'close_usd':'1817'}, 'series':points,
            'source':{'name':'Coinbase', 'url':'https://api.exchange.coinbase.com/products/ETH-USD/candles',
                      'sha256':sha256(raw).hexdigest()}}
        knowledge = SimpleNamespace(market_captures=Mock(side_effect=lambda dataset_id,**_: {
            'records':{rid:canonical} if dataset_id=='live.candles.eth' else {}}))
        def fallback(row=canonical, raw=raw, since=start, until=end):
            return saved_market_fallback([since,until], ['ETH','BTC'], knowledge=knowledge,
                lookup=lambda _:row, evidence_as_of=cutoff, raw_reader=lambda _:raw)
        report = fallback()
        self.assertEqual(report.delivery_kind,'minimal')
        self.assertEqual(report.scope.assets,['ETH','BTC'])
        self.assertEqual([claim.value for claim in report.observations],['1817'])
        self.assertFalse(report.key_changes)
        self.assertEqual(validate_report(report,lambda _:canonical,as_of=cutoff,
                                         raw_reader=lambda _:raw)['chart_points_checked'],3)
        knowledge.market_captures.assert_any_call('live.candles.eth',as_of=cutoff,limit=8)
        self.assertIsNone(fallback({**canonical,'series':[points[0],points[2]]}))
        self.assertIsNone(fallback({**canonical,'values':{'close_usd':'1900'}}))
        self.assertIsNone(fallback({**canonical,'observed_at':cutoff}))
        self.assertIsNone(fallback({**canonical,'source':{**canonical['source'],'url':'https://example.org/candles'}}))
        self.assertIsNone(fallback(raw=b'changed raw response'))
        self.assertIsNone(fallback(since='2026-11-15T06:00:00Z'))

    def test_legacy_report_serialization_is_unchanged(self):
        draft,_=self.draft_and_review();self.assertNotIn('delivery_kind',draft.model_dump())
        with self.assertRaises(ValueError):ReportDraft(title='empty',summary='empty',observations=[],limitations=['gap'])

    def service(self,root):
        return ResearchTools(fixture_run(root/'project'),root/'workspace','2026-10-13T00:00:00Z')

    def test_no_approved_facts_publishes_notice_without_model_call(self):
        with TemporaryDirectory() as tmp:
            s=self.service(Path(tmp));s.reviewer=SimpleNamespace(calls=8,max_requests=8,settings=SimpleNamespace(model_id='fixture'))
            s.minimal_reviewer_factory=Mock(side_effect=AssertionError('No factual candidate'))
            result=s.finalize_minimal_delivery()
            self.assertEqual(result['delivery_kind'],'evidence_gap')
            self.assertEqual(result['review']['status'],'notice');self.assertEqual(len(s.published_reports),1)
            s.minimal_reviewer_factory.assert_not_called()
            with self.assertRaises(ValueError):s.finalize_minimal_delivery()

    def test_minimal_failure_uses_notice_once_and_never_approves_original(self):
        with TemporaryDirectory() as tmp:
            s=self.service(Path(tmp));draft,review=self.draft_and_review()
            s.last_report_review={'draft':draft.model_dump(),'review':review}
            s.lookup=lambda rid: {'record_id':rid}
            s.reviewer=SimpleNamespace(calls=8,max_requests=8,settings=SimpleNamespace(model_id='fixture'))
            last=SimpleNamespace(calls=2,max_requests=2)
            s.minimal_reviewer_factory=Mock(return_value=last)
            original=s.publish
            def submit(body):
                if body.delivery_kind=='minimal':raise ReviewBudgetExhausted('Final audit unavailable')
                return original(body)
            s.publish=submit
            result=s.finalize_minimal_delivery(draft=draft)
            self.assertEqual(result['delivery_kind'],'evidence_gap')
            self.assertEqual(s.delivery_metadata['normal_requests_used'],8)
            self.assertEqual(s.delivery_metadata['final_requests_used'],2)
            s.minimal_reviewer_factory.assert_called_once()

    def test_unreviewed_saved_draft_with_bad_advice_gets_a_fresh_factual_audit(self):
        fact = {'statement': 'The saved close was 20 USD.', 'metric': 'close',
                'value': '20', 'unit': 'USD', 'evidence_ids': ['price']}
        event = {'statement': 'The project announced its release date.', 'evidence_ids': ['release']}
        draft = ReportDraft(title='Weekly report', summary='Draft summary.',
            observations=[fact, {**fact, 'value': '99'}], key_changes=[event], limitations=['Draft limits.'],
            advice={'summary': 'Check the release notice.', 'rationale': [{**event, 'evidence_ids': ['page-typo']}],
                    'conditions': [event], 'invalidation': [event], 'review_triggers': ['Read the release notice.']})
        for has_earlier_review in (False, True):
            with self.subTest(has_earlier_review=has_earlier_review), TemporaryDirectory() as tmp:
                service = self.service(Path(tmp))
                records = {'price': {'record_id': 'price', 'values': {'close': 20}, 'units': {'close': 'USD'}},
                           'release': {'record_id': 'release', 'text': event['statement']}}
                service.lookup = records.__getitem__
                service.reviewer = SimpleNamespace(calls=0, max_requests=4, review=Mock())
                if has_earlier_review:
                    old, old_review = self.draft_and_review()
                    service.last_report_review = {'draft': old.model_dump(), 'review': old_review}
                with self.assertRaisesRegex(ValueError, '/advice/rationale/0/evidence_ids/0'):
                    service.publish(draft)
                service.reviewer.review.assert_not_called()

                fresh = Mock(return_value={'review_id': 'fixture-final', 'status': 'reviewed',
                                            'claims_checked': 2, 'reviewer_model': 'fixture'})
                service.minimal_reviewer_factory = lambda: SimpleNamespace(review=fresh, calls=1, max_requests=2)
                result = service.finalize_minimal_delivery()
                self.assertEqual(result['delivery_kind'], 'minimal')
                fresh.assert_called_once()
                candidate = fresh.call_args.args[0]
                self.assertEqual(candidate.observations, draft.observations[:1])
                self.assertEqual(candidate.key_changes, draft.key_changes)
                self.assertIsNone(candidate.advice)
                self.assertEqual(service.delivery_metadata['retained_target_ids'], [])
                self.assertEqual(service.delivery_metadata['unreviewed_targets_selected_for_final_audit'], ['O1', 'K1'])
                self.assertEqual(len(service.published_reports), 1)

    def test_checkpoint_is_used_only_when_the_later_draft_has_no_eligible_facts(self):
        window = ('2026-10-05T00:00:00Z', '2026-10-12T00:00:00Z')
        requested_scope = {'start': window[0], 'end': window[1], 'assets': ['ETH']}
        fact = {'statement': 'The recorded close was 20 USD.', 'evidence_ids': ['price']}
        event = {'statement': 'The project announced its release date.', 'evidence_ids': ['release']}
        records = {'price': {'record_id': 'price', 'text': fact['statement']},
                   'release': {'record_id': 'release', 'text': event['statement']}}
        for mode in ('unusable_latest', 'usable_latest', 'out_of_scope_checkpoint'):
            with self.subTest(mode=mode), TemporaryDirectory() as tmp:
                service = self.service(Path(tmp))
                service.required_report_window = window
                service.report_user_prompt = 'Write a weekly ETH report.'
                service.lookup = records.__getitem__
                checkpoint_scope = requested_scope
                if mode == 'out_of_scope_checkpoint':
                    checkpoint_scope = {**requested_scope, 'start': '2026-10-04T00:00:00Z'}
                checkpoint = ReportDraft(title='Early report', summary='Summary.', scope=checkpoint_scope,
                    observations=[fact], key_changes=[event], limitations=['Saved coverage only.'])
                service.last_report_checkpoint_id = service._save_report_draft(checkpoint)
                latest_fact = fact if mode == 'usable_latest' else {**fact, 'evidence_ids': ['unknown']}
                latest = ReportDraft(title='Later report', summary='Summary.', scope=requested_scope,
                    observations=[latest_fact], limitations=['Saved coverage only.'])
                latest_id = service._save_report_draft(latest)
                audit = Mock(return_value={'review_id': 'checkpoint-audit', 'reviewer_model': 'fixture',
                    'status': 'reviewed', 'claims_checked': 2})
                factory = Mock(return_value=SimpleNamespace(review=audit, calls=1, max_requests=2))
                service.minimal_reviewer_factory = factory

                result = service.finalize_minimal_delivery()

                self.assertEqual(service.delivery_metadata['original_draft_id'], latest_id)
                if mode == 'out_of_scope_checkpoint':
                    self.assertEqual(result['delivery_kind'], 'evidence_gap')
                    factory.assert_not_called()
                    continue
                self.assertEqual(result['delivery_kind'], 'minimal')
                factory.assert_called_once()
                audit.assert_called_once()
                reduced = audit.call_args.args[0]
                self.assertEqual(reduced.observations, checkpoint.observations)
                if mode == 'unusable_latest':
                    self.assertEqual(reduced.key_changes, checkpoint.key_changes)
                    self.assertEqual(service.delivery_metadata['checkpoint_draft_id'], service.last_report_checkpoint_id)
                else:
                    self.assertFalse(reduced.key_changes)
                    self.assertNotIn('checkpoint_draft_id', service.delivery_metadata)

    def test_mixed_final_verdict_can_spend_one_remaining_request_on_supported_subset(self):
        fact = {'statement': 'Saved fact one.', 'evidence_ids': ['source']}
        event = {'statement': 'Saved event.', 'evidence_ids': ['source']}
        draft = ReportDraft(title='Report', summary='Summary.',
            observations=[fact, {**fact,'statement':'Disputed statement.'}], key_changes=[event],
            limitations=['Coverage is limited.'])
        for provider, mode in [('generation_model','pass'), ('jev','pass'), ('generation_model','fail_again'),
                               ('jev','failed_batch'), ('generation_model','spent_budget')]:
            with self.subTest(provider=provider, mode=mode), TemporaryDirectory() as tmp:
                root = Path(tmp)
                service = self.service(root)
                service.lookup = lambda rid: {'record_id':rid, 'text':'Saved fact one. Saved event.'}
                budgets, submissions = [], []
                def factory(remaining=2):
                    budgets.append(remaining)
                    result = SimpleNamespace(calls=0, max_requests=remaining)
                    def review(current, lookup):
                        submissions.append(current)
                        result.calls += 2 if mode == 'spent_budget' else 1
                        targets = review_targets(current)
                        rejected = {'O2'} if len(submissions)==1 else {'O1'} if mode=='fail_again' else set()
                        verdicts = [{'claim_id': t['claim_id'],
                                     'status':'insufficient' if t['claim_id'] in rejected else 'supported',
                                     'scope_alignment':'aligned','reason':'Synthetic adjudication.',
                                     'quotes':[{'record_id':'source','text':t['statement']}]} for t in targets]
                        return {'review_id': 'synthetic-'+str(len(submissions)), 'reviewer_model': provider,
                                'reviewer_provider':provider, 'targets':targets, 'verdicts':verdicts,
                                'status':'needs_revision' if rejected else 'reviewed', 'claims_checked':len(targets),
                                'revision_required':[v for v in verdicts if v['claim_id'] in rejected],
                                'batches':[{'status':'invalid_or_failed' if mode=='failed_batch' else 'validated'}]}
                    result.review = review
                    return result
                service.minimal_reviewer_factory = factory
                saved = service.finalize_minimal_delivery(draft=draft)
                meta = json.loads((service.workspace/saved['structured_path'].lstrip('/')).read_text())['delivery']
                retry = mode in ('pass','fail_again')
                self.assertEqual(budgets, [2,1] if retry else [2])
                self.assertEqual(saved['delivery_kind'], 'minimal' if mode=='pass' else 'evidence_gap')
                self.assertEqual(meta['final_requests_used'], 1 if mode=='failed_batch' else 2)
                if retry:
                    self.assertEqual(submissions[1].observations, draft.observations[:1])
                    self.assertEqual(submissions[1].key_changes, draft.key_changes)
                    self.assertEqual(meta['refinement']['retained_target_ids'], ['O1','K1'])
                    self.assertEqual(meta['refinement']['source_review_id'], 'synthetic-1')
                    reviewed = service.workspace/'drafts'/(meta['refinement']['source_draft_id']+'.json')
                    self.assertEqual(json.loads(reviewed.read_text())['draft'], submissions[0].model_dump())

    def single_event_case(self, *, with_cards=False):
        disputed_source = 'page-' + '1' * 24 if with_cards else 'disputed-record'
        other_source = 'page-' + '2' * 24 if with_cards else 'other-record'
        cards = [{'evidence_id': rid, 'quote': 'A source excerpt.', 'relevance': {
            'statement': 'Primary document for this event.', 'evidence_ids': [rid]}}
            for rid in (disputed_source, other_source)] if with_cards else []
        draft = ReportDraft(title='This factual title has never been verified.', summary='Prices rose.',
            scope={'assets':['ETH'],'start':'2026-10-01T00:00:00Z','end':'2026-10-08T00:00:00Z'},
            observations=[{'statement':'Prices rose.','evidence_ids':[other_source]}],
            key_changes=[{'statement':'A vote closed.','evidence_ids':[disputed_source]},
                         {'statement':'A new proposal appeared.','evidence_ids':[other_source]}],
            questions_to_watch=['What happened when the vote closed?', 'When will the project update?',
                                'How will both decisions develop?'], documents=cards,
            limitations=['Future decisions have no known outcome.'])
        targets = review_targets(draft)
        verdicts = []
        for t in targets:
            sources = [disputed_source] if t['claim_id'] in ('K1','DOC1','M1') else [other_source]
            if t['claim_id'] == 'M3':
                sources.append(disputed_source)
            verdicts.append({'claim_id':t['claim_id'],'status':'supported','scope_alignment':'aligned',
                             'quotes':[{'record_id':rid,'text':t['statement']} for rid in sources]})
        return draft, {'targets':targets,'verdicts':verdicts,'status':'needs_revision',
                       'review_id':'fixture-full','review_stage':'full_review','final_prose_bound':True,
                       'revision_required':[{'claim_id':'K1','reason':'A result is not established.'}]}

    def test_only_full_and_exact_single_event_review_can_keep_other_prose(self):
        draft, review = self.single_event_case(with_cards=True)
        for provider in ('model', 'jev'):
            verified = deepcopy(review)
            if provider == 'jev':
                verified.update(reviewer_provider='jev', batches=[{'status':'validated'}])
            candidate, omission = omit_single_reviewed_event(draft, verified)
            self.assertEqual(candidate.key_changes,draft.key_changes[1:])
            self.assertEqual(candidate.documents,draft.documents[1:])
            self.assertEqual(candidate.questions_to_watch,draft.questions_to_watch[1:])
            self.assertEqual(candidate.limitations,draft.limitations)
            self.assertEqual(candidate.summary,draft.summary)
            self.assertEqual(candidate.scope,draft.scope)
            self.assertNotEqual(candidate.title,draft.title)
            self.assertEqual(omission['path'],'/key_changes/0')
            self.assertEqual([i['path'] for i in omission['dependent_items']],['/documents/0','/questions_to_watch/0'])
            self.assertEqual(set(omission['retained_original_review_target_ids']),
                             {t['claim_id'] for t in review['targets']} - {'K1','DOC1','M1'})
        for change in ('partial','missing_verdict','wrong_source','wrong_stage','other_issue','other_negative','failed_batch'):
            changed=deepcopy(review)
            if change=='partial':changed['unassessed_targets']=[{'claim_id':'S1'}]
            if change=='missing_verdict':changed['verdicts'].pop()
            if change=='wrong_source':changed['targets'][0]['evidence_ids']=['different-evidence']
            if change=='wrong_stage':changed['review_stage']='scope_critic'
            if change=='other_issue':changed['host_issues']=[{'claim_id':'S1','reason':'Incorrect summary.'}]
            if change=='other_negative':changed['verdicts'][0]['status']='insufficient'
            if change=='failed_batch':changed.update(reviewer_provider='jev', batches=[{'status':'invalid_or_failed'}])
            with self.subTest(change=change):
                self.assertIsNone(omit_single_reviewed_event(draft,changed))

    def test_event_reduction_uses_one_slot_or_falls_back_with_remaining_one(self):
        for reject_rich in (False, True):
            with self.subTest(reject_rich=reject_rich), TemporaryDirectory() as tmp:
                service=self.service(Path(tmp))
                service.report_user_prompt='Summarize the ETH research week.'
                service.lookup=lambda rid: {'record_id':rid,'text':'Prices rose. A new proposal appeared.'}
                draft, review=self.single_event_case()
                service.last_report_review={'draft':draft.model_dump(),'review':review}
                budgets, submissions=[], []
                def factory(remaining=2):
                    budgets.append(remaining)
                    reviewer=SimpleNamespace(calls=0,max_requests=remaining)
                    def audit(current,lookup):
                        submissions.append(current)
                        reviewer.calls += 1
                        if reject_rich and len(submissions)==1:
                            raise ValueError('A surviving source presupposition remains unverified.')
                        return {'review_id':'audited-subset','reviewer_model':'fixture','status':'reviewed',
                                'claims_checked':len(review_targets(current))}
                    reviewer.review=audit
                    return reviewer
                service.minimal_reviewer_factory=factory
                saved=service.finalize_minimal_delivery(draft=draft)
                bundle=json.loads((service.workspace/saved['structured_path'].lstrip('/')).read_text())
                self.assertEqual(saved['delivery_kind'],'minimal')
                self.assertEqual(set(bundle['delivery']['retained_target_ids']),{'O1','K2'})
                self.assertEqual(submissions[0].summary,draft.summary)
                self.assertEqual(submissions[0].questions_to_watch,draft.questions_to_watch[1:])
                self.assertEqual(submissions[0].key_changes,draft.key_changes[1:])
                self.assertEqual(bundle['delivery']['event_omission']['source_review_id'],'fixture-full')
                self.assertEqual(budgets,[1,1] if reject_rich else [1])
                self.assertEqual(bundle['delivery']['final_requests_used'],len(budgets))
                if reject_rich:
                    self.assertEqual(submissions[1].title,'Limited evidence report')
                    self.assertFalse(submissions[1].questions_to_watch)
                    self.assertEqual(bundle['delivery']['event_omission']['requests_used'],1)

    def test_stop_at_entry_or_final_commit_never_publishes_notice(self):
        for at_commit in (False,True):
            with TemporaryDirectory() as tmp:
                s=self.service(Path(tmp));s.cancel=threading.Event()
                if at_commit:
                    check=s._check_delivery_cancelled;calls=[]
                    def stop():
                        calls.append(1)
                        if len(calls)>=4:s.cancel.set()
                        check()
                    s._check_delivery_cancelled=stop
                else:s.cancel.set()
                with self.subTest(at_commit=at_commit),self.assertRaises(RuntimeError):s.publish(evidence_gap_notice())
                self.assertFalse(s.published_reports)


if __name__=='__main__':unittest.main()
