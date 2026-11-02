"""Opt-in source-native references; never fill a different venue/provider's gaps."""
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode
import xml.etree.ElementTree as ET
from .store import instant
from .time import timestamp
from .repair import rate_calendar,nonbusiness_dates


def collect_bitstamp(library,http,job):
    library.register_source('bitstamp',base_url='https://www.bitstamp.net',window_start=library.window_start,window_end=library.window_end,
        material_scope='Bitstamp ETH/USD and BTC/USD hourly OHLCV; independent venue from Coinbase',redistribution='Review provider terms; not part of public source ZIP')
    contracts={r['dataset_id']:r for r in library.datasets()}
    for asset in ('eth','btc'):
        dataset=f'market.bitstamp.{asset}.hourly'
        units={k:'USD' for k in ('open_usd','high_usd','low_usd','close_usd')};units['volume_asset']=asset.upper()
        library.register_dataset(dataset,provider='bitstamp',frequency='1h',asset=asset.upper(),units=units,required=False,
            scope=f'Bitstamp {asset.upper()}/USD completed UTC hourly OHLCV; independent venue, 30-day separately marked warmup')
        original=contracts[f'analysis.{asset}.hourly']
        library.register_dataset(f'analysis.bitstamp.{asset}.hourly',provider='derived',frequency='1h',asset=asset.upper(),units=original['units'],required=False,
            feature_input_dataset=dataset,scope='Prefix-only descriptive indicators on Bitstamp USD hourly candles; independent venue from Coinbase')
        def collect(a=asset,d=dataset):
            begin=library.start-timedelta(days=30);cursor=begin;seen={}
            while cursor<library.end:
                stop=min(library.end,cursor+timedelta(hours=500))
                # Both API boundaries are inclusive. Exactly 500 requested start
                # timestamps avoid silently truncating the beginning of a page.
                url=f'https://www.bitstamp.net/api/v2/ohlc/{a}usd/?'+urlencode({'step':3600,'limit':500,'start':int(cursor.timestamp()),'end':int(stop.timestamp())-1,'exclude_current_candle':'true'})
                cid,payload=http.json('bitstamp',url)
                if payload.get('data',{}).get('pair') not in (None,a+'usd',a.upper()+'/USD'):raise ValueError('Unexpected Bitstamp pair')
                for row in payload['data']['ohlc']:
                    at=timestamp(int(row['timestamp']));end=at+timedelta(hours=1)
                    if not begin<=at or end>library.end:continue
                    values={k+'_usd':Decimal(row[k]) for k in ('open','high','low','close')};values['volume_asset']=Decimal(row['volume'])
                    low,high=values['low_usd'],values['high_usd']
                    if at.minute or at.second or not all(v.is_finite() for v in values.values()) or low<=0 or values['volume_asset']<0 or not low<=values['open_usd']<=high or not low<=values['close_usd']<=high:raise ValueError('Invalid source OHLCV')
                    if at in seen and seen[at]!=values:raise ValueError('Conflicting same-venue overlap')
                    seen[at]=values
                    library.put(d,at,values,cid,period_end=end,metadata={'warmup':at<library.start,'venue':'Bitstamp','pair':a+'usd','source_timestamp':row['timestamp']})
                cursor=stop
                library.job(d,'running',cursor=instant(cursor),warmup_start=instant(begin),unique_intervals=len(seen),expected_formal=library.hours,expected_warmup=720)
            expected=[begin+timedelta(hours=i) for i in range(library.hours+720)]
            missing=[instant(t) for t in expected if t not in seen]
            return {'source_range_complete':not missing,'observations':sum(t>=library.start for t in seen),'warmup_intervals':sum(t<library.start for t in seen),
                'expected_formal':library.hours,'expected_warmup':720,'unresolved_intervals':missing,'venue':'Bitstamp','pair':a+'usd','cursor':instant(library.end)}
        job(dataset,collect)


def treasury_rows(raw):
    """Keep publisher date and percent strings, without a synthetic day fill."""
    root=ET.fromstring(raw)
    for props in root.iter():
        if props.tag.rsplit('}',1)[-1]!='properties':continue
        fields={node.tag.rsplit('}',1)[-1]:node.text for node in props}
        if fields.get('NEW_DATE'):yield fields


def collect_treasury(library,http,job):
    library.register_source('us_treasury',base_url='https://home.treasury.gov',window_start=library.window_start,window_end=library.window_end,
        material_scope='US Treasury official daily par yield curve; source-native dataset, not an acquired FRED/H15 series',redistribution='US Treasury public source; review original terms')
    by_date={}
    for year in range(library.start.year,(library.end-timedelta(seconds=1)).year+1):
        url='https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?'+urlencode({'data':'daily_treasury_yield_curve','field_tdr_date_value':year})
        cid,raw=http.fetch('us_treasury',url)
        for fields in treasury_rows(raw):
            at=timestamp(fields['NEW_DATE'][:10])
            if library.start<=at<library.end:by_date[instant(at)]=(fields,cid)
    for years,field in [(2,'BC_2YEAR'),(10,'BC_10YEAR')]:
        dataset=f'macro.treasury.cmt{years}.daily'
        library.register_dataset(dataset,provider='us_treasury',frequency='business_day',asset='USD',units={'rate_percent':'percent/year'},required=False,
            scope=f'Official US Treasury {years}-year daily par yield curve/CMT; independently labelled provider reference, not a replacement claim for FRED/H15 acquisition')
        def collect(d=dataset,k=field):
            dates=[];source_missing=[]
            for at,(fields,cid) in sorted(by_date.items()):
                if fields.get(k) is None:source_missing.append(at[:10]);continue
                library.put(d,at,{'rate_percent':fields[k]},cid,metadata={'original_field':k,'original_date':fields['NEW_DATE'],'source_direct_not_fred_csv':True,'methodology':'Treasury daily par yield curve rates, quoted percent/year'});dates.append(at[:10])
            expected=rate_calendar(library.start,library.end,instrument='treasury')
            return {'source_range_complete':set(dates)==set(expected),'observations':len(dates),'expected_source_dates':expected,'source_explicit_missing_dates':source_missing,
                'unresolved_dates':sorted(set(expected)-set(dates)),'unexpected_dates':sorted(set(dates)-set(expected)),
                'nonobservation_dates':nonbusiness_dates(library.start,library.end,instrument='treasury'),'source':'US Treasury official daily par yield curve; not FRED/H15'}
        job(dataset,collect)
