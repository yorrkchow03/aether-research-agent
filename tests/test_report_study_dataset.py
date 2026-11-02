"""Frozen report-study inputs: provenance, isolation and blind boundaries."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from research_agent.report_study_dataset import (
    FrozenReportCorpus, actual_records, case_payload, freeze_dataset, load_dataset,
    saved_source, text_sha, validate_dataset,
)


class ReportStudyDatasetTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.originals = [
            {"record_id": "page-" + "a" * 24, "title": "A proposed council change",
             "text": "A council proposes a change. It covers two modules. Further detail αβ remains in the complete saved text.",
             "published_at": "2026-05-01T00:00:00Z", "retrieved_at": "2026-10-27T00:00:00Z",
             "available_at": "2026-10-27T00:00:00Z", "source_kind": "forum_post",
             "raw_path": "/private/example/capture.json", "source": {"name": "Council", "url": "https://example.invalid/proposal", "sha256": "a" * 64}},
            {"record_id": "page-" + "b" * 24, "title": "A later discussion",
             "text": "A participant supports discussion. No execution is reported.",
             "published_at": "2026-05-02T00:00:00Z", "retrieved_at": "2026-10-27T00:00:00Z",
             "available_at": "2026-10-27T00:00:00Z", "source_kind": "forum_post",
             "source": {"name": "Council", "url": "https://example.invalid/reply", "sha256": "b" * 64}}]
        self.ids = [r["record_id"] for r in self.originals]
        self.sources = {self.ids[0]: saved_source(self.originals[0], [(0, self.originals[0]["text"].index("Further detail"))]),
                        self.ids[1]: saved_source(self.originals[1])}
        def point(pid, statement, rid, quote):
            return {"id": pid, "statement": statement,
                    "evidence": [{"source_id": rid, "quote": quote, "sha256": text_sha(quote)}]}
        self.case = {"case_id": "case-a", "split": "development", "event_family": "event-a",
                     "question": "What is proposed and what remains unknown?", "cutoff": "2026-10-28T23:59:59Z",
                     "language": "English", "source_ids": self.ids,
                     "research_context": {"availability_mode": "observed", "query_terms": ["council"]},
                     "expected": {"required_facts": [
                         point("f1", "A change is proposed.", self.ids[0], "proposes a change"),
                         point("f2", "It covers two modules.", self.ids[0], "two modules")],
                         "counter_evidence": [point("c1", "Support is not execution.", self.ids[1], "No execution is reported.")],
                         "forbidden_inferences": ["Execution has occurred."], "allowed_gaps": ["No receipt."]}}

    def freeze(self, name="dataset"):
        output = self.root / name
        freeze_dataset(output, sources=self.sources, cases=[self.case], protocol={"arms": ["off", "pro", "jev"]})
        return output

    def test_round_trip_and_blind_inputs_exclude_rubrics_and_split(self):
        output = self.freeze()
        loaded = load_dataset(output)
        self.assertEqual(len(loaded["cases"]), 1)
        self.assertEqual(set(loaded), {"manifest", "protocol", "sources", "cases"})
        payload = case_payload(loaded["cases"][0])
        self.assertNotIn("expected", payload)
        self.assertNotIn("split", payload)
        blind = json.loads((output / "blind_reviewer_input.json").read_text())
        for case in blind["cases"]:
            self.assertNotIn("expected", case)
            self.assertNotIn("split", case)
            self.assertEqual(case["research_records"], actual_records(self.case, self.sources))
        self.assertNotIn("raw_path", json.dumps(loaded["sources"]))
        self.assertIn("complete saved text", loaded["sources"][self.ids[0]]["original_record"]["text"])

    def test_actual_slices_bind_full_original_and_unicode_offsets(self):
        item = saved_source(self.originals[0], [(0, 9), (self.originals[0]["text"].index("αβ"), len(self.originals[0]["text"]))])
        self.assertIn("αβ", item["research_record"]["text"])
        damaged = deepcopy(self.sources)
        damaged[self.ids[0]]["source_spans"][0]["sha256"] = "wrong"
        with self.assertRaises(ValueError):
            validate_dataset(damaged, [self.case])
        damaged = deepcopy(self.sources)
        damaged[self.ids[0]]["original_record"]["text"] += "Changed."
        with self.assertRaises(ValueError):
            validate_dataset(damaged, [self.case])

    def test_rubric_quotes_must_exist_in_actual_model_context(self):
        damaged = deepcopy(self.case)
        q = damaged["expected"]["required_facts"][0]["evidence"][0]
        q["quote"] = "Further detail αβ"
        q["sha256"] = text_sha(q["quote"])
        with self.assertRaises(ValueError):
            validate_dataset(self.sources, [damaged])

    def test_publication_date_does_not_allow_earlier_system_knowledge(self):
        damaged = deepcopy(self.case)
        damaged["cutoff"] = "2026-05-03T00:00:00Z"
        with self.assertRaises(ValueError):
            validate_dataset(self.sources, [damaged])

    def test_observed_mode_cannot_silently_become_historical_mode(self):
        damaged = deepcopy(self.case)
        damaged["research_context"]["availability_mode"] = "published"
        with self.assertRaises(ValueError):
            validate_dataset(self.sources, [damaged])

    def test_source_or_event_family_cannot_cross_splits(self):
        other = deepcopy(self.case)
        other.update(case_id="case-b", split="confirmation", event_family="different-family")
        with self.assertRaises(ValueError):
            validate_dataset(self.sources, [self.case, other])
        source_map = {}
        extra = {}
        for rid, source in self.sources.items():
            new_id = "other-" + rid
            row = deepcopy(source["original_record"])
            row["record_id"] = new_id
            extra[new_id] = saved_source(row)
            source_map[rid] = new_id
        other["event_family"] = self.case["event_family"]
        other["source_ids"] = [source_map[rid] for rid in self.ids]
        for point in other["expected"]["required_facts"] + other["expected"]["counter_evidence"]:
            for q in point["evidence"]:
                q["source_id"] = source_map[q["source_id"]]
        with self.assertRaises(ValueError):
            validate_dataset({**self.sources, **extra}, [self.case, other])

    def test_overwrite_and_frozen_payload_tampering_are_rejected(self):
        output = self.freeze()
        with self.assertRaises(ValueError):
            freeze_dataset(output, sources=self.sources, cases=[self.case], protocol={})
        (output / "cases.json").write_text("[]\n")
        with self.assertRaises(ValueError):
            load_dataset(output)

    def test_unordered_or_overlapping_spans_are_rejected(self):
        damaged = deepcopy(self.sources)
        item = saved_source(self.originals[0], [(0, 35), (10, 49)])
        damaged[self.ids[0]] = item
        with self.assertRaises(ValueError):
            validate_dataset(damaged, [self.case])

    def test_read_only_query_has_case_scope_and_full_original_access(self):
        corpus = FrozenReportCorpus(self.freeze())
        self.assertEqual(corpus.task("case-a"), case_payload(self.case))
        self.assertEqual(len(corpus.source_pack("case-a")), 2)
        result = corpus.search("council", case_id="case-a")
        self.assertEqual(result[0]["record_id"], self.ids[0])
        self.assertIn("complete saved text", corpus.read(self.ids[0], original=True, case_id="case-a")["text"])
        with self.assertRaises(KeyError):
            corpus.read("not-in-pack", case_id="case-a")
        result[0]["text"] = "caller mutation"
        self.assertNotEqual(corpus.read(self.ids[0])["text"], "caller mutation")

    def test_incomplete_source_count_is_not_a_valid_report_case(self):
        damaged = deepcopy(self.case)
        damaged["source_ids"] = [self.ids[0]]
        with self.assertRaises(ValueError):
            validate_dataset(self.sources, [damaged])

    def test_actual_context_cannot_claim_a_different_raw_capture_hash(self):
        damaged = deepcopy(self.sources)
        damaged[self.ids[0]]["research_record"]["source"]["sha256"] = "wrong-capture"
        with self.assertRaises(ValueError):
            validate_dataset(damaged, [self.case])

    def test_saved_real_dataset_remains_frozen_and_has_twelve_distinct_cases(self):
        output = Path(__file__).resolve().parents[1] / "local/report-flow-study/v1/dataset"
        if not output.exists():
            self.skipTest("Private frozen public-source study bundle is not installed")
        data = load_dataset(output)
        self.assertEqual(data["manifest"]["split_counts"], {"development": 2, "confirmation": 10})
        self.assertEqual(len(data["cases"]), 12)
        self.assertEqual(len({c["event_family"] for c in data["cases"]}), 12)
        self.assertEqual(len(data["sources"]), 31)
        self.assertEqual(len(data["protocol"]["arms"]), 3)
        for case in data["cases"]:
            self.assertTrue(2 <= len(case["source_ids"]) <= 4)
            self.assertEqual(case["cutoff"], "2026-10-28T23:59:59Z")


if __name__ == "__main__":
    unittest.main()
