"""A new checkout starts without a private historical archive or fabricated data."""
import json,sqlite3
from contextlib import closing
from pathlib import Path
from research_data.http import utcnow
from .live import atomic


def archive_directory(root,state_dir):
    pointer=Path(root)/'data/latest.json'
    if pointer.exists():
        data=json.loads(pointer.read_text());path=Path(data['run_dir'])
        if not path.is_absolute():path=Path(root)/path
        if not (path/'manifest.json').is_file():raise ValueError('Historical archive pointer is invalid; repair data/latest.json or remove it to use an empty archive')
        return path
    path=Path(state_dir)/'empty_archive';path.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(path/'research.sqlite')) as db:
        db.executescript('CREATE TABLE IF NOT EXISTS datasets(dataset_id TEXT PRIMARY KEY,metadata_json TEXT NOT NULL); CREATE TABLE IF NOT EXISTS records(record_id TEXT PRIMARY KEY,dataset_id TEXT NOT NULL,observed_at TEXT NOT NULL,effective_time TEXT NOT NULL,available_at TEXT,payload_json TEXT NOT NULL); CREATE TABLE IF NOT EXISTS sources(source_id TEXT PRIMARY KEY,metadata_json TEXT NOT NULL);')
    # This is an empty catalog, never a synthetic market snapshot.
    atomic(path/'manifest.json',{'schema_version':'1.0','cutoff':utcnow(),'datasets':[],'sources':[],'jobs':[],'limitations':['No historical archive has been prepared on this installation. Current public APIs work independently.'],'status':'empty'})
    return path
