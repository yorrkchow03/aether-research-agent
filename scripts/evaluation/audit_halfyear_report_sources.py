"""Independent offline numeric/original-source audit of the delivered report."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from research_agent.audit import report_evidence_ids, verify_bundle
from research_agent.collection_sources import validate_collection_sources
from research_agent.library_router import open_library
from research_data.library.time import timestamp

ROOT=Path(__file__).resolve().parents[2]


def main():
    output=ROOT/'local/halfyear/product'
    target=output/'SOURCE_AUDIT.json'
    if target.exists():raise ValueError('Preserve the existing source-audit receipt')
    delivery=json.loads((output/'CURRENT_PUBLICATION.json').read_text())
    original=json.loads((output/'RUN.json').read_text())
    bundle=Path(delivery['result']['audit_package']['path'])
    integrity=verify_bundle(bundle)
    if integrity['status']!='passed':raise ValueError('Portable evidence bundle integrity failed')
    index=json.loads((bundle/'replay_index.json').read_text())
    records={rid:json.loads((bundle/path).read_text()) for rid,path in index['records'].items()}
    report=json.loads((bundle/'report/report.json').read_text())
    library=open_library(ROOT,original['library'])
    roots=report_evidence_ids(report['report'])
    assert roots.issubset(records)
    calc=records['live-0c45fc59f8b848c62e04d4ef']
    assert calc['input_record_ids']==['series-644bd17be38ef4b8a36a4119','series-d0fccafcfb4581017c13bb2c']
    assets=[];expected_returns={}
    for cid in calc['input_record_ids']:
        collection=records[cid];rows=collection['records'];query=collection['query']
        assert query['query_result_fully_materialized'] and len(rows)==4392
        assert collection['input_record_ids']==[row['record_id'] for row in rows]
        checked=validate_collection_sources(library,rows,dataset_id=collection['dataset_id'],
            start=library.window_start,end=library.window_end,cutoff=collection['cutoff'],mode='observed')
        assert all(records[row['record_id']]['values']==row['values'] for row in rows)
        assert all((timestamp(b['observed_at'])-timestamp(a['observed_at'])).total_seconds()==3600 for a,b in zip(rows,rows[1:]))
        assert timestamp(rows[0]['observed_at'])==timestamp(library.window_start)
        assert timestamp(rows[-1]['period_end'])==timestamp(library.window_end)
        asset=rows[0]['asset'];assert all(row['asset']==asset and row['library_metadata']['venue']=='Bitstamp' for row in rows)
        value=Decimal(rows[-1]['values']['close_usd'])/Decimal(rows[0]['values']['close_usd'])-1
        expected_returns[asset]=value
        assert abs(value-Decimal(calc['values'][asset.lower()+'_close_to_close_return_fraction']))<Decimal('1e-12')
        assert Decimal(calc['values'][asset.lower()+'_first_close_usd'])==Decimal(rows[0]['values']['close_usd'])
        assert Decimal(calc['values'][asset.lower()+'_last_close_usd'])==Decimal(rows[-1]['values']['close_usd'])
        try:
            validate_collection_sources(library,rows[:1],dataset_id=collection['dataset_id'],
                start=library.window_start,end=library.window_end,cutoff=library.window_end,mode='strict')
        except ValueError:strict_rejected=True
        else:raise AssertionError('Retrospective acquisition was incorrectly accepted as strictly known')
        assets.append({'asset':asset,'dataset':collection['dataset_id'],'rows':len(rows),
            'first_observed_at':rows[0]['observed_at'],'first_candle_close_time':rows[0]['period_end'],
            'last_observed_at':rows[-1]['observed_at'],'last_candle_close_time':rows[-1]['period_end'],
            'close_to_close_intervals':len(rows)-1,'decimal_return':str(value),
            'validation':checked['validation'],'strict_historical_acquisition_rejected':strict_rejected})
    difference=expected_returns['ETH']-expected_returns['BTC']
    assert abs(difference-Decimal(calc['values']['eth_minus_btc_return_fraction']))<Decimal('1e-12')
    assert calc['values']['number_of_hourly_rows_per_series']=='4392'
    assert calc['values']['number_of_return_intervals']=='4391'
    pages=[]
    for rid in sorted(roots):
        row=records[rid]
        if not rid.startswith('page-'):continue
        source=row['source'];raw=bundle/'raw'/(source['sha256']+'.bin')
        assert hashlib.sha256(raw.read_bytes()).hexdigest()==source['sha256']
        if library.contains(rid):
            current=library.evidence(rid)
            assert current['text']==row['text'] and current['source']==source
        pages.append({'record_id':rid,'url':row.get('url') or source['url'],
            'source_sha256':source['sha256'],'published_at':row.get('published_at'),
            'retrieved_at':row['retrieved_at'],'characters':len(row.get('text','')),
            'in_selected_library':library.contains(rid)})
    assert len(pages)==3
    receipt={'passed':True,'report':delivery['result']['report_path'],'bundle':bundle.name,
        'root_citations':len(roots),'portable_records':len(records),'files_checked':integrity['files_checked'],
        'assets':assets,'text_originals':pages,'decimal_eth_minus_btc_return':str(difference),
        'source_receipts':len(report['sources']),'new_provider_requests':0,
        'scope':'Actual source identity/hash, exact full-series lineage, venue/time continuity, Decimal endpoint arithmetic and retrospective acquisition checks. AI semantic support, source truth and financial method/forecast quality remain separate.'}
    target.write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:receipt[k] for k in ('passed','root_citations','portable_records','files_checked','new_provider_requests')}))


if __name__=='__main__':main()
