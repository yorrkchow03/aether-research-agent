"""Small credential-free source checks before the half-year backfill.

Coin Metrics series are small enough to acquire once here. Their actual partial
observations and denied fields remain in the library; later stages do not need
to repeat those denied requests.
"""
import json
from pathlib import Path
from urllib.parse import urlencode

from research_data.library.store import ResearchLibrary
from research_data.library.registry import install, TEXT_SOURCES
from research_data.library.numeric import NumericCollector
from research_data.http import utcnow

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'local/halfyear/data'


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    library=ResearchLibrary(ROOT/'data/library-halfyear-20260426-20261026',window_start='2026-04-26T00:00:00Z',window_end='2026-10-26T00:00:00Z')
    install(library);worker=NumericCollector(library);checks=[]
    entries=[]
    for asset in ('ETH','BTC'):
        entries.append(('candles_'+asset,'coinbase','https://api.exchange.coinbase.com/products/'+asset+'-USD/candles?'+urlencode({'granularity':3600,'start':library.window_start,'end':'2026-04-26T03:00:00Z'}),'json'))
    entries += [
        ('funding_oldest','okx','https://www.okx.com/api/v5/public/funding-rate-history?'+urlencode({'instId':'ETH-USDT-SWAP','after':int(library.start.timestamp()*1000)+86400000,'limit':3}),'json'),
        ('oi_oldest','okx','https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history?'+urlencode({'instId':'ETH-USDT-SWAP','period':'1H','begin':int(library.start.timestamp()*1000),'end':int(library.start.timestamp()*1000)+10800000,'limit':10}),'json'),
        ('tvl','defillama','https://api.llama.fi/v2/historicalChainTvl/Ethereum','json'),
        ('stablecoins','stablecoins','https://stablecoins.llama.fi/stablecoincharts/Ethereum','json'),
        ('dex','defillama','https://api.llama.fi/overview/dexs/Ethereum?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true','json'),
        ('aave_fees','defillama','https://api.llama.fi/summary/fees/aave-v3?dataType=dailyFees','json'),
        ('lido_yield','defillama','https://yields.llama.fi/chart/747c1d2a-c668-4682-b9f9-296708a3dd90','json'),
        ('etf','farside','https://farside.co.uk/ethereum-etf-flow-all-data/','text'),
        ('rates','federal_reserve','https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json?startDate=2026-04-26&endDate=2026-04-30','json'),
        ('fomc','federal_reserve','https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm','text'),
        ('gas_chart','etherscan','https://etherscan.io/chart/gasused?output=csv','text'),
        ('execution_burn_chart','etherscan','https://etherscan.io/chart/dailyethburnt?output=csv','text'),
    ]
    entries += [('text_'+name,name,url,'text') for name,(_,_,url) in TEXT_SOURCES.items() if name!='coindesk']
    for name,source,url,kind in entries:
        item={'name':name,'source':source,'url':url}
        try:
            if kind=='json':
                cid,value=worker.http.json(source,url)
                item.update(status='response_acquired',capture_id=cid,response_kind=type(value).__name__,rows=len(value.get('data',[])) if isinstance(value,dict) and isinstance(value.get('data'),list) else len(value) if isinstance(value,list) else None)
            else:
                cid,raw=worker.http.fetch(source,url)
                item.update(status='response_acquired',capture_id=cid,bytes=len(raw))
        except Exception as exc:
            item.update(status='unresolved_source_check',error=type(exc).__name__+': '+str(exc)[:700])
        checks.append(item)
        library.job('preflight:'+name,item['status'],**{k:v for k,v in item.items() if k!='status'})
        (OUT/'PREFLIGHT.json').write_text(json.dumps({'created_at':utcnow(),'window':[library.window_start,library.window_end],'checks':checks,'scope':'Actual responses/explicit failures only; listing success does not certify pagination or original-body coverage.'},indent=2)+'\n')
        print(json.dumps({'name':name,'status':item['status'],'rows':item.get('rows'),'error':item.get('error')}),flush=True)
    worker.network()
    library.job('preflight:coindesk','access_policy_review',source_range_complete=False,reason='Retain metadata under publisher crawler restrictions; no bulk body requests or paid access enabled')
    coverage=library.coverage()
    (OUT/'PREFLIGHT_COVERAGE.json').write_text(json.dumps(coverage,indent=2)+'\n')
    print('PRECHECK_COMPLETE_WITH_EXPLICIT_GAPS',flush=True)


if __name__=='__main__':main()
