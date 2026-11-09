"""Development upgrade: preserve SQLite, migrate exactly, export actual archives."""
from pathlib import Path
import json
from sqlalchemy import URL
from sporton.storage import Store
from sporton.migration import migrate
from sporton.lake import export_archive,summary
from sporton.governance import register_report
root=Path('var/persistence-dev');owner=root.joinpath('owner.password').read_text().strip()
url=URL.create('postgresql+psycopg',username='sporton_owner',password=owner,host='127.0.0.1',port=55432,database='sporton')
source=Store('sqlite:///var/sporton.db');target=Store(url)
result=migrate(source,target)
Path('var/migrations').mkdir(exist_ok=True)
Path('var/migrations/sqlite-to-postgres.json').write_text(json.dumps(result,indent=2))
exports=[]
for row in target.rows('archive_datasets'):
    exports.append(export_archive(target,row['id']))
report=register_report(target,'RESEARCH',content={'purpose':'persistence validation on actual public archives','source_dataset_ids':[r['id'] for r in target.rows('archive_datasets')],
    'exports':exports,'quality':'UNVERIFIED_PUBLICATION_TIMESTAMPS','claims_live_prediction_accuracy':False},data_mode='real')
print(json.dumps({'migration_verified':result['verified'],'migrated_events':sum(v['count'] for v in result['entities'].values()),
    'parquet_rows':sum(e['row_count'] for e in exports),'research_report_id':report,'duckdb_summary':summary(target)},ensure_ascii=False))
