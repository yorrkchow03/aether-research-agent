"""Deterministic experiment summaries, distinct from an analyst's reviewed report."""
def render_experiment(result,sources):
    cfg=result['config'];score=result['holdout_scores'];split=result['split']
    lines=[f"# {cfg['asset']} strategy experiment",'',f"Saved run: `{result['run_id']}` · {result['created_at']}",'',
        '**Retrospective model experiment, not a recommendation or a reviewed news report.**','',
        f"Model selected by earlier validation Brier: **{result['model_selected']}**.",
        f"Holdout decisions: {split['holdout_start']} through {split['holdout_end']} ({score['samples']} observations).",'',
        f"Direction accuracy: {score['directional_accuracy']:.4%}. Brier error: {score['brier_score']:.6f}; constant-prior baseline: {result['constant_probability_brier']:.6f}."]
    lines+=['','Probability error '+('improved' if score['brier_score']<result['constant_probability_brier'] else 'did not improve')+' against the constant-prior baseline in this holdout. Simulated profits alone do not establish prediction skill.','',
        '| Strategy | Net return | Max drawdown | Fees + slippage (USD) | Mean marked exposure |',
        '| --- | ---: | ---: | ---: | ---: |']
    for row in result['comparison']['results']:
        lines.append(f"| {row['strategy']} | {row['net_return_fraction']:.4%} | {row['max_drawdown_fraction']:.4%} | {row['fees_usd']+row['slippage_usd']:.6f} | {row['mean_marked_exposure_fraction']:.4%} |")
    lines+=['','## Assumptions','',f"Paper capital: USD {cfg['budget_usd']}; allocation cap: {cfg['allocation_cap']:.2%}; fee {cfg['fee_bps']} bps and slippage {cfg['slippage_bps']} bps per side; terminal liquidation included.",
        f"Full-bar execution delay: {cfg['execution_delay_bars']}; refit cadence: {cfg['refit_every']} bars. Probability bands {cfg['cash_probability']} / {cfg['long_probability']} and target volatility {cfg['target_volatility']} are research assumptions.",
        'Features and training labels are strictly earlier than each decision. Same evaluation dates and capital; realized exposure differs between strategies.','',
        '## Latest estimate','',f"Feature cutoff {result['latest']['decision_at']}; earliest execution {result['latest']['execute_not_before']}. Estimated up probability {result['latest']['probability_up']:.4%}; state `{result['latest']['state']}`. This is an uncalibrated model estimate, not a sourced future fact.",'','## Saved sources','',
        f"Derived experiment record: `{result['evidence_record_id']}`; normalized history: `{result['source_record_id']}`. Model, folds, decisions, config, raw source hashes, recipe and environment are saved with the local experiment."]
    for row in sources:
        src=row['source'];lines.append(f"- `{row['record_id']}` · [Coinbase OHLCV original API]({src['url']}) · retrieved {row['retrieved_at']} · SHA-256 `{src['sha256']}`")
    lines+=['','## Limitations','',*['- '+s for s in result['limitations']],'']
    return '\n'.join(lines)
