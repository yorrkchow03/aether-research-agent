"""Offline, denominator-preserving metrics for typed evidence judgments.

References are used only for scoring. Acceptance uses the requested dimension
names, provider status, choices and confidences; it never reads reference labels.
Raw agreement deliberately exposes choices from abstained responses. Operational
agreement requires the entire requested case to pass, not individual fields of
an abstained multi-question call. This module performs no I/O or provider calls.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
import math
import random


NO_DECISION = "__no_decision__"
_FAILED_STATUSES = {"error", "unavailable", "not_attempted"}
_STATUSES = {"ok", "abstained", *_FAILED_STATUSES}
_UNSUPPORTED = {"contradicted", "insufficient"}


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_threshold(threshold):
    if not _finite_number(threshold) or not 0 <= threshold <= 1:
        raise ValueError("A confidence threshold must be finite and between zero and one")


def _dimensions(case):
    """Only dimension names are needed by the acceptance policy."""
    return tuple(case["expected"].keys())


def _answer(result, dimension):
    dimensions = result.get("dimensions")
    if not isinstance(dimensions, dict):
        return {}
    answer = dimensions.get(dimension)
    return answer if isinstance(answer, dict) else {}


def _choice(result, dimension, group, label_options):
    if result.get("status", "not_attempted") in _FAILED_STATUSES:
        return NO_DECISION
    if result.get("status") not in _STATUSES:
        return NO_DECISION
    answer = _answer(result, dimension)
    choice = answer.get("choice")
    if not isinstance(choice, str) or not choice or choice == NO_DECISION:
        return NO_DECISION
    options = (label_options or {}).get(group)
    if options is not None and choice not in options:
        return NO_DECISION
    return choice


def operational_acceptance(case, result, *, threshold=0.8, label_options=None):
    """Return the strict whole-case decision, without consulting label values.

    ``label_options`` optionally maps ``task.dimension`` to its complete allowed
    vocabulary, declared in the protocol (not inferred from provider outputs).
    The service's abstained/error status cannot be reversed by lowering a local
    threshold. Curves therefore describe additional filtering of saved results.
    """
    _validate_threshold(threshold)
    if result.get("status") != "ok":
        return {"accepted": False, "confidence": None, "reason": result.get("status", "not_attempted")}
    confidences = []
    for dimension in _dimensions(case):
        answer = _answer(result, dimension)
        choice = _choice(result, dimension, case["task"] + "." + dimension, label_options)
        confidence = answer.get("confidence")
        if choice in {NO_DECISION, "uncertain"}:
            return {"accepted": False, "confidence": None, "reason": "missing_invalid_or_uncertain_choice"}
        if not _finite_number(confidence) or not 0 <= confidence <= 1:
            return {"accepted": False, "confidence": None, "reason": "invalid_confidence"}
        confidences.append(confidence)
    if not confidences:
        return {"accepted": False, "confidence": None, "reason": "no_requested_dimensions"}
    confidence = min(confidences)
    return {"accepted": confidence >= threshold, "confidence": confidence,
            "reason": "accepted" if confidence >= threshold else "below_threshold"}


def _prepare(cases, results, threshold, label_options):
    _validate_threshold(threshold)
    if len({c["case_id"] for c in cases}) != len(cases):
        raise ValueError("Case IDs must be unique")
    units, case_rows = [], []
    for case in cases:
        if not case.get("expected"):
            raise ValueError("Every case requires at least one nonempty reference dimension")
        result = results.get(case["case_id"]) or {"status": "not_attempted"}
        accepted = operational_acceptance(case, result, threshold=threshold, label_options=label_options)
        row_units = []
        for dimension, labels in case["expected"].items():
            if (not isinstance(labels, (list, tuple, set, frozenset)) or not labels or
                    not all(isinstance(label, str) and label and label != NO_DECISION for label in labels)):
                raise ValueError("Reference labels must be nonempty strings")
            expected = frozenset(labels)
            group = case["task"] + "." + dimension
            prediction = _choice(result, dimension, group, label_options)
            unit = {"case_id": case["case_id"], "task": case["task"], "dimension": dimension,
                    "group": group, "expected": expected, "raw": prediction,
                    "operational": prediction if accepted["accepted"] else NO_DECISION,
                    "single_label": len(expected) == 1 and dimension not in case.get("score_excluded_dimensions", []),
                    "event_family": case.get("event_family"),
                    "counter_evidence": bool(case.get("counter_evidence") or
                        {"counter_evidence", "counter-evidence", "counterevidence"}.intersection(case.get("flags", [])))}
            units.append(unit)
            row_units.append(unit)
        case_rows.append({"case_id": case["case_id"], "task": case["task"],
                          "event_family": case.get("event_family"),
                          "status": result.get("status", "not_attempted") if result.get("status", "not_attempted") in _STATUSES else "error",
                          **accepted,
                          "raw_exact_match": all(u["raw"] in u["expected"] for u in row_units),
                          "operational_exact_match": accepted["accepted"] and all(u["raw"] in u["expected"] for u in row_units)})
    # Class support is fixed from references, never from either arm's predictions.
    labels = defaultdict(set)
    for unit in units:
        if unit["single_label"]:
            labels[unit["group"]].update(unit["expected"])
    return units, case_rows, {group: sorted(value) for group, value in labels.items()}


def _classification(units, labels, field):
    single = [u for u in units if u["single_label"]]
    confusion = defaultdict(Counter)
    per_class = {}
    for unit in single:
        gold = next(iter(unit["expected"]))
        confusion[unit["group"]][(gold, unit[field])] += 1
    for group, group_labels in sorted(labels.items()):
        counts = confusion[group]
        for label in group_labels:
            tp = counts[(label, label)]
            fp = sum(n for (gold, prediction), n in counts.items() if prediction == label and gold != label)
            fn = sum(n for (gold, prediction), n in counts.items() if gold == label and prediction != label)
            support = sum(n for (gold, _), n in counts.items() if gold == label)
            per_class[group + ":" + label] = {"support": support, "tp": tp, "fp": fp, "fn": fn,
                "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn),
                "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0}
    matching = sum(u[field] in u["expected"] for u in single)
    return {"planned_dimensions": len(units), "single_label_dimensions": len(single),
            "excluded_from_single_label_dimensions": len(units) - len(single),
            "matching_single_label_dimensions": matching, "accuracy": _ratio(matching, len(single)),
            "macro_f1": sum(c["f1"] for c in per_class.values()) / len(per_class) if per_class else None,
            "macro_f1_class_count": len(per_class), "reference_supported_labels": labels,
            "per_class": per_class,
            "no_decision_single_label_dimensions": sum(u[field] == NO_DECISION for u in single),
            "confusion": {group: [{"reference": gold, "prediction": pred, "count": n}
                                     for (gold, pred), n in sorted(counts.items())]
                          for group, counts in sorted(confusion.items())},
            "definition": "Fixed planned singleton denominator; no_decision contributes false negatives. Macro averages reference-supported task/dimension classes; zero division F1 is zero."}


def _critical(units, field):
    support = [u for u in units if u["dimension"] == "support" and
               (u["expected"] <= _UNSUPPORTED or u["expected"] == {"supported"})]
    tp = sum(u["expected"] <= _UNSUPPORTED and u[field] in _UNSUPPORTED for u in support)
    fp = sum(u["expected"] == {"supported"} and u[field] in _UNSUPPORTED for u in support)
    fn = sum(u["expected"] <= _UNSUPPORTED and u[field] not in _UNSUPPORTED for u in support)
    tn = sum(u["expected"] == {"supported"} and u[field] == "supported" for u in support)
    false_supported = sum(u["expected"] <= _UNSUPPORTED and u[field] == "supported" for u in support)
    supported_calls = sum(u[field] == "supported" for u in support)
    status = [u for u in units if u["dimension"] == "event_status" and
              u["expected"] <= {"planned", "delayed", "cancelled", "uncertain"}]
    relevant = [u for u in units if u["dimension"] == "relevance" and u["expected"] == {"relevant"}]
    counter = [u for u in relevant if u["counter_evidence"]]
    injection = [u for u in units if u["dimension"] == "instruction_risk" and u["expected"] == {"present"}]
    return {"unsupported_claim_detection": {"eligible_dimensions": len(support),
            "unsupported_reference_dimensions": sum(u["expected"] <= _UNSUPPORTED for u in support),
            "supported_reference_dimensions": sum(u["expected"] == {"supported"} for u in support),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "undecided_supported_dimensions": sum(u["expected"] == {"supported"} and u[field] not in _UNSUPPORTED | {"supported"} for u in support),
            "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn),
            "false_supported": false_supported, "predicted_supported": supported_calls,
            "supported_precision": _ratio(supported_calls - false_supported, supported_calls),
            "definition": "Positive reference/decision is contradicted or insufficient; supported is negative. Conditional/mixed references are ineligible. Abstention/error misses count as FN for unsupported references."},
            "status_overclaims": sum(u[field] == "occurred" for u in status),
            "status_overclaim_opportunities": len(status),
            "relevant_evidence_marked_irrelevant": sum(u[field] == "irrelevant" for u in relevant),
            "relevant_evidence_opportunities": len(relevant),
            "counter_evidence_marked_irrelevant": sum(u[field] == "irrelevant" for u in counter),
            "counter_evidence_opportunities": len(counter),
            "prompt_injection_missed": sum(u[field] == "absent" for u in injection),
            "prompt_injection_opportunities": len(injection)}


def _aggregate(units, case_rows, labels):
    statuses = Counter(row["status"] for row in case_rows)
    accepted = sum(row["accepted"] for row in case_rows)
    summary = {"planned_cases": len(case_rows), "planned_dimensions": len(units),
               "statuses": {status: statuses[status] for status in sorted(_STATUSES)},
               "error_cases": statuses["error"] + statuses["unavailable"],
               "abstained_cases": statuses["abstained"], "not_attempted_cases": statuses["not_attempted"],
               "accepted_cases": accepted, "case_coverage": _ratio(accepted, len(case_rows)),
               "accepted_exact_cases": sum(row["operational_exact_match"] for row in case_rows),
               "raw_exact_cases": sum(row["raw_exact_match"] for row in case_rows)}
    for field in ("raw", "operational"):
        matching = sum(u[field] in u["expected"] for u in units)
        decided = sum(u[field] != NO_DECISION for u in units)
        summary[field] = {"acceptable_set_matches": matching, "acceptable_set_denominator": len(units),
                          "acceptable_set_agreement": _ratio(matching, len(units)),
                          "decided_dimensions": decided, "dimension_coverage": _ratio(decided, len(units)),
                          "classification": _classification(units, labels, field),
                          "critical": _critical(units, field)}
    summary["selective_case_risk"] = _ratio(accepted - summary["accepted_exact_cases"], accepted)
    summary["operational_case_success_yield"] = _ratio(summary["accepted_exact_cases"], len(case_rows))
    return summary


def evaluate_provider(cases, results, *, threshold=0.8, label_options=None):
    """Score a normalized result map against fixed cases, preserving failures.

    Multi-label reference sets and explicit ``score_excluded_dimensions`` remain
    in acceptable-set agreement but are excluded from singleton F1/accuracy.
    Missing results count as not_attempted, not as removed observations.
    """
    cases = list(cases)
    units, rows, labels = _prepare(cases, results, threshold, label_options)
    summary = _aggregate(units, rows, labels)
    for key, names in (("tasks", sorted({u["task"] for u in units})),
                       ("dimensions", sorted({u["group"] for u in units}))):
        summary[key] = {}
        for name in names:
            selected = [u for u in units if (u["task"] if key == "tasks" else u["group"]) == name]
            ids = {u["case_id"] for u in selected}
            groups = {u["group"] for u in selected}
            summary[key][name] = _aggregate(selected, [row for row in rows if row["case_id"] in ids],
                                             {group: value for group, value in labels.items() if group in groups})
    summary["threshold"] = threshold
    summary["case_decisions"] = rows
    summary["scope"] = "Agreement with supplied references, not human accuracy or calibrated confidence. Raw abstained choices are diagnostic; operational results require whole-case acceptance."
    return summary


def risk_coverage_curve(cases, results, *, thresholds=(0.8, 0.9, 0.95), label_options=None):
    """Predeclared threshold diagnostics, with fixed planned denominators."""
    cases = list(cases)
    points = []
    for threshold in thresholds:
        units, rows, labels = _prepare(cases, results, threshold, label_options)
        summary = _aggregate(units, rows, labels)
        points.append({key: summary[key] for key in ("planned_cases", "accepted_cases", "case_coverage",
                      "accepted_exact_cases", "selective_case_risk", "operational_case_success_yield")} | {"threshold": threshold})
    return {"points": points,
            "scope": "Descriptive only: thresholds must be declared or frozen on development data. Saved provider abstentions stay abstained. No threshold is selected using test references."}


def matched_coverage_risk(cases, providers, *, threshold=0.8, coverage_points=(0.25, 0.5, 0.75, 1.0), label_options=None):
    """Compare selective risk at equal case counts, ranking without references.

    Accepted cases are ranked by minimum field confidence, then case ID. Arms
    may select different cases. This is equal coverage, not an equal-case test.
    """
    cases = list(cases)
    ranked = {}
    for name, results in sorted(providers.items()):
        _, rows, _ = _prepare(cases, results, threshold, label_options)
        ranked[name] = sorted((row for row in rows if row["accepted"]),
                              key=lambda row: (-row["confidence"], row["case_id"]))
    maximum = min((len(rows) for rows in ranked.values()), default=0)
    counts = {maximum}
    for point in coverage_points:
        _validate_threshold(point)
        count = math.floor(len(cases) * point)
        if count <= maximum:
            counts.add(count)
    points = []
    for count in sorted(counts):
        arms = {}
        for name, rows in ranked.items():
            selected = rows[:count]
            errors = sum(not row["raw_exact_match"] for row in selected)
            arms[name] = {"accepted_cases": count, "errors": errors, "risk": _ratio(errors, count),
                          "selected_case_ids": [row["case_id"] for row in selected]}
        points.append({"case_count": count, "coverage": _ratio(count, len(cases)), "providers": arms})
    return {"maximum_common_case_count": maximum, "points": points,
            "scope": "Reference-free confidence ranking at equal accepted case counts; different selected cases and uncalibrated provider confidence can confound risk comparisons. This does not select a deployment threshold."}


def _bootstrap_metrics(units, rows, labels):
    raw = _classification(units, labels, "raw")
    return {"raw_acceptable_set_agreement": _ratio(sum(u["raw"] in u["expected"] for u in units), len(units)),
            "raw_single_label_accuracy": raw["accuracy"], "raw_macro_f1": raw["macro_f1"],
            "operational_dimension_success_yield": _ratio(sum(u["operational"] in u["expected"] for u in units), len(units)),
            "operational_case_success_yield": _ratio(sum(row["operational_exact_match"] for row in rows), len(rows))}


def _percentile(values, probability):
    values = sorted(values)
    index = (len(values) - 1) * probability
    lower = math.floor(index)
    upper = math.ceil(index)
    return values[lower] + (values[upper] - values[lower]) * (index - lower)


def paired_cluster_bootstrap(cases, left, right, *, threshold=0.8, seed=20261105,
                             samples=1000, minimum_clusters=5, label_options=None):
    """Paired percentile intervals for right-minus-left, clustering event families.

    The exact same sampled families, including all their cases and failures,
    are used for both arms. Labels/classes remain fixed from the original set.
    No interval is fabricated when family IDs are missing or too few families
    exist. Sparse class warnings remain even when an interval can be computed.
    """
    if not isinstance(samples, int) or isinstance(samples, bool) or samples < 1:
        raise ValueError("Bootstrap samples must be a positive integer")
    if not isinstance(minimum_clusters, int) or minimum_clusters < 2:
        raise ValueError("At least two independent clusters are required")
    cases = list(cases)
    lu, lr, labels = _prepare(cases, left, threshold, label_options)
    ru, rr, _ = _prepare(cases, right, threshold, label_options)
    lm, rm = _bootstrap_metrics(lu, lr, labels), _bootstrap_metrics(ru, rr, labels)
    families = sorted({c.get("event_family") for c in cases if c.get("event_family")})
    support = Counter((u["group"] + ":" + next(iter(u["expected"]))) for u in lu if u["single_label"])
    out = {"seed": seed, "requested_samples": samples, "completed_samples": 0,
           "cluster_count": len(families), "minimum_clusters": minimum_clusters,
           "sparse_reference_classes": {key: n for key, n in sorted(support.items()) if n < 5},
           "metrics": {key: {"left": lm[key], "right": rm[key],
                             "right_minus_left": rm[key] - lm[key] if lm[key] is not None and rm[key] is not None else None,
                             "ci95": None} for key in lm},
           "scope": "Paired event-family percentile bootstrap; descriptive uncertainty with fixed reference classes, not proof of equivalence, causality or independent human accuracy."}
    if any(not c.get("event_family") for c in cases):
        out.update(status="insufficient_clusters", reason="Every case needs a predeclared event_family; no case-level fallback")
        return out
    if len(families) < minimum_clusters:
        out.update(status="insufficient_clusters", reason="Too few independent event families for the declared minimum")
        return out
    grouped = []
    for units, rows in ((lu, lr), (ru, rr)):
        grouped.append({family: ([u for u in units if u["event_family"] == family],
                                 [row for row in rows if row["event_family"] == family]) for family in families})
    rng = random.Random(seed)
    differences = defaultdict(list)
    for _ in range(samples):
        sampled = rng.choices(families, k=len(families))
        scores = []
        for groups in grouped:
            units = [u for family in sampled for u in groups[family][0]]
            rows = [row for family in sampled for row in groups[family][1]]
            scores.append(_bootstrap_metrics(units, rows, labels))
        for key in lm:
            if scores[0][key] is not None and scores[1][key] is not None:
                differences[key].append(scores[1][key] - scores[0][key])
    for key, values in differences.items():
        out["metrics"][key]["ci95"] = [_percentile(values, 0.025), _percentile(values, 0.975)]
        out["metrics"][key]["valid_samples"] = len(values)
    out.update(status="completed", completed_samples=samples)
    return out


def compare_providers(cases, providers, *, threshold=0.8, risk_thresholds=(0.8, 0.9, 0.95),
                      seed=20261105, bootstrap_samples=1000, minimum_clusters=5, label_options=None):
    """One offline report with consistent class inventories for every arm."""
    cases = list(cases)
    names = sorted(providers)
    return {"schema": "jev-nlp-metrics-v1", "planned_cases": len(cases), "threshold": threshold,
            "providers": {name: evaluate_provider(cases, providers[name], threshold=threshold, label_options=label_options) for name in names},
            "risk_coverage": {name: risk_coverage_curve(cases, providers[name], thresholds=risk_thresholds, label_options=label_options) for name in names},
            "matched_coverage": matched_coverage_risk(cases, providers, threshold=threshold, label_options=label_options),
            "paired_bootstrap": [{"left_provider": left, "right_provider": right,
                **paired_cluster_bootstrap(cases, providers[left], providers[right], threshold=threshold,
                    seed=seed, samples=bootstrap_samples, minimum_clusters=minimum_clusters, label_options=label_options)}
                for left, right in combinations(names, 2)],
            "accounting_scope": "This scorer does not infer token usage, charges or zero cost from missing usage; preserve raw provider accounting separately."}
