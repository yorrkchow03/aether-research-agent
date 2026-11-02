"""Immutable, source-grounded inputs for the small end-to-end report study.

This module performs no provider calls and opens no mutable product database.
Reference rubrics are AI-authored, predeclared audit aids, not human truth.
Both study arms must receive the same case payload and frozen source access.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


SCHEMA = "aether-report-flow-study-v1"
EXCERPT_SEPARATOR = "\n[…]\n"
PAYLOAD_FIELDS = ("case_id", "question", "cutoff", "language", "source_ids", "research_context")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def text_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clock(value):
    if not isinstance(value, str):
        raise ValueError("A saved UTC publication/acquisition clock is required")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timezone required")
    return dt.astimezone(timezone.utc)


def saved_source(original, ranges=None):
    """Bind exact model-visible slices to the complete saved normalized original.

    Raw capture hashes/URLs are retained in the original; normalized text gets a
    separate hash. Slice offsets are Python Unicode character offsets, not bytes.
    """
    original = deepcopy({k: v for k, v in original.items() if k not in ("raw_path", "recipe_path")})
    text = original["text"]
    ranges = ranges or [(0, len(text))]
    spans = [{"start": a, "end": b, "sha256": text_sha(text[a:b])} for a, b in ranges]
    fields = ("record_id", "title", "published_at", "retrieved_at", "available_at", "source_kind", "dataset_id")
    research = {k: original[k] for k in fields if k in original}
    research["source"] = {k: v for k, v in original.get("source", {}).items()
                          if k in ("name", "url", "sha256", "source_id")}
    research["text"] = EXCERPT_SEPARATOR.join(text[a:b] for a, b in ranges)
    return {"original_record": original, "research_record": research,
            "original_text_sha256": text_sha(text), "research_text_sha256": text_sha(research["text"]),
            "original_record_sha256": text_sha(canonical(original)), "source_spans": spans,
            "input_kind": "saved_original_slices", "offset_unit": "unicode_characters"}


def case_payload(case):
    """Return only the task specification, never a split, rubric or target fact."""
    return {key: deepcopy(case[key]) for key in PAYLOAD_FIELDS}


def actual_records(case, sources):
    return [deepcopy(sources[rid]["research_record"]) for rid in case["source_ids"]]


def _check_quote(item, source_ids, sources):
    if not item.get("id") or not item.get("statement") or not item.get("evidence"):
        raise ValueError("Reference observations need an id, statement and exact supporting quotes")
    for evidence in item["evidence"]:
        rid, quote = evidence["source_id"], evidence["quote"]
        if rid not in source_ids or not quote or quote not in sources[rid]["research_record"]["text"]:
            raise ValueError("Reference quote absent from actual case context: " + rid)
        if evidence["sha256"] != text_sha(quote):
            raise ValueError("Reference quote hash mismatch")


def validate_dataset(sources, cases):
    if not cases or len({c["case_id"] for c in cases}) != len(cases):
        raise ValueError("Unique nonempty cases required")
    family_splits, source_splits = defaultdict(set), defaultdict(set)
    for rid, item in sources.items():
        original, research = item["original_record"], item["research_record"]
        if original["record_id"] != rid or research["record_id"] != rid:
            raise ValueError("Original evidence identity must be preserved")
        text = original["text"]
        if text_sha(text) != item["original_text_sha256"] or text_sha(canonical(original)) != item["original_record_sha256"]:
            raise ValueError("Saved original changed: " + rid)
        if not 0 < len(research["text"]) <= 12000:
            raise ValueError("Actual source slice must be nonempty and fit 12000 characters")
        parts, previous = [], -1
        for span in item["source_spans"]:
            a, b = span["start"], span["end"]
            if (isinstance(a, bool) or isinstance(b, bool) or not isinstance(a, int)
                    or not isinstance(b, int) or not 0 <= a < b <= len(text) or a < previous):
                raise ValueError("Invalid, overlapping or unordered original span")
            part = text[a:b]
            if text_sha(part) != span["sha256"]:
                raise ValueError("Original span hash mismatch")
            parts.append(part)
            previous = b
        if not parts or EXCERPT_SEPARATOR.join(parts) != research["text"] or text_sha(research["text"]) != item["research_text_sha256"]:
            raise ValueError("Model-visible context differs from exact saved slices")
        for field in ("title", "published_at", "retrieved_at", "available_at"):
            if research.get(field) != original.get(field):
                raise ValueError("Source metadata changed")
        expected_source = {k: v for k, v in original.get("source", {}).items()
                           if k in ("name", "url", "sha256", "source_id")}
        if research.get("source", {}) != expected_source:
            raise ValueError("Source URL, capture hash or publisher metadata changed")
        for field in ("published_at", "retrieved_at", "available_at"):
            _clock(original[field])
    used = set()
    for case in cases:
        if case["split"] not in ("development", "confirmation"):
            raise ValueError("Unknown study split")
        ids = case["source_ids"]
        if not 2 <= len(ids) <= 4 or len(set(ids)) != len(ids):
            raise ValueError("Each report needs 2 to 4 distinct saved sources")
        if case["language"] != "English" or not case.get("question") or not case.get("event_family"):
            raise ValueError("Missing report task contract")
        if case["research_context"]["availability_mode"] != "observed":
            raise ValueError("These retrospective captures cannot impersonate historical knowledge")
        family_splits[case["event_family"]].add(case["split"])
        for rid in ids:
            if rid not in sources:
                raise ValueError("Case source missing")
            if _clock(sources[rid]["original_record"]["available_at"]) > _clock(case["cutoff"]):
                raise ValueError("Source acquired after observed cutoff")
            source_splits[rid].add(case["split"])
            used.add(rid)
        expected = case["expected"]
        if not 2 <= len(expected["required_facts"]) <= 3 or not expected["counter_evidence"]:
            raise ValueError("Predeclare 2-3 report observations plus a qualification/counterpoint")
        for item in expected["required_facts"] + expected["counter_evidence"]:
            _check_quote(item, ids, sources)
        if not expected["forbidden_inferences"] or not expected["allowed_gaps"]:
            raise ValueError("Unsupported claims and legitimate unknowns must be predeclared")
        if set(case_payload(case)) != set(PAYLOAD_FIELDS):
            raise ValueError("Rubric leaked into model task")
    if used != set(sources):
        raise ValueError("Every source must be assigned to a case")
    if any(len(v) > 1 for v in family_splits.values()) or any(len(v) > 1 for v in source_splits.values()):
        raise ValueError("Event family or original source crosses development/confirmation")
    return True


def freeze_dataset(output, *, sources, cases, protocol):
    """Create once, then reject overwrites; all payloads are manifest-bound."""
    output = Path(output)
    if output.exists():
        raise ValueError("Never overwrite a frozen dataset; create a new version")
    validate_dataset(sources, cases)
    frozen_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    protocol = {**deepcopy(protocol), "schema": SCHEMA, "frozen_at": frozen_at,
                "case_count": len(cases), "source_count": len(sources),
                "split_counts": dict(Counter(c["split"] for c in cases)),
                "human_labels": 0,
                "reference_status": "AI-authored predeclared rubric; independent review remains separately recorded"}
    blind = {"schema": SCHEMA,
             "instructions": "Independently identify report-worthy source observations, qualifications, and unsupported conclusions. Use the exact research records; consult saved originals where necessary. No draft reference rubric, provider results or split is included. This is AI review, not human certification.",
             "cases": [{**case_payload(case), "research_records": actual_records(case, sources),
                        "original_pointers": [{"source_id": rid, "file": "sources.json",
                                               "json_key": rid + ".original_record",
                                               "text_sha256": sources[rid]["original_text_sha256"]}
                                              for rid in case["source_ids"]]}
                       for case in cases]}
    files = {"protocol.json": protocol, "sources.json": sources, "cases.json": cases,
             "model_inputs.json": [case_payload(case) for case in cases],
             "blind_reviewer_input.json": blind}
    output.mkdir(parents=True)
    for name, value in files.items():
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    manifest = {"schema": SCHEMA, "frozen_at": frozen_at, "case_count": len(cases),
                "source_count": len(sources), "split_counts": protocol["split_counts"],
                "human_labels": 0,
                "files": {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in files}}
    (output / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (output / "MANIFEST.sha256").write_text(hashlib.sha256((output / "MANIFEST.json").read_bytes()).hexdigest() + "\n", encoding="utf-8")
    return manifest


def load_dataset(output):
    output = Path(output)
    raw_manifest = (output / "MANIFEST.json").read_bytes()
    if hashlib.sha256(raw_manifest).hexdigest() != (output / "MANIFEST.sha256").read_text().strip():
        raise ValueError("Frozen manifest changed")
    manifest = json.loads(raw_manifest)
    if manifest["schema"] != SCHEMA:
        raise ValueError("Unknown report study schema")
    for name, digest in manifest["files"].items():
        if Path(name).name != name or hashlib.sha256((output / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Frozen payload changed: " + name)
    expected_files = {"protocol.json", "sources.json", "cases.json", "model_inputs.json", "blind_reviewer_input.json"}
    if set(manifest["files"]) != expected_files:
        raise ValueError("Frozen payload set changed")
    protocol = json.loads((output / "protocol.json").read_text())
    sources = json.loads((output / "sources.json").read_text())
    cases = json.loads((output / "cases.json").read_text())
    validate_dataset(sources, cases)
    if protocol["schema"] != SCHEMA or len(cases) != manifest["case_count"] or len(sources) != manifest["source_count"]:
        raise ValueError("Manifest counts/protocol differ")
    return {"manifest": manifest, "protocol": protocol, "sources": sources, "cases": cases}


class FrozenReportCorpus:
    """One read-only retrieval entry point for both report harness arms.

    Search is lexical and deterministic; no web access or product memory writes.
    Reads use the saved evidence ID, so citations remain resolvable. Full source
    reads are available equally to both arms and must be counted in their traces.
    """
    def __init__(self, dataset):
        self.dataset = load_dataset(dataset) if isinstance(dataset, (str, Path)) else deepcopy(dataset)
        self.sources = self.dataset["sources"]
        self.cases = {case["case_id"]: case for case in self.dataset["cases"]}

    def task(self, case_id):
        return case_payload(self.cases[case_id])

    def source_pack(self, case_id):
        return actual_records(self.cases[case_id], self.sources)

    def read(self, record_id, *, original=False, case_id=None):
        if case_id is not None and record_id not in self.cases[case_id]["source_ids"]:
            raise KeyError("Source outside this case's predeclared source pack")
        return deepcopy(self.sources[record_id]["original_record" if original else "research_record"])

    def search(self, query, *, case_id=None, limit=4):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
            raise ValueError("Search limit must be 1-20")
        terms = set(re.findall(r"[\w-]+", query.lower()))
        allowed = self.cases[case_id]["source_ids"] if case_id is not None else self.sources
        scored = []
        for rid in allowed:
            record = self.sources[rid]["research_record"]
            haystack = (record["title"] + " " + record["text"]).lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                scored.append((score, rid))
        return [self.read(rid, case_id=case_id) for _, rid in sorted(scored, key=lambda x: (-x[0], x[1]))[:limit]]
