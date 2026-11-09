"""Actual PostgreSQL tests; only dedicated scratch databases and TEST/FIXTURE data."""
import os,json,uuid
from pathlib import Path
from datetime import timedelta
import pytest
from sqlalchemy import create_engine,make_url,text
from sqlalchemy.exc import DBAPIError
from sporton.storage import Store,digest
from sporton.contracts import utcnow
from sporton.governance import register_report
from sporton.durability import Policy,create_backup,restore_test,status


@pytest.fixture
def pg_store(tmp_path,monkeypatch):
    selector=os.getenv('SPORTON_TEST_POSTGRES_ADMIN_URL_FILE')
    if not selector:pytest.skip('PostgreSQL integration credential selector not configured')
    url=make_url(Path(selector).read_text().strip());name='sporton_test_'+uuid.uuid4().hex
    admin=create_engine(url.set(database='postgres'),isolation_level='AUTOCOMMIT')
    with admin.connect() as c:c.execute(text('CREATE DATABASE "'+name+'"'))
    target=Store(url.set(database=name),initialize=True)
    monkeypatch.setenv('SPORTON_HOME',str(tmp_path));monkeypatch.setenv('SPORTON_DATABASE_URL',target.engine.url.render_as_string(hide_password=False))
    with target.engine.begin() as c:
        c.execute(text('GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA public TO sporton_app'))
        c.execute(text('GRANT USAGE ON SCHEMA public TO sporton_app'))
    yield target
    target.engine.dispose()
    with admin.connect() as c:c.execute(text('DROP DATABASE "'+name+'" WITH (FORCE)'))
    admin.dispose()


def seed_report(s):
    now=utcnow();fixture={'game_id':'TEST-game','league':'NBA','season':'TEST','starts_at':(now+timedelta(hours=2)).isoformat(),'home_team':'FIXTURE-A','away_team':'FIXTURE-B'}
    snapshot={'feature_cutoff_timestamp':(now-timedelta(minutes=1)).isoformat(),'data_mode':'TEST/FIXTURE','lineage_hash':'TEST/FIXTURE'}
    prediction={'id':'TEST-pred','fixture':fixture,'model_id':'TEST-model','created_at':now.isoformat(),'snapshot':snapshot,'scores':[100.,98.],'margin':2.,'total':198.,'home_win_probability':.55}
    evidence={'fixture_id':fixture['game_id'],'created_at':(now-timedelta(seconds=1)).isoformat()}
    model={'id':'TEST-model','league':'NBA','data_mode':'TEST/FIXTURE','trained_at':(now-timedelta(days=1)).isoformat(),'code_hash':'TEST','training_hash':'TEST','feature_version':'TEST'}
    review={'prediction_id':prediction['id'],'verdict':'PASS','report_hash':digest(prediction),'evidence_id':'TEST-evidence','at':now.isoformat()}
    for table,key,value in [('fixtures',fixture['game_id'],fixture),('model_versions','TEST-model',model),('feature_snapshots','TEST-pred',snapshot),('review_evidence','TEST-evidence',evidence),('model_predictions','TEST-pred',prediction),('critical_reviews','TEST-review',review)]:s.append(table,key,value)
    return fixture,prediction


def test_pg_direct_update_delete_truncate_and_role_restrictions(pg_store):
    s=pg_store;s.append('raw_data','TEST',{'value':'TEST/FIXTURE'})
    for command in ("UPDATE raw_data SET payload='{}'","DELETE FROM raw_data","TRUNCATE raw_data"):
        with pytest.raises(DBAPIError),s.engine.begin() as c:c.execute(text(command))
    root=Path(os.environ['SPORTON_TEST_POSTGRES_ADMIN_URL_FILE']).parent
    app=Store(s.engine.url.set(username='sporton_app',password=(root/'app.password').read_text().strip()))
    app.append('raw_data','TEST-app',{'value':'TEST/FIXTURE'})
    assert app.get('raw_data','TEST-app')
    with pytest.raises(DBAPIError),app.engine.begin() as c:c.execute(text('CREATE TABLE illegal(id int)'))
    with pytest.raises(DBAPIError),app.engine.begin() as c:c.execute(text('UPDATE raw_data SET payload=payload'))
    app.engine.dispose()


def test_pg_report_gate_database_projection_and_forgery(pg_store):
    s=pg_store;f,p=seed_report(s)
    key=register_report(s,'PRE_GAME',f['game_id'],p['id'],'TEST-review',data_mode='TEST/FIXTURE')
    with s.engine.connect() as c:assert c.execute(text('SELECT count(*) FROM report_index')).scalar_one()==1
    payload=s.get('professional_reports',key);payload={**payload,'id':'TEST-forged','original_hash':'wrong'}
    with pytest.raises(DBAPIError),s.transaction() as c:s.append('professional_reports','TEST-forged',payload,conn=c)
    assert s.get('professional_reports','TEST-forged') is None
    assert s.get('professional_reports',key)['original_hash']==digest(p)


def test_pg_pre_game_after_actual_start_cannot_be_backdated(pg_store):
    s=pg_store;f,p=seed_report(s)
    # Corrected official start is earlier than now; original pre-game records stay untouched.
    from sporton.governance import append_revision
    corrected={**f,'starts_at':(utcnow()-timedelta(hours=1)).isoformat()}
    append_revision(s,'fixtures',f['game_id'],corrected,'TEST/FIXTURE corrected schedule','test',['fixture:source'])
    with pytest.raises(ValueError,match='after start'):register_report(s,'PRE_GAME',f['game_id'],p['id'],'TEST-review',data_mode='TEST/FIXTURE')
    forged={'id':'TEST-backdate','kind':'PRE_GAME','fixture_id':f['game_id'],'prediction_id':p['id'],'review_id':'TEST-review','model_id':'TEST-model',
        'created_at':(utcnow()-timedelta(hours=2)).isoformat(),'data_mode':'TEST/FIXTURE','original_hash':digest(p),'content':{'original_prediction':p}}
    with pytest.raises(DBAPIError),s.transaction() as c:s.append('professional_reports','TEST-backdate',forged,conn=c)


def test_pg_encrypted_actual_restore_verifies_rows_schema_and_assets(pg_store,tmp_path):
    s=pg_store;s.append('raw_data','TEST-backup',{'text':'TEST/FIXTURE restore only'})
    register_report(s,'RESEARCH',content={'test':'TEST/FIXTURE'},data_mode='TEST/FIXTURE')
    key=tmp_path/'key';key.write_bytes(os.urandom(32));key.chmod(0o600)
    policy=Policy(backup_root=str(tmp_path/'backups'),key_file=str(key),pg_tools_prefix=['docker','--host=unix:///var/run/docker.sock','exec','--user','postgres','sporton-pgdev'])
    backup=create_backup(s,policy);assert not status(s,policy)['latest_backup_restore_verified']
    result=restore_test(s,backup['backup_id'],policy)
    assert result['status']=='RESTORE_VERIFIED' and result['schema_objects_verified']>100
    assert status(s,policy)['latest_backup_restore_verified']
    assert s.get('raw_data','TEST-backup')=={'text':'TEST/FIXTURE restore only'}
