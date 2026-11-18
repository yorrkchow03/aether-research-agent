"""Cold replay from a portable bundle; no network or original absolute paths required."""
from __future__ import annotations
import json
import math
import shlex
import tempfile
from decimal import Decimal
from pathlib import Path
from .audit import verify_bundle
from .reports import ReportDraft,validate_report
from .review import check_review,check_saved_scope_critic
from .simulation import market_statistics,compare_strategies
from .terminal import ResearchShell


def same_numbers(actual,expected):
    if set(actual)!=set(expected):return False
    for key,value in expected.items():
        if value is None or actual[key] is None:
            if value!=actual[key]:return False
        else:
            a,b=Decimal(str(actual[key])),Decimal(str(value))
            if not a.is_finite() or not b.is_finite() or abs(a-b)>max(abs(b)*Decimal('1e-9'),Decimal('1e-11')):return False
    return True


def replay_bundle(directory,settings,expected=None,execute_recipes=False):
    directory=Path(directory).resolve();integrity=verify_bundle(directory,expected)
    result={'integrity':integrity,'calculation_checks':[],
            'scope':'Offline citation, numeric-field, exact-review-quote and calculation replay; does not validate source truth, method appropriateness or future returns',
            'recipe_execution_enabled':execute_recipes}
    if integrity['status']!='passed':return {**result,'status':'failed'}
    index=json.loads((directory/'replay_index.json').read_text())
    records={rid:json.loads((directory/path).read_text()) for rid,path in index['records'].items()}
    report=json.loads((directory/'report/report.json').read_text())
    saved_validation=report.get('validation',{})
    check_etf_flows='farside_dated_totals_checked' in saved_validation

    def raw_reader(row):
        return (directory/'raw'/(row['source']['sha256']+'.bin')).read_bytes()

    try:
        draft=ReportDraft.model_validate(report['report'])
        if getattr(draft,'scope',None):
            from .report_presentation import build_report_presentation
            saved=report['presentation']
            overview=saved['overview']
            evidence_as_of=overview['evidence_as_of']
            result['numeric_validation']=validate_report(draft,records.__getitem__,as_of=evidence_as_of,
                raw_reader=raw_reader,check_etf_flows=check_etf_flows)
            rebuilt=build_report_presentation(draft,records.__getitem__,user_prompt=overview['user_prompt'],
                created_at=report['created_at'],evidence_as_of=evidence_as_of,raw_reader=raw_reader)
            if rebuilt!=saved:
                raise ValueError('Stored report presentation differs from the replayed source projection')
            result['presentation_validation']='passed'
        else:
            result['numeric_validation']=validate_report(draft,records.__getitem__,raw_reader=raw_reader,
                check_etf_flows=check_etf_flows)
        if check_etf_flows and result['numeric_validation'].get('farside_dated_totals_checked')!=saved_validation['farside_dated_totals_checked']:
            raise ValueError('Saved Farside row validation count differs from the original table replay')
    except (ValueError,KeyError,OSError) as exc:return {**result,'status':'failed','error':str(exc)}
    review=report.get('review',{})
    if review.get('review_id'):
        saved=json.loads((directory/'review'/(review['review_id']+'.json')).read_text())
        try:
            if getattr(draft,'scope',None) and getattr(draft,'delivery_kind','full')!='evidence_gap':
                from .review import saved_report_review_targets
                expected_targets=saved_report_review_targets(draft,saved)
                saved_targets=[]
                for target, expected_target in zip(saved['targets'],expected_targets):
                    ids=target['evidence_ids']
                    if target['kind'] in ('summary','judgment'):
                        ids=[rid for rid in ids if rid in expected_target['evidence_ids'] or
                             not rid.startswith('context-') or records.get(rid,{}).get('dataset_id')!='local.user_research_parameters']
                    saved_targets.append({**target,'evidence_ids':ids})
                if len(saved['targets'])!=len(expected_targets) or saved_targets!=expected_targets:
                    raise ValueError('Saved review does not cover the report and featured-document targets')
            from .review import validate_saved_report_review
            check=validate_saved_report_review(saved,draft,lookup=records.__getitem__)
            result['review_quote_validation']={'status':'passed','claims_checked':check['claims_checked'],'semantic_status':check['status']}
        except (ValueError,KeyError) as exc:return {**result,'status':'failed','error':str(exc)}
    else:result['review_quote_validation']={'status':'not_available'}
    parent=None
    if getattr(settings,'restricted_execution_root',None):
        from .execution_safety import execution_temp_parent
        parent=execution_temp_parent(settings)
    with tempfile.TemporaryDirectory(prefix='aether-cold-replay-',dir=parent) as work:
        work=Path(work);inputs=work/'inputs';inputs.mkdir()
        # Absolute paths in archived records are provenance only. Re-map to this bundle.
        for rid,row in records.items():
            clone=dict(row)
            if clone.get('raw_path') and clone.get('source'):
                clone['raw_path']=str(directory/'raw'/(clone['source']['sha256']+'.bin'))
            if clone.get('recipe_path'):clone['recipe_path']=str(directory/'recipes'/(rid+'.py'))
            (inputs/(rid+'.json')).write_text(json.dumps(clone,ensure_ascii=False))
        for calculation in index['calculations']:
            rid=calculation['record_id'];row=records[rid];entry={'record_id':rid,'method':calculation.get('trusted_method') or 'saved_python_recipe'}
            try:
                if calculation.get('recipe'):
                    if not execute_recipes:
                        entry.update(status='not_run',reason='Saved Python execution requires --execute-recipes or the explicit replay action')
                        result['calculation_checks'].append(entry);continue
                    shell=ResearchShell(work,settings)
                    recipe=(directory/calculation['recipe']).resolve()
                    args=['python','-I',str(recipe),*[str(inputs/(r+'.json')) for r in calculation['input_ids']]]
                    output=shell.execute(shlex.join(args),timeout=60,separate_stderr=True)
                    if output.exit_code or output.truncated:raise ValueError('Recipe failed: '+output.output[:1000])
                    obj=json.loads(output.output);values=obj['values']
                    if obj['units']!=row['units']:raise ValueError('Replayed units differ')
                elif calculation.get('trusted_method')=='analysis-v1':
                    series=[records[r] for r in calculation['input_ids']]
                    stats=market_statistics(series,len(series)-1)
                    values={k:stats[k] for k in ('return_fraction','annualized_volatility_fraction','max_drawdown_fraction')}
                    values['latest_price_usd']=series[-1]['values']['price_usd']
                elif calculation.get('trusted_method')=='paper-v1':
                    original=row['simulation']['results'][0];p=original['profile'];lookback=original['signal_lookback_days']
                    series=records[calculation['input_ids'][0]]['series'][-(p['horizon_days']+lookback+1):]
                    simulation=compare_strategies(series,p,lookback);values={}
                    for strategy in simulation['results']:
                        for key in ('net_return_fraction','max_drawdown_fraction','final_equity_usd','fees_usd','slippage_usd'):
                            values[strategy['strategy']+'_'+key]=strategy[key]
                else:
                    entry.update(status='not_run',reason='Unknown calculation version');result['calculation_checks'].append(entry);continue
                entry.update(status='passed' if same_numbers(values,row['values']) else 'failed',recomputed_values=values,
                             independent_method_check='Descriptive statistics independently recomputed' if calculation.get('trusted_method')=='analysis-v1' else 'Exact saved recipe/preset replay only; chosen method is not independently validated')
            except (ValueError,KeyError,OSError) as exc:entry.update(status='failed',error=str(exc)[:1000])
            result['calculation_checks'].append(entry)
    statuses=[c['status'] for c in result['calculation_checks']]
    result['status']='failed' if 'failed' in statuses else 'partial' if 'not_run' in statuses else 'passed'
    result['calculations_recomputed']=statuses.count('passed')
    result['calculation_implementation']='Installed application version; bundle software is preserved for exact-version restoration using its environment lock'
    return result
