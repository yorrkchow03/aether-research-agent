"""Optional source-ratio proxy, distinct from the required block arithmetic mean."""
from decimal import Decimal
from pathlib import Path
import json
from research_data.http import dumps

DATASET='analysis.eth.base_fee_ratio_proxy.daily'
VERSION='etherscan-burn-gas-ratio-proxy-v1'
SCOPE='Publisher burn ETH / publisher gas used * 1e18. A ratio proxy; publisher burn scope is not independently authenticated for the whole window. Not arithmetic block-mean base fee, total gas price, consensus burn or a substitute for the required base-fee dataset.'


def derive_proxy(library):
    library.register_dataset(DATASET,provider='derived',frequency='1d',asset='ETH',required=False,scope=SCOPE,
        units={'burn_to_gas_ratio_wei_per_gas':'wei/gas','display_half_unit_error_wei_per_gas':'wei/gas'})
    gas={r['observed_at']:r for r in library.series('network.etherscan.gas.daily',limit=1000)['records']}
    burn=library.series('network.etherscan.burn.daily',limit=1000)['records']
    implementation=library.object(Path(__file__).read_bytes());count=0;unknown=[]
    for b in burn:
        g=gas.get(b['observed_at']);used=Decimal(g['values']['gas_used']) if g else None
        if used is None or used<=0:unknown.append(b['observed_at']);continue
        amount=Decimal(b['values']['execution_fee_burn_eth']);raw_value=b['library_metadata']['original_row']['BurntFees']
        quantum=Decimal(1).scaleb(Decimal(raw_value).as_tuple().exponent)
        value=amount*Decimal(10**18)/used;half_unit=quantum*Decimal('0.5')*Decimal(10**18)/used
        ids=[b['record_id'],g['record_id']]
        manifest={'algorithm':VERSION,'implementation_sha256':implementation,'input_ids':ids,'scope':SCOPE}
        cid=library.capture('calculation:'+VERSION,'urn:aether:calculation:'+DATASET+':'+b['observed_at'][:10],dumps(manifest,sort_keys=True).encode())
        library.put(DATASET,b['observed_at'],{'burn_to_gas_ratio_wei_per_gas':value,'display_half_unit_error_wei_per_gas':half_unit},cid,
            period_end=b['period_end'],input_ids=ids,metadata={'calculation_version':VERSION,'scope_status':'ratio_proxy_publisher_burn_scope_uncertain',
                'publisher_burn_eth_string':raw_value,'publisher_gas_integer_string':str(used),'publisher_decimal_quantum_eth':str(quantum),
                'precision_scope':'Half of last displayed decimal unit only; publisher rounding convention not asserted',
                'input_source_hashes':{r['record_id']:r['source']['sha256'] for r in (b,g)}})
        count+=1
    library.job(DATASET,'collected',source_range_complete=count==library.days,observations=count,unknown_denominator_dates=unknown,scope_status='ratio_proxy_publisher_burn_scope_uncertain')
    return {'observations':count,'unknown_denominator_dates':unknown,'scope':SCOPE}


def audit_proxy(library,row):
    manifest=json.loads(library.raw(row['library_capture_id']))
    if manifest['algorithm']!=VERSION or row['input_record_ids']!=manifest['input_ids']:raise ValueError('Ratio proxy input mapping differs')
    implementation=manifest['implementation_sha256']
    path=library.root/'objects'/implementation[:2]/(implementation+'.bin')
    import hashlib
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=implementation:raise ValueError('Missing ratio proxy implementation')
    b,g=[library.evidence(rid) for rid in row['input_record_ids']]
    if b['dataset_id']!='network.etherscan.burn.daily' or g['dataset_id']!='network.etherscan.gas.daily' or b['observed_at']!=g['observed_at'] or row['observed_at']!=b['observed_at']:raise ValueError('Proxy parents differ in source type/date')
    meta=row['library_metadata'];raw=b['library_metadata']['original_row']['BurntFees'];used=Decimal(g['values']['gas_used'])
    if used<=0:raise ValueError('Undefined proxy denominator')
    quantum=Decimal(1).scaleb(Decimal(raw).as_tuple().exponent)
    expected={'burn_to_gas_ratio_wei_per_gas':Decimal(raw)*10**18/used,'display_half_unit_error_wei_per_gas':quantum*Decimal('0.5')*10**18/used}
    if any(Decimal(row['values'][key])!=value for key,value in expected.items()):raise ValueError('Ratio proxy differs from saved Decimal inputs')
    if meta['publisher_burn_eth_string']!=raw or meta['input_source_hashes']!={r['record_id']:r['source']['sha256'] for r in (b,g)}:raise ValueError('Ratio proxy precision/raw source mapping differs')
