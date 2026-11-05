"""Freeze/routing/receipt failure contracts, with every provider mocked offline."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from research_agent import jev_nlp_study as study


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "examples/evaluation/jev_nlp_course_v2.json"


def response(provider="jev", status="ok", choices=None):
    return {"provider": provider, "model": study.MODELS[provider], "status": status,
            "dimensions": {name: {"choice": choice, "confidence": .99} for name, choice in (choices or {"support": "supported"}).items()},
            "latency_seconds": .1, "cache_hit": False, "cache_key": "cached-key",
            "billable_request_made": True, "physical_request_count": 1,
            "usage": {"input_tokens": 10, "output_tokens": 5}}


class FakeService:
    def __init__(self, provider="jev", *, result=None, cached=None):
        self.provider = provider; self.cache_only = False; self.calls = []
        self.result = deepcopy(result or response(provider)); self.cached = cached

    def assess(self, task, **kwargs):
        self.calls.append({"cache_only": self.cache_only, "task": task, **deepcopy(kwargs)})
        if self.cache_only:
            if self.cached:
                return {**deepcopy(self.cached), "cache_hit": True, "billable_request_made": False,
                        "physical_request_count": 0, "usage": None}
            return {"provider": self.provider, "status": "unavailable", "cache_hit": False,
                    "dimensions": {}, "billable_request_made": False, "physical_request_count": 0, "usage": None}
        return deepcopy(self.result)

    def receipt(self, cache_key):
        if not self.cached or cache_key != self.cached["cache_key"]:
            raise KeyError(cache_key)
        return {"result": deepcopy(self.cached)}


class StudyInputAndFreezeTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(DATASET.read_text())
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup); self.directory = Path(temp.name)

    def review(self, **updates):
        value = {"provider_outputs_seen": False, "dataset_sha256": study.digest(DATASET),
                 "reviewer": "offline unit-test fixture; not an actual independent annotation",
                 "cases": [{"case_id": case["case_id"], "labels": deepcopy(case["expected"])} for case in self.data["cases"]]}
        value.update(updates)
        path = self.directory / "review.json"; path.write_text(json.dumps(value)); return path

    def test_no_reference_annotation_reaches_provider_payload(self):
        case = self.data["cases"][0]; expected = study.payload(case, self.data)
        poisoned = deepcopy(case)
        poisoned.update(expected={"asset": ["LEAK"]}, label_rationale="SECRET", split="SECRET", event_family="SECRET")
        self.assertEqual(study.payload(poisoned, self.data), expected)
        self.assertEqual(set(expected), {"task", "question", "evidence"})
        for record in expected["evidence"]:
            self.assertNotIn("provenance", record)
            self.assertNotIn("expected", record)
            self.assertNotIn("assets", record)

    def test_context_pair_schedule_does_not_read_labeled_relation_edges(self):
        original = study.make_jobs(self.data)
        poisoned = deepcopy(self.data)
        for context in poisoned["context_sets"]:
            context["relations"] = [{"a": "NONEXISTENT", "b": "LEAK", "expected": ["duplicate"]}]
            context["required_retention"] = [{"source_key": "LEAK", "quote": "LEAK"}]
            context["duplicate_groups"] = [["LEAK", "SECRET"]]
        for case in poisoned["cases"]:
            case["expected"] = {key: ["LEAK"] for key in case["expected"]}
            case["label_rationale"] = "SECRET"
        self.assertEqual(original, study.make_jobs(poisoned))
        jobs, _, context_jobs = original
        self.assertLessEqual(len(jobs), 144)
        self.assertTrue(all(len(item["relations"]) <= 3 for item in context_jobs.values()))
        self.assertTrue(all(set(job["payload"]) == {"task", "question", "evidence"} for job in jobs.values()))

    def test_same_actual_request_cannot_cross_splits(self):
        damaged = deepcopy(self.data)
        second = deepcopy(damaged["cases"][0]); second.update(case_id="duplicate-in-test", split="confirmation")
        damaged["cases"].append(second)
        with self.assertRaisesRegex(ValueError, "crosses"):
            study.make_jobs(damaged)

    def test_freeze_is_offline_preserves_original_and_rejects_reuse_or_tampering(self):
        before = DATASET.read_bytes(); output = self.directory / "study"
        with patch.object(study, "EvidenceJudgmentService", side_effect=AssertionError("Unexpected API setup")):
            result = study.prepare(ROOT, DATASET, output, self.review())
            self.assertEqual(result["status"], "frozen")
            protocol, _ = study.load(ROOT, output)
            self.assertIn("scripts/evaluation/prepare_jev_nlp_course.py", protocol["implementation"])
            with self.assertRaisesRegex(ValueError, "new output"):
                study.prepare(ROOT, DATASET, output, self.review())
        self.assertEqual(DATASET.read_bytes(), before)
        saved = json.loads((output / "DATASET.json").read_text()); saved["cases"][0]["question"] += " tampered"
        (output / "DATASET.json").write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, "Frozen study file changed"):
            study.load(ROOT, output)

    def test_review_must_precede_outputs_identify_bytes_and_cover_every_case(self):
        bad_values = [{"provider_outputs_seen": True}, {"dataset_sha256": "wrong"}, {"cases": []}]
        for index, bad in enumerate(bad_values):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                study.prepare(ROOT, DATASET, self.directory / ("bad-" + str(index)), self.review(**bad))


class RoutingAndSubmissionTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup); self.directory = Path(temp.name)
        self.value = {"task": "support", "question": "A claim", "evidence": [{"record_id": "source", "text": "Actual evidence"}]}

    def test_context_relevance_hint_rejects_uncertain_instruction_risk_or_missing_fields(self):
        for provider in ("jev", "doubao", "rules"):
            original = {"provider": provider, "status": "ok", "dimensions": {
                "relevance": {"choice": "relevant", "confidence": .99,
                              "probabilities": {"relevant": .99, "background": .01}},
                "instruction_risk": {"choice": "absent", "confidence": .2}}}
            with self.subTest(provider=provider, reason="low_instruction_risk_confidence"):
                self.assertEqual(study.semantic_relevance_hint(original)["status"], "abstained")
            for missing in ("relevance", "instruction_risk"):
                incomplete = deepcopy(original)
                incomplete["dimensions"]["instruction_risk"]["confidence"] = .99
                incomplete["dimensions"].pop(missing)
                with self.subTest(provider=provider, missing=missing):
                    self.assertEqual(study.semantic_relevance_hint(incomplete)["status"], "abstained")

    def test_context_relevance_hint_accepts_complete_case_and_preserves_weighted_score(self):
        for provider in ("jev", "doubao", "rules"):
            result = {"provider": provider, "status": "ok", "dimensions": {
                "relevance": {"choice": "relevant", "confidence": .99,
                              "probabilities": {"relevant": .99, "background": .01,
                                                "irrelevant": 0, "uncertain": 0}},
                "instruction_risk": {"choice": "absent", "confidence": .8}}}
            with self.subTest(provider=provider):
                hint = study.semantic_relevance_hint(result, threshold=.8)
                self.assertEqual(hint["status"], "ok")
                self.assertEqual(hint["confidence"], .99)
                self.assertAlmostEqual(hint["score"], .99 + .35 * .01)
                self.assertEqual(study.semantic_relevance_hint(result, threshold=.9)["status"], "abstained")

    def test_routing_accepts_a_whole_complete_case_not_only_the_primary_label(self):
        calibration = {"thresholds": {"classification": .9, "support": .9}, "task_selective_direct_pro": ["support", "relation"]}
        pro = response("doubao")
        good = response(choices={"asset": "ETH", "topic": "security", "event_status": "occurred"})
        selected, route = study.routed("classification", good, pro, calibration)
        self.assertIs(selected, good); self.assertEqual(route, "jev")
        for mode in ("low", "missing", "uncertain", "abstained"):
            damaged = deepcopy(good)
            if mode == "low": damaged["dimensions"]["event_status"]["confidence"] = .8
            if mode == "missing": damaged["dimensions"].pop("topic")
            if mode == "uncertain": damaged["dimensions"]["event_status"]["choice"] = "uncertain"
            if mode == "abstained": damaged["status"] = "abstained"
            with self.subTest(mode=mode):
                self.assertEqual(study.routed("classification", damaged, pro, calibration)[1], "doubao")
        self.assertEqual(study.routed("support", response(), pro, calibration)[1], "doubao")
        self.assertEqual(study.routed("support", response(), pro, calibration, selective=False)[1], "jev")
        calibration["thresholds"]["classification"] = None
        self.assertEqual(study.routed("classification", good, pro, calibration)[1], "doubao")

    def test_fresh_submission_writes_marker_and_calls_once(self):
        service = FakeService(); marker = self.directory / "started.json"
        result = study._submit(service, self.value, marker)
        self.assertEqual(len(service.calls), 1)
        self.assertFalse(service.calls[0]["cache_only"])
        self.assertEqual(result["physical_request_count"], 1)
        self.assertEqual(json.loads(marker.read_text())["payload_sha256"], study._key(self.value))

    def test_interrupted_cache_miss_does_not_resubmit_and_keeps_unknown_cost(self):
        marker = self.directory / "started.json"; study.write_json(marker, {"started_at": "earlier"})
        service = FakeService(); result = study._submit(service, self.value, marker)
        self.assertEqual([c["cache_only"] for c in service.calls], [True])
        self.assertFalse(service.cache_only)
        self.assertTrue(result["interrupted_outcome"])
        self.assertTrue(result["billable_request_made"])
        self.assertIsNone(result["physical_request_count"])
        self.assertIsNone(result["usage"])
        ledger = study.accounting([result])
        self.assertEqual(ledger["unknown_physical_counts"], 1)
        self.assertEqual(ledger["unknown_input_usage_responses"], 1)
        self.assertIsNone(ledger["input_tokens"])
        self.assertIsNone(ledger["actual_cash_debit"])

    def test_interrupted_cached_response_restores_original_billable_receipt(self):
        marker = self.directory / "started.json"; study.write_json(marker, {"started_at": "earlier"})
        original = response(); service = FakeService(cached=original)
        result = study._submit(service, self.value, marker)
        self.assertEqual([c["cache_only"] for c in service.calls], [True])
        self.assertFalse(service.cache_only)
        self.assertTrue(result["recovered_from_local_receipt"])
        self.assertTrue(result["billable_request_made"])
        self.assertFalse(result["cache_hit"])
        self.assertEqual(result["usage"], original["usage"])
        self.assertEqual(study.accounting([result])["input_tokens"], 10)


class CollectionAndTimingTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup); self.directory = Path(temp.name)
        self.value = {"task": "support", "question": "A claim", "evidence": [{"record_id": "source", "text": "Actual evidence"}]}
        self.protocol = {"jobs": {str(i): {"payload": deepcopy(self.value)} for i in range(4)},
            "order": [{"job_id": str(i), "provider": arm, "split": "development"} for i in range(4) for arm in study.MODELS],
            "max_requests_per_provider": 160, "max_consecutive_provider_errors": 3, "jev_estimated_usd_stop": "0.25"}

    def test_one_provider_error_circuit_does_not_block_other_and_resume_does_not_repeat(self):
        services = {}
        def factory(config, cache, arm, **kwargs):
            service = FakeService(arm, result=response(arm, "error" if arm == "jev" else "ok")); services[arm] = service; return service
        with patch.object(study, "load", return_value=(self.protocol, {})), patch.object(study, "EvidenceJudgmentService", side_effect=factory):
            study.collect(ROOT, self.directory, self.directory, phase="development")
            self.assertEqual(len(services["jev"].calls), 3)
            self.assertEqual(len(services["doubao"].calls), 4)
            stopped = json.loads((self.directory / "first/jev/3.json").read_text())["result"]
            self.assertEqual(stopped["status"], "not_attempted")
            self.assertFalse(stopped["billable_request_made"])
            study.collect(ROOT, self.directory, self.directory, phase="development")
            self.assertFalse(services["jev"].calls)
            self.assertFalse(services["doubao"].calls)

    def test_saved_error_streak_survives_resume_before_a_pending_job(self):
        for index in range(3):
            study.write_json(self.directory / "first/jev" / (str(index) + ".json"), {"result": response("jev", "error")})
        services = {}
        def factory(config, cache, arm, **kwargs):
            service = FakeService(arm); services[arm] = service; return service
        with patch.object(study, "load", return_value=(self.protocol, {})), patch.object(study, "EvidenceJudgmentService", side_effect=factory):
            study.collect(ROOT, self.directory, self.directory, phase="development")
        self.assertEqual(len(services["jev"].calls), 0)
        self.assertEqual(len(services["doubao"].calls), 4)
        self.assertEqual(json.loads((self.directory / "first/jev/3.json").read_text())["result"]["status"], "not_attempted")

    def timing_fixture(self):
        data = {"sources": {"s": {"record": self.value["evidence"][0]}},
                "cases": [{"case_id": "c", "task": "support", "question": "A claim", "source_keys": ["s"]}]}
        protocol = {**self.protocol, "timing_case_ids": ["c"], "aliases": {"case:c": "0"}}
        for index in range(4):
            for arm in study.MODELS:
                study.write_json(self.directory / "first" / arm / (str(index) + ".json"), {"result": response(arm)})
        return protocol, data

    def test_timing_cannot_start_before_primary_collection_is_terminal(self):
        fixture = self.timing_fixture()
        (self.directory / "first/jev/0.json").unlink()
        with patch.object(study, "load", return_value=fixture), patch.object(study, "_calibration", return_value={}), patch.object(study, "EvidenceJudgmentService", side_effect=AssertionError("Unexpected provider setup")):
            with self.assertRaisesRegex(ValueError, "primary slot"):
                study.timing(ROOT, self.directory, self.directory)

    def test_failed_cold_timing_never_falls_through_to_a_second_paid_assessment(self):
        instances = []
        def factory(config, cache, arm, **kwargs):
            service = FakeService(arm, result=response(arm, "error")); instances.append(service); return service
        with patch.object(study, "load", return_value=self.timing_fixture()), patch.object(study, "_calibration", return_value={}), patch.object(study, "EvidenceJudgmentService", side_effect=factory):
            result = study.timing(ROOT, self.directory, self.directory)
        self.assertEqual(len(instances), 4)
        self.assertTrue(all([c["cache_only"] for c in s.calls] == [False] for s in instances))
        self.assertTrue(all(result[arm]["warm_cache_seconds"]["n"] == 0 for arm in study.MODELS))

    def test_warm_timing_is_explicitly_cache_only_and_does_not_hide_cold_cost(self):
        instances = []
        def factory(config, cache, arm, **kwargs):
            original = response(arm); service = FakeService(arm, result=original, cached=original); instances.append(service); return service
        with patch.object(study, "load", return_value=self.timing_fixture()), patch.object(study, "_calibration", return_value={}), patch.object(study, "EvidenceJudgmentService", side_effect=factory):
            result = study.timing(ROOT, self.directory, self.directory)
        self.assertTrue(all([c["cache_only"] for c in s.calls] == [False, True] for s in instances))
        for arm in study.MODELS:
            self.assertEqual(result[arm]["accounting_cold"]["physical_requests"], 2)
            self.assertEqual(result[arm]["accounting_warm"]["physical_requests"], 0)
            self.assertEqual(result[arm]["warm_cache_seconds"]["n"], 2)


if __name__ == "__main__":
    unittest.main()
