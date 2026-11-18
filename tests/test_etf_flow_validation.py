import hashlib
import unittest

from research_agent.etf_flow_validation import DatedETFValidationError, check_etf_flow_statement
from research_agent.reports import ReportDraft, validate_report


_RID = 'page-farside'
_CAPTURE = b"""
<table>
<tr><th>Date</th><th>ETHA</th><th>FETH</th><th>Total</th></tr>
<tr><td>10 Nov 2026</td><td>1.1</td><td>(2.4)</td><td>(1.3)</td></tr>
<tr><td>11 Nov 2026</td><td>(129.8)</td><td>(15.0)</td><td>(144.8)</td></tr>
<tr><td>12 Nov 2026</td><td>(62.0)</td><td>(8.5)</td><td>(70.5)</td></tr>
<tr><td>13 Nov 2026</td><td>11.6</td><td>(9.3)</td><td>2.3</td></tr>
<tr><td>16 Nov 2026</td><td>(5.0)</td><td>(3.4)</td><td>(8.4)</td></tr>
<tr><td>17 Nov 2026</td><td>(27.6)</td><td>(1.5)</td><td>(29.1)</td></tr>
<tr><td>Total</td><td>(211.7)</td><td>(40.1)</td><td>(251.8)</td></tr>
</table>
"""
_WRONG = (
    "Farside's US Ethereum ETF flow table showed mixed dated moves during the week, including a row "
    "total of (144.8) for 12 November 2026 and another row total of (29.1) for 17 November 2026; "
    "the figures are fund-flow observations and do not establish causation."
)
_OBSERVATION = {
    'statement': 'Farside US Ethereum ETF daily outflows included $70.5 million on 12 November 2026.',
    'evidence_ids': [_RID],
}


