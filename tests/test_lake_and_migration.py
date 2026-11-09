from pathlib import Path
import hashlib,json
import pytest
from sqlalchemy import text
from sporton.storage import Store
from sporton.lake import export_archive,summary,export_events
from sporton.migration import migrate


def test_actual_parquet_and_duckdb_grade_and_tamper(store,tmp_path):
    body=json.dumps({'name':'TEST/FIXTURE','matches':[{'date':'2025-01-01','team1':'FIXTURE-A','team2':'FIXTURE-B','score':{'ft':[1,0]}},{'date':'2025-01-02','team1':'FIXTURE-B','team2':'FIXTURE-A','score':[0,0]}]}).encode()
    raw=tmp_path/'raw.json';raw.write_bytes(body)
    dataset={'source_key':'TEST/FIXTURE','league':'EPL','season':'TEST','source_url':'https://fixture.example.test/data','raw':{'path':str(raw),'sha256':hashlib.sha256(body).hexdigest()}}
    store.append('archive_datasets','TEST-data',dataset)
    exported=export_archive(store,'TEST-data')
    assert exported['row_count']==2 and exported['production_eligible'] is False
    assert export_archive(store,'TEST-data')==exported
    rows=summary(store);assert rows[0]['games']==2 and rows[0]['data_grade']=='date_only_archive'
    assert not store.rows('historical_game_results')
    Path(exported['path']).write_bytes(b'TEST/FIXTURE corrupt')
    with pytest.raises(ValueError,match='integrity'):summary(store)


def test_exact_idempotent_migration_and_transaction_rollback(store,tmp_path):
    for key in ['KBL-TEST','z-TEST','1-TEST']:
        store.append('raw_data',key,{'text':'TEST/FIXTURE','id':key})
    target=Store('sqlite:///'+str(tmp_path/'target.sqlite'))
    result=migrate(store,target);assert result['verified'] and result['entities']['raw_data']['count']==3
    assert migrate(store,target)['verified']
    assert target.rows('raw_data')==store.rows('raw_data')
    with pytest.raises(ValueError),target.transaction() as c:
        target.append('raw_data','ROLLBACK',{'test':True},conn=c)
        target.append('raw_data','KBL-TEST',{'conflict':True},conn=c)
    assert target.get('raw_data','ROLLBACK') is None


def test_event_snapshot_retains_hashes_and_ids(store):
    store.append('raw_data','TEST-event',{'name':'TEST/FIXTURE'})
    item=export_events(store,'raw_data')
    assert item['row_count']==1
    assert export_events(store,'raw_data')==item
