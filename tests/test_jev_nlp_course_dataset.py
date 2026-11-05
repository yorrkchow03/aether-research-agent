"""Portable source/provenance and split guards; no library credentials or APIs."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "examples/evaluation/jev_nlp_course_v2.json"
PREP = runpy.run_path(str(ROOT / "scripts/evaluation/prepare_jev_nlp_course.py"))


class CourseDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = PREP["load_dataset"](DATASET)

    def test_real_design_is_fresh_chronological_and_explicitly_not_human_gold(self):
        data = self.data
        self.assertEqual(len(data["cases"]), 48)
        families = {c["event_family"]: c["split"] for c in data["cases"]}
        self.assertEqual(len(families), 10)
        self.assertEqual(sum(s == "development" for s in families.values()), 4)
        self.assertEqual(sum(s == "confirmation" for s in families.values()), 6)
        self.assertEqual(data["protocol"]["human_labels"], 0)
        self.assertFalse(data["protocol"]["training"])
        self.assertGreaterEqual(data["exclusions"]["unique_prior_record_ids"], 81)
        self.assertNotIn("/Users/", DATASET.read_text())

    def test_every_classifier_gets_the_same_neutral_question(self):
        questions = {c["question"] for c in self.data["cases"] if c["task"] == "classification"}
        self.assertEqual(questions, {PREP["CLASSIFICATION_QUESTION"]})

    def test_exact_excerpt_parts_match_declared_character_lengths_and_hashes(self):
        for key, value in self.data["sources"].items():
            record, provenance = value["record"], value["provenance"]
            if provenance["input_kind"] != "saved_original_excerpt":
                continue
            with self.subTest(source=key):
                self.assertEqual(record["record_id"], provenance["original_record_id"])
                self.assertEqual(record["source"]["sha256"], provenance["raw_object_sha256"])
                parts = record["text"].split("\n[…]\n")
                self.assertEqual(len(parts), len(provenance["source_spans"]))
                for part, span in zip(parts, provenance["source_spans"]):
                    self.assertEqual(len(part), span["end"] - span["start"])
                    self.assertEqual(hashlib.sha256(part.encode()).hexdigest(), span["sha256"])
                self.assertNotIn("asset", record)
                self.assertNotIn("assets", record)

    def test_fixture_is_separate_identity_with_no_publisher_clocks(self):
        for key, value in self.data["sources"].items():
            record, provenance = value["record"], value["provenance"]
            if provenance["input_kind"] != "controlled_fixture":
                continue
            with self.subTest(source=key):
                self.assertTrue(record["record_id"].startswith("fixture-"))
                self.assertEqual(record["source_kind"], "controlled_test_fixture")
                self.assertEqual(provenance["source_spans"], [])
                self.assertTrue(all(record[t] is None for t in ("published_at", "retrieved_at", "available_at")))
                self.assertEqual(PREP["sha"](record["text"]), provenance["fixture_text_sha256"])
                self.assertEqual(record["source"]["sha256"], provenance["fixture_text_sha256"])

    def test_provider_and_context_payloads_ignore_poisoned_reference_annotations(self):
        case = deepcopy(self.data["cases"][0]); context = deepcopy(self.data["context_sets"][0])
        case_before = PREP["provider_payload"](case, self.data)
        context_before = PREP["context_payload"](context, self.data)
        case.update(expected={"asset": ["GOLD_LEAK"]}, label_rationale="SECRET", split="HIDDEN", counter_evidence=True)
        context.update(required_retention=[{"quote": "SECRET"}], relations=[{"expected": ["SECRET"]}], forbidden_inferences=["SECRET"])
        self.assertEqual(case_before, PREP["provider_payload"](case, self.data))
        self.assertEqual(context_before, PREP["context_payload"](context, self.data))
        self.assertEqual(set(case_before), {"task", "question", "evidence"})
        self.assertEqual(set(context_before), {"question", "evidence"})

    def test_mutations_cannot_masquerade_as_exact_source_provenance(self):
        changes = [
            ("triage", lambda s: s["provenance"]["source_spans"][0].update(sha256="0" * 64)),
            ("triage", lambda s: s["provenance"]["source_spans"][0].update(end=1)),
            ("triage", lambda s: s["record"]["source"].update(sha256="0" * 64)),
            ("triage_fixture_contrary", lambda s: s["record"].update(text="Changed fixture")),
            ("triage_fixture_contrary", lambda s: s["record"].update(published_at="2026-07-01T00:00:00Z")),
            ("triage_fixture_contrary", lambda s: s["provenance"].update(fixture_parent="stress")),
        ]
        for key, mutate in changes:
            damaged = deepcopy(self.data); mutate(damaged["sources"][key])
            with self.subTest(source=key), self.assertRaises(ValueError):
                PREP["validate"](damaged)

    def test_unknown_split_family_crossing_and_original_crossing_are_rejected(self):
        for kind in ("unknown_split", "family", "original", "context", "chronology"):
            damaged = deepcopy(self.data)
            if kind == "unknown_split":
                damaged["cases"][0]["split"] = "training"
            elif kind == "family":
                damaged["cases"][0]["split"] = "confirmation"
            elif kind == "original":
                case = next(c for c in damaged["cases"] if c["case_id"] == "solana_support")
                case["source_keys"] = ["triage"]
                case["question"] = "A different question does not make the original independent."
            elif kind == "context":
                damaged["context_sets"][0]["split"] = "confirmation"
            else:
                damaged["sources"]["triage"]["record"]["published_at"] = "2026-10-01T00:00:00Z"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                PREP["validate"](damaged)

    def test_duplicate_evidence_id_and_repeated_relation_input_are_rejected(self):
        damaged = deepcopy(self.data)
        damaged["sources"]["triage_fixture_copy"]["record"]["record_id"] = damaged["sources"]["triage"]["record"]["record_id"]
        with self.assertRaises(ValueError): PREP["validate"](damaged)
        damaged = deepcopy(self.data)
        case = next(c for c in damaged["cases"] if c["task"] == "relation")
        case["source_keys"] = [case["source_keys"][0]] * 2
        with self.assertRaises(ValueError): PREP["validate"](damaged)

    def test_context_targets_are_in_actual_inputs_and_budgets_are_prespecified(self):
        for context in self.data["context_sets"]:
            self.assertEqual(len(context["source_keys"]), 4)
            self.assertEqual(context["maximum_selected_records"], 3)
            self.assertEqual(context["context_budget_characters"], 12000)
            self.assertEqual(context["secondary_context_budget_characters"], 6400)
            for target in context["required_retention"]:
                self.assertIn(target["source_key"], context["source_keys"])
                self.assertIn(target["quote"], self.data["sources"][target["source_key"]]["record"]["text"])
        damaged = deepcopy(self.data)
        damaged["context_sets"][0]["required_retention"][0]["quote"] = "Fabricated required quote"
        with self.assertRaises(ValueError): PREP["validate"](damaged)

    def test_frozen_file_change_is_rejected_even_when_json_stays_valid(self):
        damaged = deepcopy(self.data); damaged["cases"][0]["expected"]["asset"] = ["BTC"]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "dataset.json"; path.write_text(json.dumps(damaged))
            with self.assertRaisesRegex(ValueError, "hash changed"):
                PREP["load_dataset"](path)


if __name__ == "__main__":
    unittest.main()
