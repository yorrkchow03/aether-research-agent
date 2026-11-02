"""Citable saved query pages, so general Python can analyze long histories."""
import hashlib
import json
from pathlib import Path

from research_data.http import dumps, utcnow
from research_data.normalize import timestamp
from .live import atomic


def collection_core(row):
    return {key:row[key] for key in ('collection_kind','query','records','cutoff','mode')}


def collection_digest(row):
    return hashlib.sha256(dumps(collection_core(row),sort_keys=True).encode()).hexdigest()


def save_collection(workspace, packet, cutoff, mode, *, offset=0):
    rows=packet['records']
    if not rows:return None
    query={key:packet[key] for key in ('dataset','start','end','mode','known_by','total','next_offset')}
    query.update(offset=offset,query_result_fully_materialized=offset==0 and len(rows)==packet['total'],
                 scope='This snapshots one query page; all returned rows does not certify missing market dates. Inspect library coverage and time continuity.')
    row={'collection_kind':'library_series','query':query,'records':rows,'cutoff':cutoff,'mode':mode}
    sha=collection_digest(row);rid='series-'+sha[:24]
    path=Path(workspace)/'data/collections'/(rid+'.json')
    if path.exists():
        saved=json.loads(path.read_text())
        if collection_digest(saved)!=sha:raise ValueError('Saved query collection changed')
    else:
        row.update(record_id=rid,dataset_id=query['dataset']['dataset_id'],
            observed_at=rows[-1].get('period_end') or rows[-1]['observed_at'],retrieved_at=utcnow(),
            input_record_ids=[r['record_id'] for r in rows],values={'observation_count':str(len(rows))},
            units={'observation_count':'count'},collection_sha256=sha,
            scope='Exact saved source-preserving query page. Underlying raw records remain individually verifiable; not a prediction or complete-coverage assertion.')
        atomic(path,row)
    return {'collection_record_id':rid,'data_file':'/data/collections/'+path.name,
            'collection_sha256':sha,'page_observations':len(rows),'query_result_fully_materialized':query['query_result_fully_materialized']}


def validate_collection(row, library, cutoff, mode):
    if not library or row.get('cutoff')!=cutoff or row.get('mode')!=mode:
        raise ValueError('Library collection belongs to another task scope')
    if collection_digest(row)!=row.get('collection_sha256') or row['record_id']!='series-'+row['collection_sha256'][:24]:
        raise ValueError('Library collection content hash mismatch')
    if row['input_record_ids'] != [r['record_id'] for r in row['records']]:
        raise ValueError('Library collection input lineage mismatch')
    if row.get('values')!={'observation_count':str(len(row['records']))} or row.get('units')!={'observation_count':'count'}:
        raise ValueError('Collection count metadata differs from saved rows')
    if not row['records'] or len(set(row['input_record_ids']))!=len(row['records']):
        raise ValueError('Collection has empty or duplicate observations')
    if row.get('dataset_id')!=row['query']['dataset']['dataset_id']:
        raise ValueError('Collection dataset identity mismatch')
    times=[timestamp(r['observed_at']) for r in row['records']]
    if times!=sorted(times):raise ValueError('Collection observations must remain chronological')
    from .collection_sources import validate_collection_sources
    validate_collection_sources(library,row['records'],dataset_id=row['dataset_id'],
        start=row['query']['start'],end=row['query']['end'],cutoff=cutoff,mode=mode)
    return row
