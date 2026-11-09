"""Single-writer inbox worker and SQLite-consistent backup; no silent provider fallback."""
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4
from .contracts import utcnow
from .data import ingest
from .engine import daily
from .storage import home


def tick(store,inbox,test_mode=False,retrain=False,league='NBA'):
    inbox=Path(inbox);inbox.mkdir(parents=True,exist_ok=True)
    processed={r['payload'].get('inbox_hash') for r in store.rows('raw_data')}
    imported=[];failures=[]
    for file in sorted(inbox.glob('*.csv')):
        sha=hashlib.sha256(file.read_bytes()).hexdigest()
        if sha in processed:continue
        try:
            count=ingest(store,file,test_mode)
            store.append('raw_data','inbox:'+sha,{'inbox_hash':sha,'filename':file.name,'at':utcnow().isoformat()},idempotent=True)
            imported.append({'file':file.name,'n':count})
        except Exception as exc:
            failures.append({'file':file.name,'error':str(exc)})
    return {'imported':imported,'failures':failures,'maintenance':daily(store,test_mode,retrain,league)}


def backup(store,destination):
    if store.engine.dialect.name!='sqlite':
        raise ValueError('use pg_dump for PostgreSQL backups')
    dest=Path(destination)
    if dest.exists():raise ValueError('backup destination already exists')
    dest.mkdir(parents=True)
    with sqlite3.connect(store.engine.url.database) as source, sqlite3.connect(dest/'sporton.db') as target:
        source.backup(target)
    for name in ('models','raw','research'):
        if (home()/name).exists():shutil.copytree(home()/name,dest/name)
    files={str(p.relative_to(dest)):hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.rglob('*') if p.is_file()}
    manifest={'at':utcnow().isoformat(),'files':files,'restore_database_path':str(Path(store.engine.url.database).resolve()),
              'restore_model_directory':str((home()/'models').resolve()),
              'restore_data_directories':{name:str((home()/name).resolve()) for name in ('raw','research')},
              'instructions':'Stop this instance; verify hashes; restore DB and model files at original absolute paths; then restart.'}
    (dest/'manifest.json').write_text(json.dumps(manifest,indent=2))
    return manifest
