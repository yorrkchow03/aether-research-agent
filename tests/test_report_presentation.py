"""Report overview, exact chart projection and original-document publication gates."""

from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
import unittest
from html import escape

from pydantic import ValidationError

from research_agent.report_presentation import (
    build_report_presentation, document_review_targets, presentation_evidence_ids,
)
from research_agent.reports import ReportDraft, render_report, validate_report


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
        self.assertLess(markdown.index("## Report overview"), markdown.index("## Key data charts"))
        self.assertLess(markdown.index("## Key data charts"), markdown.index("## Important documents"))
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

    def test_legacy_report_loads_and_has_no_projection(self):
        payload = {key: value for key, value in self.payload.items() if key not in ("scope", "charts", "documents")}
        draft = self.draft(payload)
        self.assertIsNone(self.present(payload))
        self.assertNotIn("charts_checked", validate_report(draft, self.records.__getitem__))
        markdown = render_report(draft, END, {}, [])
        self.assertNotIn("## Report overview", markdown)
        self.assertIn("## Main facts", markdown)


if __name__ == "__main__":
    unittest.main()
