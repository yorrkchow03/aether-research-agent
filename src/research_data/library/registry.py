"""The fixed monthly contract; data gaps never remove a required metric."""
from .store import WINDOW_START, WINDOW_END
from .time import timestamp

CHAINS=('Ethereum','Arbitrum','Base','Optimism')
TEXT_SOURCES={
 'ethereum':('https://blog.ethereum.org','sitemap','https://blog.ethereum.org/sitemap.xml'),
 'bitcoin_core':('https://bitcoincore.org','index','https://bitcoincore.org/en/blog/'),
 'aave':('https://governance.aave.com','discourse','https://governance.aave.com/latest.json'),
 'lido':('https://research.lido.fi','discourse','https://research.lido.fi/latest.json'),
 'magicians':('https://ethereum-magicians.org','discourse','https://ethereum-magicians.org/latest.json'),
 'coinbase_institutional':('https://www.coinbase.com','sitemap','https://www.coinbase.com/sitemap-institutional.xml'),
 'coindesk':('https://www.coindesk.com','sitemap','https://www.coindesk.com/sitemap-index.xml'),
 'decrypt':('https://decrypt.co','sitemap','https://decrypt.co/sitemap_index.xml'),
 'sec':('https://www.sec.gov','index','https://www.sec.gov/newsroom/press-releases'),
 'federal_reserve':('https://www.federalreserve.gov','index','https://www.federalreserve.gov/newsevents/pressreleases.htm'),
}

