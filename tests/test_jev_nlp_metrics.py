from copy import deepcopy
import math
import unittest

from research_agent.jev_nlp_metrics import (
    NO_DECISION, compare_providers, evaluate_provider, matched_coverage_risk,
    operational_acceptance, paired_cluster_bootstrap, risk_coverage_curve,
)


def case(case_id, expected, *, task="support", family=None, **kwargs):
    return {"case_id": case_id, "task": task, "split": "confirmation",
            "event_family": family or case_id, "expected": expected, **kwargs}


def result(choices, *, status="ok", confidence=0.95):
    return {"status": status, "dimensions": {name: {"choice": choice, "confidence": confidence}
                                               for name, choice in choices.items()}}


class FixedDenominatorTests(unittest.TestCase):
    def test_errors_and_missing_responses_are_false_negatives_with_fixed_classes(self):
        cases = [case("a", {"support": ["supported"]}),
                 case("b", {"support": ["contradicted"]}),
                 case("c", {"support": ["insufficient"]})]
        predictions = {"a": result({"support": "supported"}),
                       "b": result({"support": "contradicted"}, status="error")}
        scored = evaluate_provider(cases, predictions)
        metrics = scored["raw"]["classification"]
        self.assertEqual(scored["planned_cases"], 3)
        self.assertEqual(scored["error_cases"], 1)
        self.assertEqual(scored["not_attempted_cases"], 1)
        self.assertEqual(metrics["single_label_dimensions"], 3)
        self.assertEqual(metrics["accuracy"], 1 / 3)
        self.assertEqual(metrics["macro_f1"], 1 / 3)
        self.assertEqual(metrics["no_decision_single_label_dimensions"], 2)
        self.assertEqual(metrics["per_class"]["support.support:contradicted"]["fn"], 1)
        self.assertEqual(metrics["per_class"]["support.support:insufficient"]["fn"], 1)
        self.assertIn(NO_DECISION, str(metrics["confusion"]))

    def test_ambiguous_and_preexcluded_labels_keep_acceptable_set_denominator(self):
        cases = [case("a", {"support": ["supported", "conditional"]}),
                 case("b", {"support": ["supported"]}, score_excluded_dimensions=["support"]),
                 case("c", {"support": ["contradicted"]})]
        predictions = {"a": result({"support": "conditional"}), "b": result({"support": "supported"})}
        scored = evaluate_provider(cases, predictions)
        self.assertEqual(scored["raw"]["acceptable_set_matches"], 2)
        self.assertEqual(scored["raw"]["acceptable_set_denominator"], 3)
        self.assertEqual(scored["raw"]["classification"]["single_label_dimensions"], 1)
        self.assertEqual(scored["raw"]["classification"]["excluded_from_single_label_dimensions"], 2)
        self.assertEqual(scored["raw"]["classification"]["macro_f1"], 0)

    def test_raw_abstained_fields_do_not_become_operational_successes(self):
        cases = [case("a", {"asset": ["ETH"], "event_status": ["planned"]}, task="classification")]
        prediction = result({"asset": "ETH", "event_status": "planned"}, status="abstained")
        prediction["dimensions"]["asset"]["confidence"] = 0.99
        scored = evaluate_provider(cases, {"a": prediction})
        self.assertEqual(scored["raw"]["acceptable_set_matches"], 2)
        self.assertEqual(scored["accepted_cases"], 0)
        self.assertEqual(scored["operational"]["acceptable_set_matches"], 0)
        self.assertEqual(scored["operational"]["classification"]["accuracy"], 0)
        self.assertEqual(scored["tasks"]["classification"]["planned_dimensions"], 2)
        self.assertEqual(scored["dimensions"]["classification.asset"]["planned_dimensions"], 1)

    def test_whole_case_rejects_low_confidence_missing_invalid_and_uncertain_fields(self):
        example = case("a", {"asset": ["ETH"], "event_status": ["planned"]}, task="classification")
        base = result({"asset": "ETH", "event_status": "planned"})
        self.assertTrue(operational_acceptance(example, base)["accepted"])
        for change in [0.79, None, math.nan, math.inf, True, 1.01]:
            with self.subTest(confidence=change):
                prediction = deepcopy(base)
                prediction["dimensions"]["event_status"]["confidence"] = change
                self.assertFalse(operational_acceptance(example, prediction)["accepted"])
        prediction = deepcopy(base)
        del prediction["dimensions"]["event_status"]
        self.assertFalse(operational_acceptance(example, prediction)["accepted"])
        prediction = result({"asset": "ETH", "event_status": "uncertain"})
        self.assertFalse(operational_acceptance(example, prediction)["accepted"])

    def test_impossible_choice_is_no_decision_and_reference_labels_do_not_route(self):
        a = case("a", {"support": ["supported"]})
        b = case("a", {"support": ["contradicted"]})
        prediction = result({"support": "supported"})
        self.assertEqual(operational_acceptance(a, prediction), operational_acceptance(b, prediction))
        invalid = evaluate_provider([a], {"a": result({"support": "fabricated_choice"})},
                                    label_options={"support.support": ["supported", "insufficient", "contradicted", "conditional"]})
        self.assertEqual(invalid["accepted_cases"], 0)
        self.assertEqual(invalid["raw"]["classification"]["no_decision_single_label_dimensions"], 1)
        self.assertEqual(invalid["raw"]["classification"]["per_class"]["support.support:supported"]["fn"], 1)

    def test_provider_class_inventory_is_identical_even_when_an_arm_errors(self):
        cases = [case("a", {"support": ["supported"]}), case("b", {"support": ["insufficient"]})]
        perfect = {"a": result({"support": "supported"}), "b": result({"support": "insufficient"})}
        scored = compare_providers(cases, {"good": perfect, "failed": {}}, bootstrap_samples=10)
        good = scored["providers"]["good"]["raw"]["classification"]
        failed = scored["providers"]["failed"]["raw"]["classification"]
        self.assertEqual(good["reference_supported_labels"], failed["reference_supported_labels"])
        self.assertEqual(good["macro_f1_class_count"], 2)
        self.assertEqual(good["macro_f1"], 1)
        self.assertEqual(failed["macro_f1"], 0)

    def test_empty_or_duplicate_cases_and_invalid_thresholds(self):
        scored = evaluate_provider([], {})
        self.assertIsNone(scored["raw"]["classification"]["accuracy"])
        self.assertIsNone(scored["selective_case_risk"])
        a = case("a", {"support": ["supported"]})
        with self.assertRaises(ValueError):
            evaluate_provider([a, a], {})
        for threshold in [-1, 1.1, math.nan, True]:
            with self.assertRaises(ValueError):
                evaluate_provider([a], {}, threshold=threshold)