class DatedETFFlowTest(unittest.TestCase):
    def setUp(self):
        self.records = {_RID: {
            'dataset_id': 'live.webpage',
            'record_id': _RID,
            # Text flattened or supplied for semantic review may contain both
            # numbers. Only the hash-bound publisher table establishes a row.
            'text': '12 November 2026 (144.8) (70.5)',
            'source': {
                'url': 'https://farside.co.uk/ethereum-etf-flow-all-data/',
                'source_id': _RID,
                'retrieved_at': '2026-11-18T08:23:14Z',
                'sha256': hashlib.sha256(_CAPTURE).hexdigest(),
            },
        }}

    def check(self, text, *, evidence_ids=(_RID,), raw=_CAPTURE):
        return check_etf_flow_statement(text, evidence_ids, self.records.__getitem__, raw_reader=lambda row: raw)

    def test_off_by_one_in_multi_date_report_is_blocked(self):
        with self.assertRaisesRegex(DatedETFValidationError, r'2026-11-12.*-70\.5.*-144\.8'):
            self.check(_WRONG)
        first_draft = (
            "Farside’s dated US Ethereum ETF-flow table showed a 11 November 2026 total row of (1.3) "
            "and a 12 November 2026 total row of (144.8), in US$ millions; "
            "individual fund rows for those dates included negatives and some positive values."
        )
        with self.assertRaisesRegex(DatedETFValidationError, r'2026-11-11.*2026-11-12'):
            self.check(first_draft)
        self.assertEqual(self.check(_WRONG.replace('(144.8)', '(70.5)')), 2)
        self.assertEqual(self.check(first_draft.replace('(144.8)', '(70.5)').replace('(1.3)', '(144.8)')), 2)

    def test_amount_sign_unit_and_trading_date(self):
        for statement in (
            'On November 12, 2026, Ethereum ETFs recorded net outflows of US$70.5 million.',
            'Ether ETF flows on 12 Nov. 2026 were −70.5 USDm.',
            'Ethereum ETF net flow on 2026-11-12 at 00:00 UTC was (USD 70,500,000).',
            'On 2026-11-13, Ethereum ETF net inflows were $2.3m.',
            'Farside 以太坊 ETF 2026年11月12日净流出 7050 万美元。',
        ):
            with self.subTest(statement=statement):
                self.assertEqual(self.check(statement), 1)
        for statement in (
            'Ethereum ETF net inflow on 12 November 2026 was US$70.5m.',
            'Ethereum ETF net outflow on 12 November 2026 was US$70.5.',
            'Ethereum ETF net outflow on 12 November 2026 was €70.5m.',
            'Ethereum ETF net outflow on 12 November 2026 was 70.5.',
            'Ethereum ETF net outflow on 12 November 2026 was US$62.0m.',
            'Ethereum ETF net outflow on 12 November 2025 was US$70.5m.',
        ):
            with self.subTest(statement=statement):
                with self.assertRaises(DatedETFValidationError):
                    self.check(statement)

    def test_dated_list_needs_unambiguous_pairs(self):
        for text in (
            'For 16–17 November 2026, Farside showed dated total ETF-flow rows of '
            '2.3 and (29.1), respectively, in US$ millions.',
            'Farside Ethereum ETF row totals on 11 November 2026 and 12 November 2026 were '
            '(144.8) and (70.5), respectively.',
            'Farside Ethereum ETF daily net outflow on 12 November was US$70.5m.',
        ):
            with self.subTest(text=text):
                with self.assertRaises(DatedETFValidationError):
                    self.check(text)

    def test_unrelated_values_do_not_compare_to_total(self):
        for statement in (
            'ETHA had US$62.0m in ETF net outflows on 12 November 2026.',
            'Farside reported a total row of (62.0) for ETHA on 12 November 2026.',
            'Bitcoin ETFs had net outflow of US$70.5m on 12 November 2026.',
            'Farside reported weekly Ethereum ETF net outflows of US$250.5m over 11–17 November 2026.',
            'On 12 November 2026, Ethereum ETFs posted net outflows.',
        ):
            with self.subTest(statement=statement):
                self.assertEqual(self.check(statement), 0)

    def test_requires_hash_bound_capture_or_exact_structured_row(self):
        with self.assertRaisesRegex(DatedETFValidationError, 'raw capture hash mismatch'):
            self.check(_OBSERVATION['statement'], raw=_CAPTURE.replace(b'(70.5)', b'(144.8)'))
        with self.assertRaisesRegex(DatedETFValidationError, 'cite the saved Farside'):
            self.check(_OBSERVATION['statement'], evidence_ids=())
        rid = 'row-farside-12'
        self.records[rid] = {
            'dataset_id': 'flows.farside.eth_etf.daily',
            'observed_at': '2026-11-12T00:00:00Z',
            'values': {'net_flow_usd': '-70500000'},
            'units': {'net_flow_usd': 'USD/trading day'},
        }
        self.assertEqual(self.check(_OBSERVATION['statement'], evidence_ids=(rid,)), 1)
        with self.assertRaisesRegex(DatedETFValidationError, r'no published Total.*2026-11-11'):
            self.check(_OBSERVATION['statement'].replace('12 November', '11 November'), evidence_ids=(rid,))

    def test_report_applies_check_to_claims_and_narrative_and_supports_legacy_replay(self):
        draft = ReportDraft(
            title='ETH weekly report', summary='Saved source review.', limitations=['Limited sample.'],
            observations=[_OBSERVATION], key_changes=[{'statement': _WRONG, 'evidence_ids': [_RID]}],
        )
        args = (self.records.__getitem__,)
        raw_reader = lambda row: _CAPTURE
        with self.assertRaisesRegex(DatedETFValidationError, '/key_changes/0'):
            validate_report(draft, *args, raw_reader=raw_reader)
        self.assertNotIn('farside_dated_totals_checked',
                         validate_report(draft, *args, raw_reader=raw_reader, check_etf_flows=False))
        draft.key_changes.clear()
        draft.summary = 'ETH closed at $1,822.15 on 12 November 2026. ' + _WRONG
        with self.assertRaisesRegex(DatedETFValidationError, r'/summary.*2026-11-12.*-144\.8'):
            validate_report(draft, *args, raw_reader=raw_reader)
        draft.summary = draft.summary.replace('(144.8)', '(70.5)')
        self.assertEqual(validate_report(draft, *args, raw_reader=raw_reader)['farside_dated_totals_checked'], 3)