def contracts(library=None):
 rows=[]
 def add(name,provider,frequency,units,asset='ETH',scope='',**extra):
  rows.append({'dataset_id':name,'provider':provider,'frequency':frequency,'units':units,'asset':asset,
               'scope':scope,'required':True,**extra})
 for asset in ('ETH','BTC'):
  unit={key:'USD' for key in ('open_usd','high_usd','low_usd','close_usd')};unit['volume_asset']=asset
  for frequency in ('hourly','daily'):
   add(f'market.coinbase.{asset.lower()}.{frequency}','coinbase','1h' if frequency=='hourly' else '1d',unit,asset,
       f'Coinbase Exchange {asset}-USD completed UTC OHLCV; single venue')
  add(f'market.coingecko.{asset.lower()}.daily','coingecko','1d',{'price_usd':'USD','market_cap_usd':'USD','rolling_24h_volume_usd':'USD'},asset,'CoinGecko dated aggregate reference; not an executable quote')
  add(f'analysis.{asset.lower()}.hourly','derived','1h',
      {key:'fraction' for key in ('return_1h','return_24h','return_7d','return_30d','realized_vol_7d','max_drawdown_30d','distance_sma24','distance_sma168','range_fraction','volume_vs_prior24')},asset,
      'Hourly close-derived descriptive indicators; volatility annualized using 365*24 hourly periods; no forecasts')
  add(f'analysis.{asset.lower()}.daily','derived','1d',
      {key:'fraction' for key in ('return_1d','return_3d','return_7d','return_21d','return_30d','realized_vol_7d','realized_vol_21d','max_drawdown_30d','distance_sma21','volume_vs_prior20','range_fraction')},asset,'Daily close-derived descriptive indicators, 365-day annualization')
 add('analysis.eth_btc.daily','derived','1d',{'eth_btc_ratio':'BTC/ETH','relative_return_7d':'fraction','relative_return_30d':'fraction','return_correlation_90d':'dimensionless'},scope='Aligned Coinbase daily ETH/BTC closes and 90-day daily return correlation')
 add('network.coinmetrics.eth.daily','coinmetrics','1d',{'active_address_count':'count','transaction_count':'count','total_fees_eth':'ETH','gas_used':'gas','mean_fee_eth':'ETH/transaction'},scope='Coin Metrics Ethereum daily network metrics; addresses are not people; transaction fee is not base fee')
 add('network.coinmetrics.eth.supply','coinmetrics','1d',{'supply_eth':'ETH','gross_issuance_eth':'ETH','signed_burn_eth':'ETH','staked_eth':'ETH','active_validator_count':'count','supply_change_eth':'ETH/day'},scope='Coin Metrics source-defined daily supply/staking; signed burn retains provider convention')
 add('network.ethereum.base_fee.daily','ethereum','1d',{'mean_base_fee_wei_per_gas':'wei/gas'},scope='Daily execution-layer base fee; source must explicitly identify base fee, not total gas price')
 add('flows.coinmetrics.eth.daily','coinmetrics','1d',{'exchange_inflow_eth':'ETH','exchange_outflow_eth':'ETH'},scope='Provider-labelled exchange transfers; not verified investor purchases/sales')
 add('derivatives.okx.eth.oi.hourly','okx','1h',{'oi_contracts':'contracts','oi_eth':'ETH','oi_usd':'USD','oi_change_fraction':'fraction','oi_change_usd':'USD'},scope='OKX ETH-USDT-SWAP historical open interest; USD exposure includes repricing')
 add('derivatives.okx.eth.funding','okx','settlement',{'funding_rate_fraction':'fraction/settlement','realized_rate_fraction':'fraction/settlement'},scope='OKX ETH-USDT-SWAP actual settlement timestamps; no assumed eight-hour interval')
 add('analysis.eth.funding','derived','settlement',{'latest_settled_rate_fraction':'fraction/settlement','median_settled_rate_fraction':'fraction/settlement','empirical_percentile_fraction':'fraction'},scope='Expanding same-settlement-interval history through each observation; interval changes split groups')
 for chain in CHAINS:
  for metric,unit in [('tvl_usd','USD'),('stablecoin_circulating_value_usd','USD'),('dex_volume_usd','USD/day')]:
   add(f'ecosystem.{chain.lower()}.{metric}','defillama','1d',{metric:unit},scope=f'DefiLlama {chain} covered series; TVL/stablecoin stock changes are not net flow')
 for field in ('fees_usd','revenue_usd'):
  add('protocol.aave_v3.'+field,'defillama','1d',{field:'USD/day'},scope='Aave V3 all source-covered chains; not Ethereum-only or ETH token revenue')
 for pool in ('lido.steth','aave_v3.weth'):
  add('yields.'+pool,'defillama','provider_daily',{'apy_percent':'percent/year','apy_base_percent':'percent/year','tvl_usd':'USD'},scope='Ethereum named pool provider snapshot times; not guaranteed realized yield')
 add('flows.farside.eth_etf.daily','farside','business_day',{'net_flow_usd':'USD/trading day'},scope='US spot ETH ETF aggregate, Farside publisher table; non-trading days have no observations')
 for series in ('DFF','DGS2','DGS10'):
  add('macro.fred.'+series.lower(),'federal_reserve','business_day',{'rate_percent':'percent/year'},asset='USD',scope=f'FRED {series}, Federal Reserve source; missing publication and non-observation dates are explicit')
 add('macro.federal_reserve.fomc.calendar','federal_reserve','calendar',{'scheduled_meeting':'count'},asset='USD',scope=('Official scheduled FOMC meeting dates; zero meetings in this fixed window. Adjacent meetings retained in calendar metadata.' if library is None or (library.window_start==WINDOW_START and library.window_end==WINDOW_END) else 'Official scheduled FOMC meeting dates in the configured window; adjacent meetings retained in calendar metadata.'))
 add('execution.coinbase.eth.snapshot','coinbase','snapshot',{'best_bid_usd':'USD','best_ask_usd':'USD','spread_bps':'bps','bid_depth_10bps_usd':'USD','ask_depth_10bps_usd':'USD','bid_depth_50bps_usd':'USD','ask_depth_50bps_usd':'USD','bid_depth_100bps_usd':'USD','ask_depth_100bps_usd':'USD','estimated_vwap_1000usd':'USD','slippage_1000usd_bps':'bps','covered_notional_1000usd':'USD'},scope='Current Coinbase top25 levels; USD1000 is an explicitly labelled execution scenario before fees, not a user order')
 return rows

def install(library):
 for item in contracts(library):
  name=item.pop('dataset_id');library.register_dataset(name,**item)
 for name,(base,strategy,url) in TEXT_SOURCES.items():
  library.register_source(name,base_url=base,discovery=strategy,url=url,window_start=library.window_start,window_end=library.window_end,
                          material_scope='Source-specific window discovery; original material not a model summary',redistribution='Review publisher terms; raw material excluded from public source ZIP')
 for name,base in {'coinbase':'https://api.exchange.coinbase.com','coingecko':'https://api.coingecko.com','coinmetrics':'https://community-api.coinmetrics.io','okx':'https://www.okx.com','defillama':'https://api.llama.fi','stablecoins':'https://stablecoins.llama.fi','farside':'https://farside.co.uk','etherscan':'https://etherscan.io','beaconchain':'https://beaconcha.in'}.items():
  library.register_source(name,base_url=base,window_start=library.window_start,window_end=library.window_end,material_scope='Source-defined financial data; inspect dataset contracts and original captures',redistribution='Review source terms before redistribution; not included in public source ZIP')
