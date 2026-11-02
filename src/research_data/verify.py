"""Check file integrity, provenance, time eligibility and numerical invariants."""
import hashlib
import json
import math
import sqlite3
from decimal import Decimal
from pathlib import Path

from .normalize import timestamp


def verify_bundle(run_dir):
    root=Path(run_dir).resolve()
    manifest=json.loads((root/"manifest.json").read_text())
    errors=[]; warnings=[]; records={}; count=0
    def check_file(relative_path, expected):
        path=(root/relative_path).resolve()
        if not path.is_relative_to(root):
            errors.append(f"Unsafe path: {relative_path}");return
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            errors.append(f"Missing file or hash mismatch: {relative_path}")
    sources={s["source_id"]:s for s in manifest["sources"]}
    for source in sources.values():
        if source["path"]:check_file(source["path"],source["sha256"])
    for meta in manifest["datasets"]:
        check_file(meta["path"],meta["sha256"])
        rows=[json.loads(line) for line in (root/meta["path"]).read_text().splitlines()]
        if len(rows)!=meta["record_count"]:errors.append("Row count mismatch: "+meta["dataset_id"])
        for r in rows:
            count+=1
            if r["record_id"] in records:errors.append("Duplicate record ID: "+r["record_id"])
            records[r["record_id"]]=r
            if r["dataset_id"]!=meta["dataset_id"]:errors.append("Dataset identity mismatch")
            if meta["kind"]!="snapshot" and timestamp(r.get("period_end") or r["observed_at"])>timestamp(manifest["cutoff"]):errors.append("Future historical observation: "+r["record_id"])
            for ref in r["evidence"]:
                if ref["source_id"] not in sources:errors.append("Broken source reference: "+r["record_id"])
                elif not sources[ref["source_id"]]["path"]:errors.append("Source reference has no raw response")
            for k,v in r["values"].items():
                if v is None:continue
                try:
                    val=Decimal(v)
                    if not val.is_finite():raise ValueError("Non-finite")
                    if k in ("price_usd","market_cap_usd","tvl_usd","supply_eth","transaction_count","active_address_count","dex_volume_usd","circulating_value_usd") and val<0:errors.append("Negative unsigned metric: "+r["record_id"]+"/"+k)
                    if k=="eth_btc_return_correlation_90d" and not -1<=val<=1:errors.append("Invalid correlation")
                    if k=="eth_max_drawdown_30d_fraction" and not -1<=val<=0:errors.append("Invalid drawdown")
                except (ValueError,ArithmeticError):errors.append("Invalid numeric value: "+r["record_id"]+"/"+k)
        if "stale_or_short_coverage" in meta["quality_flags"]:warnings.append("Limited or stale coverage: "+meta["dataset_id"])
    for r in records.values():
        for rid in r.get("input_record_ids",[]):
            if rid not in records:errors.append("Broken calculation input: "+rid)
    check_file("research.sqlite",manifest["database_sha256"])
    db=sqlite3.connect((root/"research.sqlite").as_uri()+"?mode=ro",uri=True)
    if db.execute("PRAGMA quick_check").fetchone()[0]!="ok":errors.append("SQLite quick_check failed")
    if db.execute("SELECT COUNT(*) FROM records").fetchone()[0]!=count:errors.append("SQLite row count mismatch")
    db.close()
    # Independently recompute reported 7-day returns from their cited price records.
    returns_checked=0
    for r in records.values():
        target=r["values"].get("eth_return_7d_fraction")
        if target is None:continue
        prices={p["observed_at"]:Decimal(p["values"]["price_usd"]) for rid in r["input_record_ids"] for p in [records[rid]] if p["dataset_id"]=="market.coingecko.eth.daily"}
        from datetime import timedelta
        now=timestamp(r["observed_at"]);past=now-timedelta(days=7)
        values={timestamp(k):v for k,v in prices.items()}
        if now not in values or past not in values or abs(values[now]/values[past]-1-Decimal(target))>Decimal("1e-12"):
            errors.append("7-day return does not match cited prices: "+r["record_id"])
        returns_checked+=1
    result={"status":"passed" if not errors else "failed","run_dir":str(root),"datasets":len(manifest["datasets"]),"records":count,
            "sources":len(sources),"returns_independently_recomputed":returns_checked,"errors":errors,"warnings":warnings,
            "scope":"File hashes, SQLite, time cutoff, provenance links, financial invariants and 7-day returns. Does not certify provider truth or historical publication availability."}
    (root/"verification.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    return result
