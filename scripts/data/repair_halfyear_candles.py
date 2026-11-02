"""Repair only sparse real Coinbase gaps with bounded overlapping source reads."""
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

from research_data.library.store import ResearchLibrary, instant
from research_data.library.numeric import NumericCollector
from research_data.normalize import iso
from research_data.library.time import timestamp

ROOT=Path(__file__).resolve().parents[2]


def main():
    library=ResearchLibrary(ROOT/'data/library-halfyear-20260426-20261026',create=False)
    worker=NumericCollector(library);receipt=[]
    for asset in ('ETH','BTC'):
        dataset='market.coinbase.'+asset.lower()+'.hourly';begin=library.start-timedelta(days=30)
        expected={instant(begin+timedelta(hours=i)) for i in range(library.hours+720)}
        existing={r['observed_at'] for r in library.series(dataset,start=begin,limit=10000)['records']}
        missing=sorted(expected-existing);item={'asset':asset,'missing_before':missing,'capture_ids':[]}
        if missing and len(missing)<=24:
            groups=[]
            for date in missing:
                at=timestamp(date)
                if groups and at-groups[-1][-1]==timedelta(hours=1):groups[-1].append(at)
                else:groups.append([at])
            for group in groups:
                url='https://api.exchange.coinbase.com/products/'+asset+'-USD/candles?'+urlencode({'granularity':3600,'start':iso(group[0]-timedelta(hours=2)),'end':iso(group[-1]+timedelta(hours=3))})
                cid,rows=worker.http.json('coinbase',url);item['capture_ids'].append(cid)
                for row in rows:
                    at=timestamp(row[0])
                    if not begin<=at<library.end:continue
                    low,high,opening,close,volume=[Decimal(str(v)) for v in row[1:6]]
                    if not all(v.is_finite() for v in (low,high,opening,close,volume)) or low<=0 or volume<0 or not low<=opening<=high or not low<=close<=high:
                        raise ValueError('Invalid overlapping original Coinbase candle')
                    library.put(dataset,at,dict(zip(['low_usd','high_usd','open_usd','close_usd','volume_asset'],[low,high,opening,close,volume])),cid,period_end=at+timedelta(hours=1),metadata={'warmup':at<library.start,'source_array_time':row[0]})
        present={r['observed_at'] for r in library.series(dataset,start=begin,limit=10000)['records']};item['missing_after']=sorted(expected-present)
        library.job(dataset,'collected' if not item['missing_after'] else 'unresolved',source_range_complete=not item['missing_after'],expected_intervals=len(expected),formal_intervals=library.hours,warmup_intervals=720,sparse_overlap_repair=item)
        receipt.append(item)
    p=ROOT/'local/halfyear/data/CANDLE_REPAIR.json';p.write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))


if __name__=='__main__':main()
