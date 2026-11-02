"""Explicit hypotheses for the existing OHLCV features, not new trading rules."""
from copy import deepcopy

from research_agent.investment.strategies import FEATURES

VERSION = 'ohlcv-signal-contracts-v1'


def _signal(key, name, formula, lookback, role, hypothesis, alternative, failure):
    return {'id': key, 'name': name, 'formula': formula, 'unit': 'fraction',
        'lookback_periods': lookback, 'role': role, 'available_at': 'Last input bar period_end, after the full UTC bar closes',
        'inputs': ['Completed single-venue daily OHLCV'], 'rationale': hypothesis,
        'competing_explanation': alternative, 'failure_conditions': failure,
        'evaluation': ['Single-feature training-only-scaled logistic baseline',
            'Leave-one-feature-out refit versus the same full logistic model',
            'Identical eligible target rows, delayed execution and cost scenarios'],
        'scope': 'Research hypothesis; neither causal explanation nor independently established investment edge'}


_CATALOG = []
for n in (1, 3, 7, 21):
    _CATALOG.append(_signal('return_'+str(n)+'d', str(n)+'-day close return',
        f'close[t] / close[t-{n}] - 1', n, 'Directional hypothesis',
        'Recent price persistence might predict later direction; sign and transfer must be evaluated on earlier-data-trained models.',
        'Short-horizon reversal, common market exposure and correlated return windows can explain apparent performance.',
        ['Regime changes or reversal', 'Missing/nonpositive prices', 'Costs consume a small forecast effect']))

_CATALOG += [
    _signal('realized_vol_7d', '7-day realized log-return volatility',
        'sample_std(log(close[j] / close[j-1]), j=t-6..t, ddof=1)', 7, 'Risk/state feature',
        'Recent dispersion describes risk and may identify different conditional environments; it has no presumed bullish sign.',
        'Volatility clustering does not itself imply predictable return direction.',
        ['Too few contiguous closes', 'Volatility regime changes', 'Risk variation mistaken for directional alpha']),
    _signal('realized_vol_21d', '21-day realized log-return volatility',
        'sample_std(log(close[j] / close[j-1]), j=t-20..t, ddof=1)', 21, 'Risk/state feature',
        'A longer risk estimate can qualify exposure and changes in the short risk estimate.',
        'It overlaps the short estimate and may add no conditional information.',
        ['Abrupt jumps', 'Stale windows', 'Redundancy with shorter volatility']),
    _signal('distance_sma21', 'Distance from 21-close average',
        'close[t] / mean(close[t-20:t+1]) - 1', 20, 'Trend/reversal hypothesis',
        'Distance from a trailing level may describe persistence or overextension; the evaluation does not assume its sign.',
        'This largely re-expresses recent returns and a price-level transformation.',
        ['Trend reversal', 'Correlated return features', 'Unstable or selected-after-test sign']),
    _signal('drawdown_21d', 'Trailing 21-close peak distance',
        'close[t] / max(close[t-20:t+1]) - 1', 20, 'Risk/state feature',
        'Distance below a recent peak describes downside state and may qualify conditional risk.',
        'An apparent recovery or continuation effect can be market beta or hindsight.',
        ['New regime', 'Short window misses older losses', 'Recovery interpreted as a guaranteed rebound']),
    _signal('volume_vs_prior20', 'Volume relative to prior 20 bars',
        'volume[t] / mean(volume[t-20:t]) - 1', 20, 'Activity/context feature',
        'Unusually high single-venue activity may qualify a price move; it does not identify buyer/seller intent.',
        'Venue share, reporting changes and two-sided churn can generate the same pattern.',
        ['Zero prior volume denominator', 'Missing volume', 'Venue/reporting changes']),
    _signal('range_fraction', 'Completed high-low range',
        '(high[t] - low[t]) / close[t]', 0, 'Intrabar risk/state feature',
        'The completed bar range describes dispersion missed by close-only returns; it has no presumed directional sign.',
        'Opening gaps, liquidity and one-venue extremes can dominate the range.',
        ['Unfinished bar', 'Invalid high/low/open/close relation', 'Range treated as executable intrabar profit']),
]

assert [r['id'] for r in _CATALOG] == FEATURES


def signal_catalog():
    return {'version': VERSION, 'signals': deepcopy(_CATALOG),
        'availability': 'Completed inputs only. Prospective capture availability is separate from retrospective economic observation time.',
        'evaluation_target': 'Delayed future daily open-to-close direction; distinct from future close-to-close return or realized trading profit.',
        'interpretation': 'All hypotheses and lookbacks are predeclared. Weak or redundant effects and model-dependent ablations remain in the results.'}
