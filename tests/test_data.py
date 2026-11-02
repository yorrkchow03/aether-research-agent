"""Regression checks for data semantics and historical access boundaries."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from research_data.http import Snapshot
from research_data.normalize import coingecko, etf_flows, llama_stable, coinmetrics, record
from research_data.pipeline import Preparation
from research_data.sdk import ResearchData


def snapshot(obj, content_type="application/json"):
    body=json.dumps(obj).encode() if not isinstance(obj,str) else obj.encode()
    return Snapshot({"source_id":"fixture:1","retrieved_at":"2026-10-13T12:00:00Z","name":"fixture","path":"raw/fixture.bin","sha256":"fixture","response_headers":{"Content-Type":content_type}},body)


class DataSemantics(unittest.TestCase):
    def test_live_tail_is_not_a_daily_price(self):
        midnight=int(datetime(2026,10,12,tzinfo=timezone.utc).timestamp()*1000)
        obj={k:[[midnight,100],[midnight+3600*1000,200]] for k in ("prices","market_caps","total_volumes")}
        rows=coingecko(snapshot(obj),"eth")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["values"]["rolling_24h_volume_usd"],"100")

    def test_mixed_native_stablecoin_currencies_are_not_added(self):
        obj=[{"date":"1791763200","totalCirculating":{"peggedUSD":100,"peggedJPY":10000},"totalCirculatingUSD":{"peggedUSD":100,"peggedJPY":70}}]
        rows=llama_stable(snapshot(obj),"Ethereum")
        self.assertEqual(rows[0]["values"]["circulating_value_usd"],"170")

    def test_etf_missing_is_not_zero_and_parentheses_are_negative(self):
        html='<table><tr><th></th><th></th><th></th><th>Total</th></tr><tr><th></th><th>ETHA</th><th>FETH</th><th></th></tr><tr><td>12 Oct 2026</td><td>(2.5)</td><td>-</td><td>(2.5)</td></tr></table>'
        r=etf_flows(snapshot(html,"text/html"))[0]
        self.assertEqual(r["values"]["etha_net_flow_usd"],"-2500000.0")
        self.assertIsNone(r["values"]["feth_net_flow_usd"])
        self.assertIn("incomplete_publication",r["quality_flags"])

    def test_partial_provider_page_is_rejected(self):
        with self.assertRaisesRegex(ValueError,"paginated"):
            coinmetrics(snapshot({"data":[],"next_page_url":"https://example.org/page2"}),"fixture")

    def test_daily_flow_waits_until_day_is_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Preparation("2026-10-13",31,Path(tmp))
            rows=coinmetrics(snapshot({"data":[{"time":"2026-10-12T00:00:00Z","TxCnt":"10"},{"time":"2026-10-13T00:00:00Z","TxCnt":"20"}]}),"fixture")
            saved=p.save("fixture",rows)
            self.assertEqual(len(saved),1)
            self.assertEqual(saved[0]["values"]["transaction_count"],"10")

    def test_event_time_collision_preserves_distinct_posts(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Preparation("2026-10-13",31,Path(tmp))
            s=snapshot({})
            a=record("events.fixture","2026-10-12T00:00:00Z",{},s,"a",extra={"url":"https://example.org/a"})
            b=record("events.fixture","2026-10-12T00:00:00Z",{},s,"b",extra={"url":"https://example.org/b"})
            self.assertEqual(len(p.save("events.fixture",[a,b],kind="events")),2)

    def test_html_cell_evidence_is_read_as_html_not_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"rates.html"
            path.write_text('<table><tr><td>10-year</td><td>5.22</td></tr></table>')
            value=ResearchData._fragment(path,{"rate":"table[0]/row[0]/column[1]"},{})
            self.assertEqual(value,{"rate":"5.22"})


class AccessBoundary(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        p=Preparation("2026-10-13",31,Path(self.temp.name));s=snapshot({})
        p.fetcher.sources=[s.metadata]
        old=record("daily.fixture","2026-10-11T00:00:00Z",{"price":1},s,"",available_at="2026-10-11T01:00:00Z")
        unknown=record("daily.fixture","2026-10-12T00:00:00Z",{"price":2},s,"")
        live=record("live.fixture","2026-10-13T12:00:00Z",{"price":3},s,"",available_at="2026-10-13T12:00:00Z")
        boundary=record("live.boundary","2026-10-11T12:00:00.000001Z",{"price":4},s,"",available_at="2026-10-11T12:00:00.000001Z")
        p.save("daily.fixture",[old,unknown]);p.save("live.fixture",[live],kind="snapshot");p.save("live.boundary",[boundary],kind="snapshot");p.build_database()
        (p.run_dir/"manifest.json").write_text(json.dumps({"cutoff":"2026-10-13T00:00:00Z","sources":[s.metadata]}))
        self.run_dir=p.run_dir;self.live_id=live["record_id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_observation_cutoff_also_blocks_direct_evidence_lookup(self):
        sdk=ResearchData(self.run_dir,cutoff="2026-10-11T12:00:00Z")
        try:
            self.assertEqual(len(sdk.load_dataset("daily.fixture")["records"]),1)
            self.assertEqual(len(sdk.load_dataset("live.fixture")["records"]),0)
            with self.assertRaises(KeyError):sdk.read_evidence(self.live_id)
        finally:sdk.close()

    def test_strict_mode_excludes_unknown_availability(self):
        sdk=ResearchData(self.run_dir,mode="strict")
        try:self.assertEqual(len(sdk.load_dataset("daily.fixture")["records"]),1)
        finally:sdk.close()

    def test_microsecond_after_cutoff_is_not_eligible(self):
        sdk=ResearchData(self.run_dir,cutoff="2026-10-11T12:00:00Z")
        try:self.assertEqual(sdk.load_dataset("live.boundary")["records"],[])
        finally:sdk.close()


if __name__=="__main__":unittest.main()