class CriticalErrorTests(unittest.TestCase):
    def test_unsupported_detection_uses_explicit_reference_classes_and_failures(self):
        cases = [case("a", {"support": ["contradicted"]}),
                 case("b", {"support": ["insufficient"]}),
                 case("c", {"support": ["supported"]}),
                 case("d", {"support": ["supported"]}),
                 case("e", {"support": ["conditional"]}),
                 case("f", {"support": ["contradicted", "insufficient"]})]
        predictions = {"a": result({"support": "insufficient"}),
                       "b": result({"support": "supported"}),
                       "c": result({"support": "contradicted"}),
                       "d": result({"support": "supported"}),
                       "e": result({"support": "supported"})}
        metrics = evaluate_provider(cases, predictions)["raw"]["critical"]["unsupported_claim_detection"]
        self.assertEqual(metrics["eligible_dimensions"], 5)
        self.assertEqual((metrics["tp"], metrics["fp"], metrics["fn"], metrics["tn"]), (1, 1, 2, 1))
        self.assertEqual(metrics["precision"], 0.5)
        self.assertEqual(metrics["recall"], 1 / 3)
        self.assertEqual(metrics["false_supported"], 1)
        self.assertEqual(metrics["supported_precision"], 0.5)

    def test_status_overclaim_lost_counterevidence_and_injection_denominators(self):
        cases = [case("a", {"event_status": ["planned"]}, task="classification"),
                 case("b", {"relevance": ["relevant"], "instruction_risk": ["present"]},
                      task="relevance", flags=["counter_evidence"])]
        predictions = {"a": result({"event_status": "occurred"}),
                       "b": result({"relevance": "irrelevant", "instruction_risk": "absent"}, status="abstained")}
        scored = evaluate_provider(cases, predictions)
        raw, operational = scored["raw"]["critical"], scored["operational"]["critical"]
        self.assertEqual(raw["status_overclaims"], 1)
        self.assertEqual(raw["status_overclaim_opportunities"], 1)
        self.assertEqual(raw["counter_evidence_marked_irrelevant"], 1)
        self.assertEqual(raw["counter_evidence_opportunities"], 1)
        self.assertEqual(raw["prompt_injection_missed"], 1)
        self.assertEqual(operational["counter_evidence_marked_irrelevant"], 0)
        self.assertEqual(operational["counter_evidence_opportunities"], 1)
        self.assertEqual(operational["status_overclaims"], 1)


