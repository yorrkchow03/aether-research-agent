"""Read-only, cutoff-aware access for future agent tools and sandbox scripts."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from .normalize import Tables, iso, timestamp


def resolve_pointer(obj, pointer):
    if pointer == "":
        return obj
    if not pointer.startswith("/"):
        raise ValueError("Not a JSON pointer")
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        obj = obj[int(key)] if isinstance(obj, list) else obj[key]
    return obj


class ResearchData:
    """The caller fixes cutoff/mode once; individual queries cannot override them."""

    def __init__(self, run_dir=None, *, cutoff=None, mode="observed"):
        root = Path(__file__).resolve().parents[2]
        if run_dir is None:
            run_dir = json.loads((root / "data/latest.json").read_text())["run_dir"]
        self.run_dir = Path(run_dir).resolve()
        self.manifest = json.loads((self.run_dir / "manifest.json").read_text())
        if mode not in ("observed", "strict"):
            raise ValueError("mode must be observed or strict")
        self.cutoff = iso(timestamp(cutoff or self.manifest["cutoff"]))
        self.mode = mode
        self.db = sqlite3.connect((self.run_dir / "research.sqlite").as_uri() + "?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        # ISO strings may differ in fractional-second precision. Compare actual
        # instants rather than relying on text order or SQLite millisecond rounding.
        self.db.create_function("eligible_time", 1, self._time_allowed, deterministic=True)
        self.sources = {s["source_id"]: s for s in self.manifest["sources"]}

    def close(self):
        self.db.close()

    def _time_allowed(self, value):
        try:
            return int(timestamp(value) <= timestamp(self.cutoff))
        except (ValueError, TypeError, OverflowError):
            return 0

    def _condition(self):
        result = "eligible_time(effective_time) = 1"
        params = []
        if self.mode == "strict":
            result += " AND available_at IS NOT NULL AND eligible_time(available_at) = 1"
        return result, params

    def list_datasets(self):
        condition, params = self._condition()
        result = []
        for row in self.db.execute("SELECT dataset_id, metadata_json FROM datasets ORDER BY dataset_id"):
            meta = json.loads(row["metadata_json"])
            counts = self.db.execute(f"SELECT COUNT(*) AS n,MIN(observed_at) AS first,MAX(effective_time) AS last FROM records WHERE dataset_id=? AND {condition}", [row["dataset_id"], *params]).fetchone()
            result.append({"dataset_id":row["dataset_id"],"kind":meta["kind"],"scope":meta["scope"],"units":meta["units"],"time_semantics":meta["time_semantics"],
                           "eligible_records":counts["n"],"first_observed_at":counts["first"],"last_effective_time":counts["last"]})
        return {"cutoff":self.cutoff,"mode":self.mode,"historical_availability_guaranteed":self.mode=="strict","datasets":result}

    def load_dataset(self, dataset_id, *, start=None, limit=1000):
        if not isinstance(limit,int) or not 1 <= limit <= 10000:
            raise ValueError("limit must be an integer between 1 and 10000")
        meta = self.db.execute("SELECT metadata_json FROM datasets WHERE dataset_id=?",[dataset_id]).fetchone()
        if meta is None:
            raise KeyError(f"Unknown dataset: {dataset_id}")
        condition, params = self._condition()
        if start:
            condition += " AND observed_at >= ?"; params.append(iso(timestamp(start)))
        rows = self.db.execute(f"SELECT payload_json FROM records WHERE dataset_id=? AND {condition} ORDER BY observed_at LIMIT ?",[dataset_id,*params,limit+1]).fetchall()
        return {"dataset_id":dataset_id,"cutoff":self.cutoff,"mode":self.mode,"units":json.loads(meta[0])["units"],
                "records":[json.loads(r[0]) for r in rows[:limit]],"truncated":len(rows)>limit,
                "note":"Observed mode is retrospective and permits unknown publication availability." if self.mode=="observed" else "Only records with known availability by cutoff are eligible."}

    def _record(self, record_id):
        condition, params = self._condition()
        row = self.db.execute(f"SELECT payload_json FROM records WHERE record_id=? AND {condition}",[record_id,*params]).fetchone()
        if row is None:
            raise KeyError("Record is absent or not eligible at this cutoff")
        return json.loads(row[0])

    def read_evidence(self, record_id):
        row = self._record(record_id)
        evidence = []
        for ref in row["evidence"]:
            source = self.sources[ref["source_id"]]
            path = (self.run_dir / source["path"]).resolve()
            if not path.is_relative_to(self.run_dir / "raw"):
                raise ValueError("Evidence path is outside the raw snapshot directory")
            locator = ref["locator"]
            fragment = self._fragment(path, locator, row)
            evidence.append({"source_id":source["source_id"],"url":source["url"],"retrieved_at":source["retrieved_at"],"sha256":source["sha256"],"locator":locator,"fragment":fragment})
        return {"record":row,"evidence":evidence,"input_record_ids":row.get("input_record_ids",[]),"mode":self.mode}

    @staticmethod
    def _fragment(path, locator, row):
        # Return the cited item only, never the entire historical raw response.
        if isinstance(locator,dict):
            return {k:ResearchData._fragment(path,v,row) for k,v in locator.items()}
        if locator.startswith("/") and not locator.startswith("/rss"):
            return resolve_pointer(json.loads(path.read_bytes()),locator)
        m = re.fullmatch(r"table\[(\d+)\]/row\[(\d+)\](?:/column\[(\d+)\])?",locator)
        if m:
            p=Tables();p.feed(path.read_text(errors="replace"))
            cells=p.tables[int(m[1])][int(m[2])]
            return cells[int(m[3])] if m[3] is not None else cells
        m = re.fullmatch(r"/rss/channel/item\[(\d+)\]",locator)
        if m:
            item=ElementTree.fromstring(path.read_bytes()).findall("./channel/item")[int(m[1])]
            return ElementTree.tostring(item,encoding="unicode")
        if locator == "full_document":
            return row.get("text", "")
        if locator.startswith("CSV row "):
            import csv,io
            items=list(csv.DictReader(io.StringIO(path.read_text())))
            return items[int(locator.removeprefix("CSV row "))-2]
        raise ValueError(f"Unsupported evidence locator: {locator}")
