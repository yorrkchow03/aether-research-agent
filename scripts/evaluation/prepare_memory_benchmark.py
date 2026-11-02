"""Capture a bounded official-source pilot. No model calls, keys or shared memory.

Raw pages stay in private data/validation. Failure is retained, never replaced by
invented content. This is a current capture, not a historical availability claim.
"""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import json

from research_agent.public_web import fetch_page
from research_data.http import utcnow

ROOT = Path(__file__).resolve().parents[2]
CASES = [
    ('glamsterdam', 'development', '2026/09/17/glamsterdam-testnet-announcement'),
    ('transaction-assertions', 'development', '2026/10/05/transaction-assertions'),
    ('zkapi', 'development', '2026/10/01/introducing-zkapi'),
    ('hegota', 'development', '2026/09/07/protocol-hegota-eips'),
    ('protocol-priorities', 'development', '2026/09/07/protocol-priorities'),
    ('codes-challenge', 'held_out', '2026/08/20/better-codes-challenge'),
    ('allocations', 'held_out', '2026/08/18/allocation-q2-26'),
    ('webcat', 'held_out', '2026/08/05/1ts-grant'),
    ('board', 'held_out', '2026/07/29/ef-board-update'),
    ('security-triage', 'held_out', '2026/07/09/triage-is-the-product'),
    ('institutions', 'held_out', '2026/07/01/ethereum-for-institutions'),
    ('treasury-staking', 'held_out', '2026/02/24/staking'),
    ('epf', 'held_out', '2026/04/30/epf-7'),
]


def main():
    output = ROOT / 'data/validation/memory_v1'
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'capture.json').exists():
        raise ValueError('Frozen capture exists; do not refetch or overwrite it')

    def capture(case):
        group, split, path = case
        url = 'https://blog.ethereum.org/en/' + path
        meta = {'event_group': group, 'split': split, 'requested_url': url}
        try:
            page = fetch_page(url)
            raw = page.pop('raw')
            digest = sha256(raw).hexdigest()
            seen = utcnow()
            meta.update(page, raw_sha256=digest, retrieved_at=seen,
                        record_id='page-' + sha256((url + digest + seen).encode()).hexdigest()[:24],
                        status='captured')
            (output / (group + '.raw')).write_bytes(raw)
        except Exception as exc:
            meta.update(status='failed', error=str(exc))
        (output / (group + '.json')).write_text(json.dumps(meta, indent=2) + '\n')
        return {k: v for k, v in meta.items() if k != 'text'}

    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(capture, CASES))
    manifest = {'version': 'official-memory-capture-v1', 'created_at': utcnow(),
                'sources': rows, 'scope': 'One current capture per topic; not independently observed revision histories or past public availability.'}
    (output / 'capture.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'captured': sum(r['status'] == 'captured' for r in rows),
                      'failed': [r['event_group'] for r in rows if r['status'] != 'captured']}))


if __name__ == '__main__':
    main()
