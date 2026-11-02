"""Synthetic, explicitly labelled test data; never imported into a user's ledger."""
import hashlib,json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from research_data.pipeline import Preparation
from research_data.http import Snapshot
from research_data.normalize import coingecko,record


def project_fixture(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    if (root/'data/latest.json').exists():return root
    p=Preparation('2026-10-13',365,root);cutoff=datetime(2026,10,13,tzinfo=timezone.utc)
    for asset,base in [('eth',1000),('btc',20000)]:
        points=[[int((cutoff-timedelta(days=364-i)).timestamp()*1000),base+i*2+(i%7)] for i in range(365)]
        obj={'prices':points,'market_caps':points,'total_volumes':points};body=json.dumps(obj).encode();path='raw/'+asset+'.json';(p.run_dir/path).write_bytes(body)
        source={'source_id':'synthetic-'+asset,'name':'SYNTHETIC UNIT FIXTURE','url':'https://example.org/synthetic-'+asset,'path':path,'sha256':hashlib.sha256(body).hexdigest(),'retrieved_at':'2026-10-14T00:00:00Z','response_headers':{'Content-Type':'application/json'}}
        p.fetcher.sources.append(source);snap=Snapshot(source,body)
        p.save('market.coingecko.'+asset+'.daily',coingecko(snap,asset),units={'price_usd':'USD','market_cap_usd':'USD','rolling_24h_volume_usd':'USD'},scope='Synthetic unit-test values, not market data')
    s=Snapshot(p.fetcher.sources[0],b'{}');r=record('flows.farside.eth_etf.daily','2026-10-12T00:00:00Z',{'total_net_flow_usd':42},s,'',flags=['synthetic_fixture','availability_unknown'])
    p.save('flows.farside.eth_etf.daily',[r],units={'*_net_flow_usd':'USD/trading day'},scope='Synthetic ETF fixture, not real flows')
    p.build_database();manifest={'schema_version':'1.0','cutoff':'2026-10-13T00:00:00Z','datasets':list(p.datasets.values()),'sources':p.fetcher.sources,'jobs':[],'limitations':['Synthetic test fixture; never production market data']}
    (p.run_dir/'manifest.json').write_text(json.dumps(manifest));(root/'data/latest.json').write_text(json.dumps({'run_dir':str(p.run_dir)}))
    return root


def fixture_run(root):
    project=project_fixture(root)
    return Path(json.loads((project/'data/latest.json').read_text())['run_dir'])
