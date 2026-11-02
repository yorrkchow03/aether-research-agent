"""Resumable real OHLCV capture and strict offline frozen-corpus loading."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
import re
import math
import httpx

from research_agent.investment.history import normalize_history
from research_agent.live import atomic
from research_data.http import dumps, utcnow
from research_data.normalize import iso, timestamp

SCHEMA = 'aether-signal-corpus-v1'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(value):
    return hashlib.sha256(dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_protocol(protocol):
    if protocol.get('schema') != 'aether-signal-protocol-v1':
        raise ValueError('Unknown signal protocol schema')
    assets = protocol.get('assets', [])
    if not assets or len(set(assets)) != len(assets) or any(a not in ('ETH', 'BTC') for a in assets):
        raise ValueError('Protocol needs unique ETH/BTC assets')
    begin, end = timestamp(protocol['start']), timestamp(protocol['end'])
    if not begin < end or any(t.hour or t.minute or t.second or t.microsecond for t in (begin, end)):
        raise ValueError('Corpus bounds must be increasing UTC midnights')
    if (end-begin).days > 3000:
        raise ValueError('Corpus capture is bounded to3000 days per asset')
    windows = protocol.get('windows', [])
    if not windows or len(windows) > 24 or len({w['id'] for w in windows}) != len(windows):
        raise ValueError('Protocol needs unique bounded windows')
    previous = begin; order = {'development': 0, 'validation': 1, 'confirmation': 2}; phase = -1
    for window in windows:
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', window['id']):raise ValueError('Invalid window identifier')
        split = window['split']; first, last = timestamp(window['start']), timestamp(window['end'])
        if split not in order or order[split] < phase or not begin <= first < last <= end or first < previous:
            raise ValueError('Windows must be nonoverlapping chronological development/validation/confirmation partitions')
        if any(t.hour or t.minute or t.second or t.microsecond for t in (first, last)):
            raise ValueError('Window bounds must be UTC midnights')
        previous = last; phase = order[split]
    if set(w['split'] for w in windows) != set(order):
        raise ValueError('Separate development, validation and confirmation windows are required')
    evaluation = protocol['evaluation']
    if not math.isfinite(evaluation['logistic_C']) or not 0 < evaluation['logistic_C'] <= 10:raise ValueError('Invalid regularization')
    if evaluation['execution_delay_bars'] not in (1, 2) or evaluation['minimum_training_samples'] < 100:
        raise ValueError('Explicit delayed execution and at least100 earlier training labels are required')
    if not 0 < evaluation['allocation_cap'] <= 1 or not 0 < evaluation['cash_probability'] < .5 < evaluation['long_probability'] < 1:
        raise ValueError('Invalid prespecified research thresholds')
    if not 1 <= evaluation['bootstrap_block_days'] <= 90 or not 20 <= evaluation['bootstrap_draws'] <= 1000:
        raise ValueError('Invalid time-block uncertainty bounds')
    costs = evaluation['cost_scenarios']
    if len({c['id'] for c in costs}) != len(costs) or not any(c['id'] == 'base' for c in costs):
        raise ValueError('Unique cost scenarios and a base scenario are required')
    if any(not 0 <= c[k] <= 500 for c in costs for k in ('fee_bps', 'slippage_bps')):
        raise ValueError('Invalid cost assumptions')
    return protocol


def prepare_corpus(output, protocol, live, *, sleep=time.sleep, page_days=250, attempts=2):
    """Capture once; a partially completed identical capture can resume."""
    validate_protocol(protocol)
    output = Path(output).resolve()
    if (output/'freeze.json').exists() or (output/'manifest.json').exists():
        raise ValueError('Frozen corpus exists; choose a new capture, never overwrite it')
    if not isinstance(page_days, int) or not 1 <= page_days <= 250 or not 1 <= attempts <= 3:
        raise ValueError('Capture paging/retry bounds invalid')
    end = timestamp(protocol['end']); begin = timestamp(protocol['start'])
    if end > datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0):
        raise ValueError('The requested final UTC day has not completed')
    output.mkdir(parents=True, exist_ok=True)
    config = output/'protocol.json'
    if config.exists() and json.loads(config.read_text()) != protocol:
        raise ValueError('Partial capture belongs to a different frozen protocol')
    if not config.exists():atomic(config, protocol)
    included = {'protocol.json'}; datasets = []
    for asset in protocol['assets']:
        cursor = begin; pages = []; index = 0
        while cursor < end:
            boundary = min(end, cursor+timedelta(days=page_days))
            params = {'granularity': 86400, 'start': iso(cursor), 'end': iso(boundary)}
            relative = f'receipts/{asset.lower()}-{index:03d}.json'; meta_path = output/relative
            raw_relative = f'raw/{asset.lower()}-{index:03d}.bin'
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
                if meta['request']['params'] != params or meta['raw_file'] != raw_relative or digest(output/raw_relative) != meta['raw_sha256']:
                    raise ValueError('Partial capture source/config changed')
            else:
                for attempt in range(attempts):
                    try:
                        row = live.request('coinbase', f'/products/{asset}-USD/candles', params, ttl=3600)['record']
                        body = Path(row['raw_path']).read_bytes()
                        if hashlib.sha256(body).hexdigest() != row['source']['sha256']:
                            raise ValueError('Provider raw receipt changed')
                        meta = {'record_id': row['record_id'], 'asset': asset,
                            'request': {'provider': 'coinbase', 'path': f'/products/{asset}-USD/candles', 'params': params},
                            'source': row['source'], 'retrieved_at': row['retrieved_at'],
                            'raw_file': raw_relative, 'raw_sha256': hashlib.sha256(body).hexdigest()}
                        (output/'raw').mkdir(exist_ok=True); (output/raw_relative).write_bytes(body)
                        atomic(meta_path, meta)
                        break
                    except (ValueError, RuntimeError, OSError, httpx.HTTPError) as exc:
                        failure = {'asset': asset, 'page': index, 'attempt': attempt+1, 'time': utcnow(), 'error': str(exc)[:450]}
                        with (output/'capture_failures.jsonl').open('a') as f:f.write(dumps(failure)+'\n')
                        if attempt+1 == attempts:raise
                        sleep(1.5*2**attempt)
                sleep(.6)  # Conservative collection, far below the documented public IP rate.
            included.update((relative, raw_relative))
            pages.append(meta); index += 1; cursor = boundary
            atomic(output/'capture_state.json', {'asset': asset, 'completed_pages': index, 'latest_boundary': iso(boundary), 'state': 'capturing'})
        inputs = [{**m, 'raw_path': str(output/m['raw_file'])} for m in pages]
        normalized = normalize_history(inputs, asset, protocol['start'], protocol['end'])
        now = utcnow(); rid = 'live-'+identity({'asset': asset, 'series': normalized['series'], 'sources': [m['record_id'] for m in pages]})[:24]
        history = {'record_id': rid, 'dataset_id': 'live.validation_history.'+asset.lower(), 'asset': asset,
            'observed_at': normalized['series'][-1]['period_end'], 'retrieved_at': now, 'available_at': now,
            'series': normalized['series'], 'values': normalized['values'], 'units': normalized['units'],
            'input_record_ids': [m['record_id'] for m in pages], 'source_receipts': [f'receipts/{asset.lower()}-{i:03d}.json' for i in range(len(pages))],
            'method': 'Union preserved Coinbase daily candle pages; reject conflicts/gaps and unfinished bars',
            'scope': 'Single-venue retrospective completed USD OHLCV; capture now does not authenticate past public availability',
            'quality_flags': ['retrospective_current_retrieval', 'not_strict_point_in_time', 'frozen_validation_corpus']}
        relative = f'histories/{asset.lower()}.json'; atomic(output/relative, history); included.add(relative)
        datasets.append({'asset': asset, 'file': relative, 'record_id': rid, 'candles': len(history['series']), 'source_pages': len(pages)})
    if (output/'capture_failures.jsonl').exists():included.add('capture_failures.jsonl')
    manifest = {'schema': SCHEMA, 'created_at': utcnow(), 'protocol_sha256': digest(config),
        'datasets': datasets, 'start': protocol['start'], 'end': protocol['end'],
        'files': {name: digest(output/name) for name in sorted(included)},
        'scope': 'Immutable real-data capture for offline retrospective validation; no future network refresh, personal data or model labels.'}
    atomic(output/'manifest.json', manifest)
    atomic(output/'freeze.json', {'schema': SCHEMA, 'manifest_sha256': digest(output/'manifest.json'), 'created_at': utcnow()})
    atomic(output/'capture_state.json', {'state': 'frozen', 'datasets': datasets})
    return {'manifest_sha256': digest(output/'manifest.json'), 'datasets': datasets, 'files': len(included)}


def _inside(root, name):
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts or '\\' in name:
        raise ValueError('Frozen source path escaped the corpus')
    path = root/relative
    if not path.resolve().is_relative_to(root.resolve()) or any((root/Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts)+1)):
        raise ValueError('Frozen source symlink/traversal refused')
    return path


def load_corpus(root, *, expected_manifest_sha256=None):
    """Only filesystem reads. No live adapter, key, network or model exists here."""
    root = Path(root).resolve(); freeze = json.loads((root/'freeze.json').read_text())
    manifest_hash = digest(root/'manifest.json')
    if manifest_hash != freeze['manifest_sha256'] or (expected_manifest_sha256 and manifest_hash != expected_manifest_sha256):
        raise ValueError('Frozen corpus manifest identity changed')
    manifest = json.loads((root/'manifest.json').read_text())
    if manifest['schema'] != SCHEMA:raise ValueError('Unknown frozen corpus schema')
    for name, checksum in manifest['files'].items():
        if digest(_inside(root, name)) != checksum:raise ValueError('Frozen source bytes changed: '+name)
    protocol = validate_protocol(json.loads((root/'protocol.json').read_text()))
    if digest(root/'protocol.json') != manifest['protocol_sha256']:
        raise ValueError('Frozen protocol identity changed')
    histories = {}
    for item in manifest['datasets']:
        if item['file'] not in manifest['files']:raise ValueError('Unfrozen history reference')
        history = json.loads(_inside(root, item['file']).read_text()); inputs = []
        for name in history['source_receipts']:
            if name not in manifest['files']:raise ValueError('Unfrozen receipt reference')
            receipt = json.loads(_inside(root, name).read_text()); raw = _inside(root, receipt['raw_file'])
            if receipt['raw_file'] not in manifest['files'] or digest(raw) != receipt['raw_sha256']:
                raise ValueError('Frozen raw receipt changed')
            inputs.append({**receipt, 'raw_path': str(raw)})
        normalized = normalize_history(inputs, item['asset'], protocol['start'], protocol['end'])
        if history['asset'] != item['asset'] or history['series'] != normalized['series'] or history['input_record_ids'] != [r['record_id'] for r in inputs]:
            raise ValueError('Normalized history does not reproduce preserved originals')
        if len(history['series']) != item['candles'] or history['record_id'] != item['record_id']:
            raise ValueError('History manifest count/identity changed')
        histories[item['asset']] = history
    if set(histories) != set(protocol['assets']):raise ValueError('Frozen corpus asset coverage incomplete')
    return {'protocol': protocol, 'histories': histories, 'manifest': manifest, 'manifest_sha256': manifest_hash, 'root': root}
