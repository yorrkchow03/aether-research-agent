import json
from pathlib import Path
import runpy
import tempfile
import unittest

from research_agent.monthly_report_benchmark import DiagnosticReport, digest, eligible, load, score


class MonthlyReportScoringTests(unittest.TestCase):
    def report(self, **overrides):
        body = dict(title='Diagnostic', summary='Retrospective source-only study.',
            observations=[{'statement': 'The recorded return is -10 percent.', 'evidence_ids': ['live-a'],
                           'metric': 'return_30d', 'value': '-0.10', 'unit': 'fraction'}],
            judgment='insufficient_evidence', judgment_reason='A past observation cannot establish a trading edge.',
            limitations=['Only the supplied snapshot is available.'], response_mode='answer')
        body.update(overrides)
        return DiagnosticReport.model_validate(body)

    def test_correct_citation_does_not_hide_wrong_number_or_unit(self):
        source = {'live-a': {'values': {'return_30d': '-0.10'}, 'units': {'return_30d': 'fraction'}}}
        case = {'case_id': 'market', 'expected_mode': 'answer', 'expected_numeric': [
            {'record_id': 'live-a', 'metric': 'return_30d', 'value': '-0.10', 'unit': 'fraction'}]}
        original = self.report()
        good = score(original, case, source)
        self.assertEqual((good['exact_numeric_claims'], good['required_metrics_reported']), (1, 1))
        for value, unit in [('-10', 'fraction'), ('-0.10', 'percent'), ('NaN', 'fraction')]:
            bad = original.model_copy(deep=True)
            bad.observations[0].value = value
            bad.observations[0].unit = unit
            result = score(bad, case, source)
            self.assertEqual(result['claims_with_eligible_citations'], 1)
            self.assertEqual(result['exact_numeric_claims'], 0)
            self.assertEqual(result['required_metrics_reported'], 0)

    def test_missing_requested_metrics_cannot_score_perfect_by_saying_less(self):
        case = {'case_id': 'market', 'expected_mode': 'answer', 'expected_numeric': [
            {'record_id': 'live-a', 'metric': 'return_30d', 'value': '-0.10', 'unit': 'fraction'}]}
        result = score(self.report(observations=[]), case, {})
        self.assertEqual(result['numeric_claims'], 0)
        self.assertEqual(result['required_metrics'], 1)
        self.assertEqual(result['required_metrics_reported'], 0)
        self.assertIsNone(result['semantic_correctness'])

    def test_strict_clock_uses_acquisition_not_older_publication(self):
        row = {'published_at': '2026-10-22T00:00:00Z', 'retrieved_at': '2026-10-27T00:00:00Z'}
        self.assertTrue(eligible(row, {}, '2026-10-26T00:00:00Z'))
        self.assertFalse(eligible(row, {'strict_known_by': '2026-10-26T00:00:00Z'}, '2026-10-26T00:00:00Z'))
        self.assertFalse(eligible({'published_at': '2026-10-27T00:00:00Z'}, {}, '2026-10-26T00:00:00Z'))

    def test_forecast_citation_cannot_escape_source_eligibility(self):
        report = self.report(observations=[], forecasts=[{'statement': 'Conditional scenario only.',
            'horizon_days': 7, 'assumptions': ['No newly adverse event.'],
            'supporting_evidence_ids': ['fabricated-id'], 'uncertainty': 'Unknown.'}])
        result = score(report, {'case_id': 'strict_time', 'expected_mode': 'insufficient_evidence', 'expected_numeric': []}, {})
        self.assertEqual(result['ineligible_citation_ids'], ['fabricated-id'])
        self.assertTrue(result['empty_context_no_factual_claims'])

    def test_frozen_source_or_label_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in [('protocol.json', {}), ('evidence.json', {}), ('labels.json', [])]:
                (root / name).write_text(json.dumps(value))
            (root / 'manifest.json').write_text(json.dumps({'files': {name: digest(root / name)
                for name in ('protocol.json', 'evidence.json', 'labels.json')}}))
            self.assertEqual(load(root)[1:], ({}, []))
            (root / 'labels.json').write_text('[{"expected_mode":"changed"}]')
            with self.assertRaisesRegex(ValueError, 'Frozen study file changed'):
                load(root)

    def test_prose_date_check_recognizes_dates_inside_source_iso_timestamps(self):
        namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] /
            'scripts/evaluation/review_monthly_report_baseline.py'))
        check = namespace['date_flags']
        context = {'evidence': [{'observed_at': '2026-10-25T00:00:00.000000Z'}]}
        self.assertEqual(check({'summary': 'Observed 2026-10-25.'}, context), [])
        self.assertEqual(check({'summary': 'Observed 2025-10-25.'}, context), ['2025-10-25'])


if __name__ == '__main__':
    unittest.main()
