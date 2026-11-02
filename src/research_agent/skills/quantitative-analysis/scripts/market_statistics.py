"""Reference recipe: completed daily close statistics over the saved input."""
import json
import sys
from research_agent.simulation import market_statistics

record = json.load(open(sys.argv[1]))
series = record['series']
result = market_statistics(series, len(series) - 1)
fields = ('return_fraction', 'annualized_volatility_fraction', 'max_drawdown_fraction')
print(json.dumps({'values': {key: result[key] for key in fields},
                  'units': {key: 'fraction' for key in fields},
                  'meta': {'periods': len(series) - 1, 'scope': 'Saved daily closes; descriptive only'}}))
