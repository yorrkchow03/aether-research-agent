"""Anonymous result packets preserve failures and mechanically replay evidence."""
from copy import deepcopy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from research_data.http import dumps
from research_agent.report_study_dataset import freeze_dataset, saved_source, text_sha
from research_agent.report_study_review import ARMS, build_blind_review
from research_agent.reports import ReportDraft
from research_agent.review import evidence_text, review_targets


class ReportStudyReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.attempts = self.root / "attempts"
        self.attempts.mkdir()
        self.dataset = self.root / "dataset"
        self.ids = ["page-" + "a" * 24, "page-" + "b" * 24]
        self.raw = [b'{"source":"fixture one"}', b'{"source":"fixture two"}']
        self.originals = []
        for i, rid in enumerate(self.ids):
            self.originals.append({"record_id": rid, "title": "Saved public fixture " + str(i),
                "text": ["There are four units in this saved example. No execution is established.", "A second source asks for verification. No return is guaranteed."][i],
                "published_at": "2026-05-01T00:00:00Z", "retrieved_at": "2026-10-27T00:00:00Z",
                "available_at": "2026-10-27T00:00:00Z", "values": {"n": "4"}, "units": {"n": "count"},
                "source": {"name": "Public fixture", "url": "https://example.invalid/" + str(i),
                           "sha256": hashlib.sha256(self.raw[i]).hexdigest(), "source_id": "capture-" + str(i)}})
        def point(pid, rid, quote):
            return {"id": pid, "statement": quote, "evidence": [{"source_id": rid, "quote": quote, "sha256": text_sha(quote)}]}
        self.case = {"case_id": "report-dev-fixture", "split": "development", "event_family": "fixture-event",
            "question": "What does the saved evidence establish?", "cutoff": "2026-10-28T23:59:59Z", "language": "English",
            "source_ids": self.ids, "research_context": {"availability_mode": "observed"},
            "expected": {"required_facts": [point("f1", self.ids[0], "four units"), point("f2", self.ids[1], "asks for verification")],
                "counter_evidence": [point("c1", self.ids[1], "No return is guaranteed.")],
                "forbidden_inferences": ["Guaranteed profit"], "allowed_gaps": ["No outcome study"]}}
        freeze_dataset(self.dataset, sources={r["record_id"]: saved_source(r) for r in self.originals},
                       cases=[self.case], protocol={"arms": list(ARMS)})
        self.draft = {"title": "A source-grounded note", "summary": "The saved sources support a limited descriptive observation.",
                      "observations": [{"statement": "The source records four units.", "evidence_ids": [self.ids[0]], "metric": "n", "value": "4", "unit": "count"},
                                       {"statement": "The second source asks for verification.", "evidence_ids": [self.ids[1]]}],
                      "questions_to_watch": ["What additional execution evidence will become available?"],
                      "limitations": ["This is saved-source research rather than an outcome guarantee."]}

    def write(self, path, obj):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dumps(obj, indent=2, sort_keys=True))

    def store(self, attempt, row):
        db = attempt / "runtime/knowledge/knowledge.sqlite"
        db.parent.mkdir(parents=True, exist_ok=True)
        body = dumps(row, sort_keys=True)
        with closing(sqlite3.connect(db)) as c:
            with c:
                c.execute("CREATE TABLE IF NOT EXISTS evidence(record_id TEXT PRIMARY KEY,body TEXT,digest TEXT)")
                c.execute("INSERT OR REPLACE INTO evidence VALUES(?,?,?)", (row["record_id"], body, text_sha(body)))

    def make_attempt(self, arm=ARMS[0], *, published=True, state="completed", draft=None, name=None, extra_rows=None):
        attempt = self.attempts / (name or arm)
        receipt = {"case_id": self.case["case_id"], "arm": arm, "status": state,
                   "session_id": "fixture-session", "job_id": "fixture-job",
                   "dataset_manifest_sha256": hashlib.sha256((self.dataset / "MANIFEST.json").read_bytes()).hexdigest()}
        self.write(attempt / "ATTEMPT.json", receipt)
        workspace = attempt / "runtime/sessions/fixture-session/workspace"
        rows = {}
        for i, original in enumerate(self.originals):
            row = deepcopy(original)
            path = attempt / "runtime/knowledge/raw" / (row["source"]["sha256"] + ".bin")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.raw[i])
            row["raw_path"] = str(path)
            rows[row["record_id"]] = row
            self.store(attempt, row)
        for row in extra_rows or []:
            rows[row["record_id"]] = deepcopy(row)
            self.store(attempt, row)
        self.write(attempt / "RESULT.json", {"state": state, "published_reports": ["/reports/research-fixture.md"] if published else [],
                    "arm": arm, "provider": "doubao", "wall_seconds": 100})
        if published:
            self.write_report(attempt, workspace, rows, draft or self.draft)
        return attempt, workspace, rows

    def write_report(self, attempt, workspace, rows, draft, *, basename="research-fixture"):
        model = ReportDraft.model_validate(draft)
        targets = review_targets(model)
        texts, verdicts = {}, []
        for target in targets:
            rid = target["evidence_ids"][0]
            for source_id in target["evidence_ids"]:
                texts[source_id] = evidence_text(rows[source_id])
            quote = rows[rid].get("text") or rows[rid].get("method") or "Task scenario only."
            quote = quote[:100]
            verdicts.append({"claim_id": target["claim_id"], "status": "conditional" if target["kind"] == "research_action" else "supported",
                             "reason": "An existing AI judgment to replay, not independent truth.",
                             "quotes": [{"record_id": rid, "text": quote}]})
        review_id = "review-" + "c" * 24
        review = {"review_id": review_id, "reviewer_model": "doubao-seed-2.1-pro", "verdicts": verdicts,
                  "targets": targets, "evidence_text": texts}
        self.write(workspace / "reviews" / (review_id + ".json"), review)
        bundle = {"report": draft, "review": {k: v for k, v in review.items() if k not in ("targets", "evidence_text")},
                  "semantic_checks": {"provider": "jev", "arm": "jev_routed"}, "run_metadata": {"runtime": "/" + "Users/private/local"}}
        self.write(workspace / "reports" / (basename + ".json"), bundle)
        (workspace / "reports" / (basename + ".md")).write_text("Not used; structured draft content is the review input.")

    def build(self, name="review", seed=1729):
        output = self.root / name
        manifest = build_blind_review(self.dataset, self.attempts, output, seed=seed)
        return manifest, json.loads((output / "review_input.json").read_text()), json.loads((output / "AUTOMATIC_CHECKS.json").read_text()), output

    def published_audit(self, packet, audits):
        row = next(r for c in packet["cases"] for r in c["reports"] if r["publication_status"] == "published")
        return row, audits["reports"][row["anonymous_report_id"]]

    def test_three_arm_packet_is_anonymous_deterministic_and_rubric_free(self):
        for arm in ARMS:
            self.make_attempt(arm)
        manifest, packet, audits, output = self.build()
        text = json.dumps(packet)
        for forbidden in [*ARMS, "doubao", "jev", "semantic_checks", "reviewer_model", "run_metadata", "expected", "/Users/"]:
            self.assertNotIn(forbidden, text)
        self.assertEqual(manifest["planned_case_arm_slots"], 3)
        self.assertEqual(manifest["published_report_rows"], 3)
        self.assertTrue(all(row["status"] == "passed" for row in audits["reports"].values()))
        self.assertNotIn("report-dev-fixture", text)
        self.assertIn("report-dev-fixture", (output / "HOST_MAPPING.json").read_text())
        _, other, _, _ = self.build("review-duplicate")
        self.assertEqual(packet, other)

    def test_failure_and_unattempted_arms_keep_null_rows_in_denominator(self):
        self.make_attempt(published=False, state="failed")
        manifest, packet, audits, _ = self.build()
        self.assertEqual(manifest["registered_attempts"], 1)
        self.assertEqual(manifest["planned_case_arm_slots"], 3)
        self.assertEqual(manifest["failed_or_unattempted_rows"], 3)
        self.assertEqual(len(packet["cases"][0]["reports"]), 3)
        self.assertTrue(all(row["content"] is None for row in packet["cases"][0]["reports"]))
        self.assertEqual(audits["denominators"]["published_report_rows"], 0)

    def test_mechanical_checks_do_not_score_semantic_truth(self):
        draft = deepcopy(self.draft)
        draft["observations"][0]["statement"] = "The price will certainly double; this is an unsupported semantic claim."
        self.make_attempt(draft=draft)
        _, packet, audits, _ = self.build()
        row, checked = self.published_audit(packet, audits)
        self.assertIn("certainly double", row["content"]["observations"][0]["statement"])
        self.assertEqual(checked["status"], "passed")
        self.assertFalse(checked["semantic_gold_scored"])
        self.assertFalse(checked["prose_numeric_truth_checked"])

    def test_structured_numeric_mismatch_fails_without_rewriting_content(self):
        draft = deepcopy(self.draft)
        draft["observations"][0]["value"] = "5"
        self.make_attempt(draft=draft)
        _, packet, audits, _ = self.build()
        row, checked = self.published_audit(packet, audits)
        self.assertEqual(row["content"]["observations"][0]["value"], "5")
        self.assertEqual(checked["structured_validation"]["status"], "failed")

    def test_changed_actual_original_and_raw_response_fail_hash_audit(self):
        attempt, workspace, rows = self.make_attempt()
        row = rows[self.ids[0]]
        Path(row["raw_path"]).write_bytes(b"changed raw")
        _, packet, audits, _ = self.build()
        _, checked = self.published_audit(packet, audits)
        self.assertEqual(checked["source_leaf_whitelist"]["status"], "failed")
        self.assertIn("raw_capture", checked["evidence_identity_and_lineage"][self.ids[0]]["error"])

    def test_saved_review_quote_is_replayed_with_exact_original_offsets(self):
        self.make_attempt()
        _, packet, audits, _ = self.build()
        _, checked = self.published_audit(packet, audits)
        self.assertEqual(checked["mandatory_review_replay"]["status"], "passed")
        for quote in checked["quote_replay"]:
            self.assertEqual(text_sha(quote["text"]), quote["quote_sha256"])
            locations = quote["locations"]
            self.assertTrue(locations)
            original = next(r for r in self.originals if r["record_id"] == quote["record_id"])
            for location in locations:
                if location["representation"] == "original_string_leaf":
                    leaf = original
                    for key in location["field"]:
                        leaf = leaf[key]
                    self.assertEqual(leaf[location["start"]:location["end"]], quote["text"])

    def test_invented_saved_review_quote_fails_even_if_both_copies_match(self):
        attempt, workspace, _ = self.make_attempt()
        path = workspace / "reports/research-fixture.json"
        bundle = json.loads(path.read_text())
        bundle["review"]["verdicts"][0]["quotes"][0]["text"] = "An invented quote absent from every source."
        self.write(path, bundle)
        review_path = workspace / "reviews" / (bundle["review"]["review_id"] + ".json")
        review = json.loads(review_path.read_text())
        review["verdicts"] = bundle["review"]["verdicts"]
        self.write(review_path, review)
        _, packet, audits, _ = self.build()
        _, checked = self.published_audit(packet, audits)
        self.assertEqual(checked["mandatory_review_replay"]["status"], "failed")

    def test_outside_source_id_is_not_accepted_by_prefix(self):
        outsider = deepcopy(self.originals[0])
        outsider["record_id"] = "page-" + "d" * 24
        draft = deepcopy(self.draft)
        draft["observations"][0]["evidence_ids"] = [outsider["record_id"]]
        self.make_attempt(draft=draft, extra_rows=[outsider])
        _, packet, audits, _ = self.build()
        _, checked = self.published_audit(packet, audits)
        self.assertEqual(checked["source_leaf_whitelist"]["status"], "failed")
        self.assertIn("outside_case_whitelist", checked["evidence_identity_and_lineage"][outsider["record_id"]]["error"])

    def test_current_task_context_is_separate_from_external_sources(self):
        attempt, workspace, rows = self.make_attempt()
        profile = {"horizon_days": 30, "budget_usd": None}
        rid = "context-" + text_sha(dumps({"profile": profile, "cutoff": self.case["cutoff"], "job": "fixture-job"}, sort_keys=True))[:24]
        context = {"record_id": rid, "dataset_id": "local.user_research_parameters", "profile": profile,
                   "observed_at": self.case["cutoff"], "text": "Task scenario only.", "values": {}, "units": {}}
        self.store(attempt, context)
        self.write(workspace / "data/context" / (rid + ".json"), context)
        rows[rid] = context
        draft = deepcopy(self.draft)
        draft["interpretations"] = [{"statement": "The task scenario does not establish actual holdings.", "evidence_ids": [rid]}]
        self.write_report(attempt, workspace, rows, draft)
        _, packet, audits, _ = self.build()
        row, checked = self.published_audit(packet, audits)
        self.assertEqual(checked["status"], "passed")
        self.assertEqual(checked["source_leaf_whitelist"]["context_records"], [rid])
        self.assertEqual(row["derived_context_records"][0]["kind"], "user_scenario_not_external_evidence")

    def test_real_derived_record_uses_leaf_whitelist_and_recipe_hash_without_execution(self):
        attempt, workspace, rows = self.make_attempt()
        rid = "live-" + "e" * 24
        body = b'raise RuntimeError("This audit must never execute saved code")\n'
        recipe = workspace / "data/calculations" / (rid + ".py")
        recipe.parent.mkdir(parents=True, exist_ok=True)
        recipe.write_bytes(body)
        derived = {"record_id": rid, "dataset_id": "live.calculation", "input_record_ids": [self.ids[0]],
                   "recipe_path": str(recipe), "recipe_sha256": hashlib.sha256(body).hexdigest(),
                   "method": "Double a source field as a declared derived calculation.", "values": {"n": "8"}, "units": {"n": "count"}}
        self.store(attempt, derived)
        self.write(workspace / "data/live" / (rid + ".json"), derived)
        rows[rid] = derived
        draft = deepcopy(self.draft)
        draft["observations"][0].update(evidence_ids=[rid], value="8")
        self.write_report(attempt, workspace, rows, draft)
        _, packet, audits, _ = self.build()
        row, checked = self.published_audit(packet, audits)
        self.assertEqual(checked["status"], "passed")
        self.assertEqual(checked["source_leaf_whitelist"]["derived_records"], [rid])
        self.assertEqual(checked["evidence_identity_and_lineage"][rid]["source_leaf_ids"], [self.ids[0]])
        self.assertNotIn("recipe_path", json.dumps(row))

    def test_no_overwrite_no_private_path_and_no_cross_dataset_receipts(self):
        draft = deepcopy(self.draft)
        draft["summary"] = "The author saved a file at /" + "Users/private/person/report.json before writing this note."
        attempt, _, _ = self.make_attempt(draft=draft)
        _, packet, _, output = self.build()
        self.assertNotIn("/Users/", json.dumps(packet))
        with self.assertRaises(ValueError):
            build_blind_review(self.dataset, self.attempts, output)
        receipt = json.loads((attempt / "ATTEMPT.json").read_text())
        receipt["dataset_manifest_sha256"] = "wrong"
        self.write(attempt / "ATTEMPT.json", receipt)
        with self.assertRaises(ValueError):
            self.build("other")

    def test_repeated_attempts_are_retained_instead_of_cherry_picking_success(self):
        self.make_attempt(published=False, state="failed", name="first-failed")
        self.make_attempt(published=True, name="retry-published")
        manifest, packet, _, _ = self.build()
        self.assertEqual(manifest["registered_attempts"], 2)
        self.assertEqual(manifest["planned_case_arm_slots"], 3)
        self.assertEqual(len(packet["cases"][0]["reports"]), 4)
        self.assertEqual(manifest["published_report_rows"], 1)

    def test_running_attempt_is_not_frozen_as_a_failed_report(self):
        self.make_attempt(published=False, state="running")
        with self.assertRaises(ValueError):
            self.build()

    def test_derived_lineage_cycle_is_rejected(self):
        attempt, workspace, rows = self.make_attempt()
        ids = ["live-" + "e" * 24, "live-" + "f" * 24]
        for rid, parent in zip(ids, reversed(ids)):
            row = {"record_id": rid, "dataset_id": "live.calculation", "input_record_ids": [parent],
                   "method": "Circular purported derivation.", "values": {"n": "8"}, "units": {"n": "count"}}
            self.store(attempt, row)
            rows[rid] = row
        draft = deepcopy(self.draft)
        draft["observations"][0].update(evidence_ids=[ids[0]], value="8")
        self.write_report(attempt, workspace, rows, draft)
        _, packet, audits, _ = self.build()
        _, checked = self.published_audit(packet, audits)
        self.assertEqual(checked["status"], "failed")
        self.assertTrue(any("lineage_cycle" in v.get("error", "") for v in checked["evidence_identity_and_lineage"].values()))


if __name__ == "__main__":
    unittest.main()