class CoverageAndUncertaintyTests(unittest.TestCase):
    def test_risk_curve_preserves_case_denominators_and_never_revives_abstention(self):
        cases = [case("a", {"support": ["supported"]}),
                 case("b", {"support": ["supported"]}),
                 case("c", {"support": ["supported"]})]
        predictions = {"a": result({"support": "supported"}, confidence=0.99),
                       "b": result({"support": "contradicted"}, confidence=0.85),
                       "c": result({"support": "supported"}, status="abstained", confidence=0.99)}
        points = risk_coverage_curve(cases, predictions, thresholds=(0.8, 0.9))["points"]
        self.assertEqual([p["planned_cases"] for p in points], [3, 3])
        self.assertEqual([p["accepted_cases"] for p in points], [2, 1])
        self.assertEqual([p["selective_case_risk"] for p in points], [0.5, 0])

    def test_matched_coverage_ranking_does_not_consult_reference_labels(self):
        cases = [case(str(i), {"support": ["supported"]}) for i in range(4)]
        left = {str(i): result({"support": "supported"}, confidence=0.99 - i * 0.02) for i in range(4)}
        right = deepcopy(left)
        right["1"]["status"] = "abstained"
        comparison = matched_coverage_risk(cases, {"left": left, "right": right})
        self.assertEqual(comparison["maximum_common_case_count"], 3)
        for point in comparison["points"]:
            self.assertEqual(point["providers"]["left"]["accepted_cases"], point["providers"]["right"]["accepted_cases"])
        changed = deepcopy(cases)
        for item in changed:
            item["expected"]["support"] = ["contradicted"]
        again = matched_coverage_risk(changed, {"left": left, "right": right})
        for point, other in zip(comparison["points"], again["points"]):
            for name in ["left", "right"]:
                self.assertEqual(point["providers"][name]["selected_case_ids"], other["providers"][name]["selected_case_ids"])

    def test_seeded_paired_cluster_bootstrap_counts_failures_and_is_reproducible(self):
        cases = [case(str(i), {"support": ["supported" if i % 2 else "insufficient"]}, family="family" + str(i)) for i in range(6)]
        left = {c["case_id"]: result({"support": c["expected"]["support"][0]}) for c in cases}
        right = {}
        scored = paired_cluster_bootstrap(cases, left, right, samples=80, seed=7)
        self.assertEqual(scored, paired_cluster_bootstrap(cases, left, right, samples=80, seed=7))
        self.assertEqual(scored["cluster_count"], 6)
        self.assertEqual(scored["completed_samples"], 80)
        self.assertEqual(scored["metrics"]["raw_single_label_accuracy"]["right_minus_left"], -1)
        self.assertEqual(scored["metrics"]["raw_single_label_accuracy"]["ci95"], [-1, -1])
        self.assertTrue(scored["sparse_reference_classes"])
        identical = paired_cluster_bootstrap(cases, left, left, samples=30)
        self.assertEqual(identical["metrics"]["raw_macro_f1"]["ci95"], [0, 0])

    def test_few_or_missing_family_ids_produce_no_case_level_interval(self):
        cases = [case(str(i), {"support": ["supported"]}, family="same-family") for i in range(6)]
        scored = paired_cluster_bootstrap(cases, {}, {}, samples=10)
        self.assertEqual(scored["status"], "insufficient_clusters")
        self.assertEqual(scored["completed_samples"], 0)
        self.assertIsNone(scored["metrics"]["raw_macro_f1"]["ci95"])
        del cases[0]["event_family"]
        self.assertIn("predeclared event_family", paired_cluster_bootstrap(cases, {}, {}, samples=10)["reason"])

    def test_scoring_does_not_mutate_inputs_or_turn_unknown_usage_into_zero(self):
        cases = [case("a", {"support": ["supported"]})]
        predictions = {"a": result({"support": "supported"}) | {"usage": None}}
        original = deepcopy(predictions)
        scored = compare_providers(cases, {"a": predictions}, bootstrap_samples=10)
        self.assertEqual(predictions, original)
        self.assertIsNone(predictions["a"]["usage"])
        self.assertNotIn("usage", scored["providers"]["a"])


if __name__ == "__main__":
    unittest.main()
