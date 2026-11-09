from datetime import timedelta
from pathlib import Path
import os,json,hashlib
import pytest
from sqlalchemy import text
from sporton.contracts import utcnow
from sporton.storage import digest
from sporton.governance import append_revision,effective_get,record_observation,record_statistics,register_report,register_alias
from sporton.durability import Policy,create_backup,restore_test,status
from conftest import make_games


def policy(tmp_path):
    key=tmp_path/'keys'/'key';key.parent.mkdir();key.write_bytes(os.urandom(32));key.chmod(0o600)
    return Policy(backup_root=str(tmp_path/'encrypted'),key_file=str(key))


def test_explicit_correction_preserves_original_and_clock(store):
    g=make_games(1)[0].model_dump(mode='json');store.append('historical_game_results',g['game_id'],g)
    corrected={**g,'home_score':g['home_score']+4,'home_periods':g['home_periods'][:3]+[g['home_periods'][3]+4]}
    before=utcnow();key=append_revision(store,'historical_game_results',g['game_id'],corrected,'TEST/FIXTURE source correction','test-module',['fixture:evidence'])
    assert store.get('historical_game_results',g['game_id'])==g
    assert effective_get(store,'historical_game_results',g['game_id'],before)==g
    assert effective_get(store,'historical_game_results',g['game_id'])==corrected
    assert store.get('record_revisions',key)['actor']=='test-module'
    with pytest.raises(ValueError,match='frozen'):append_revision(store,'model_predictions','x',{},'r','a',['e'])


def observation():
    now=utcnow()
    return {'source':'TEST/FIXTURE-A','source_url':'https://fixture.example.test/result','source_record_id':'fixture:game','data_version':'1',
        'collected_at':now.isoformat(),'published_at':now.isoformat(),'effective_at':now.isoformat(),'league':'NBA','season':'TEST',
        'entity_id':'FIXTURE-team','field':'score','value':100,'test_fixture':True}


def test_missing_conflict_no_guessed_values(store):
    a=observation();key,state=record_observation(store,a);assert state['status']=='TEST/FIXTURE'
    same,_=record_observation(store,a);assert same==key
    _,conflict=record_observation(store,{**a,'source':'TEST/FIXTURE-B','source_url':'https://other.example.test/result','value':99})
    assert conflict['status']=='CONFLICTED'
    _,missing=record_observation(store,{**a,'field':'lineup','value':None});assert missing['status']=='MISSING'
    _,unverified=record_observation(store,{**a,'field':'injury','test_fixture':False,'published_at':None});assert unverified['status']=='UNVERIFIED'


def test_typed_sports_statistics_and_identity(store):
    a=observation();a.pop('field');a.pop('value')
    key,state=record_statistics(store,'basketball',a,{'ortg':115.,'drtg':110.,'net_rtg':5.,'efg_pct':.53})
    with store.engine.connect() as c:assert c.execute(text('select ortg from sport_basketball_statistics where id=:id'),{'id':key}).scalar_one()==115.
    with pytest.raises(ValueError,match='NetRtg'):record_statistics(store,'basketball',a,{'ortg':115,'drtg':110,'net_rtg':20})
    with pytest.raises(ValueError,match='schema mismatch'):record_statistics(store,'football',a,{'xg':1.})
    register_alias(store,'team','NBA','TEST','A','FIXTURE-A',['fixture:evidence'])
    with pytest.raises(ValueError,match='conflicting'):register_alias(store,'team','NBA','TEST','A','FIXTURE-B',['fixture:evidence'])


def test_typed_report_phase_and_missing_lineage(store):
    f={'game_id':'TEST-future','league':'NBA','season':'TEST','starts_at':(utcnow()+timedelta(hours=1)).isoformat(),'home_team':'A','away_team':'B'}
    store.append('fixtures',f['game_id'],f)
    with pytest.raises(ValueError,match='PASS'):register_report(store,'PRE_GAME',f['game_id'],'missing','missing',data_mode='TEST/FIXTURE')
    with pytest.raises(ValueError,match='LIVE'):register_report(store,'LIVE',f['game_id'])
    key=register_report(store,'RESEARCH',content={'quality':'TEST/FIXTURE','note':'No live predictions'},data_mode='TEST/FIXTURE')
    assert store.get('professional_reports',key)['kind']=='RESEARCH'


def test_encrypted_backup_requires_actual_restore(store,tmp_path):
    p=policy(tmp_path);store.append('raw_data','TEST',{'secret_fixture':'TEST/FIXTURE preserve this'})
    b=create_backup(store,p)
    assert b['restore_verified'] is False
    assert b'TEST/FIXTURE preserve this' not in Path(b['path']).read_bytes()
    assert status(store,p)['latest_backup_restore_verified'] is False
    r=restore_test(store,b['backup_id'],p)
    assert r['status']=='RESTORE_VERIFIED' and r['tables_verified']>40
    assert status(store,p)['latest_backup_restore_verified'] is True
    assert store.get('raw_data','TEST')['secret_fixture']=='TEST/FIXTURE preserve this'


def test_corruption_or_wrong_key_cannot_pass_restore(store,tmp_path):
    p=policy(tmp_path);b=create_backup(store,p);path=Path(b['path']);raw=bytearray(path.read_bytes());raw[-20]^=1;path.write_bytes(raw)
    with pytest.raises(ValueError,match='checksum'):restore_test(store,b['backup_id'],p)
    assert status(store,p)['latest_backup_restore_verified'] is False
    assert store.rows('restore_tests')[-1]['payload']['status']=='FAILED'


def test_key_permissions_and_production_separation(store,tmp_path):
    p=policy(tmp_path);Path(p.key_file).chmod(0o644)
    with pytest.raises(ValueError,match='permissions'):create_backup(store,p)


def test_restore_success_is_not_kept_verified_after_later_failure(store,tmp_path):
    p=policy(tmp_path);store.append('raw_data','TEST',{'v':1})
    b=create_backup(store,p);restore_test(store,b['backup_id'],p)
    assert status(store,p)['latest_backup_restore_verified']
    Path(b['path']).write_bytes(b'corrupt TEST/FIXTURE')
    with pytest.raises(ValueError):restore_test(store,b['backup_id'],p)
    assert not status(store,p)['latest_backup_restore_verified']


def test_admin_requires_token_and_report_trace(store,tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from sporton.api import app
    token=tmp_path/'admin.token';token.write_text('TEST/FIXTURE-token');token.chmod(0o600)
    monkeypatch.setenv('SPORTON_ADMIN_TOKEN_FILE',str(token))
    key=register_report(store,'RESEARCH',content={'test':'TEST/FIXTURE'},data_mode='TEST/FIXTURE')
    with TestClient(app) as c:
        assert c.get('/admin').status_code==200
        assert c.get('/api/admin/status').status_code==401
        headers={'Authorization':'Bearer TEST/FIXTURE-token'}
        assert c.get('/api/admin/status',headers=headers).json()['counts']['professional_reports']==1
        assert c.get('/api/admin/reports/'+key,headers=headers).json()['report']['kind']=='RESEARCH'


def test_game_correction_does_not_leak_into_old_feature_cutoff(store,history):
    from sporton.governance import games_as_of
    g=history[10].model_dump(mode='json');corrected={**g,'home_score':g['home_score']+1,'home_periods':g['home_periods'][:3]+[g['home_periods'][3]+1]}
    cutoff=history[12].starts_at
    append_revision(store,'historical_game_results',g['game_id'],corrected,'TEST/FIXTURE correction','test',['fixture:evidence'])
    old=next(x for x in games_as_of(store,'NBA',cutoff) if x.game_id==g['game_id'])
    current=next(x for x in games_as_of(store,'NBA') if x.game_id==g['game_id'])
    assert old.home_score==g['home_score'] and current.home_score==corrected['home_score']


def test_result_correction_appends_new_audit_without_rewriting_old_result(store):
    from sporton.analytics import audit_result_corrections,resolved_evaluations,performance
    g=make_games(1)[0].model_dump(mode='json')
    g.update(home_score=100,away_score=110,home_periods=[25]*4,away_periods=[27,27,28,28])
    f={k:g[k] for k in ('game_id','league','season','starts_at','home_team','away_team')}
    store.append('fixtures',g['game_id'],f);store.append('actual_results',g['game_id'],g)
    p={'id':'TEST-legacy','fixture':f,'model_id':'TEST-model','created_at':g['starts_at'],'scores':[110.,100.],'home_win_probability':.8}
    old={'prediction_id':p['id'],'model_id':'TEST-model','data_mode':'TEST/FIXTURE','period':g['starts_at'],'result_source':g['source'],'correct':0,'brier':.64,'log_loss':1.6,'score_mae':10.,'margin_error':20.,'total_error':0.,'market_settlement':None}
    store.append('model_predictions',p['id'],p);store.append('prediction_evaluations',p['id'],old)
    corrected={**g,'home_score':111,'home_periods':[27,28,28,28]}
    append_revision(store,'actual_results',g['game_id'],corrected,'TEST/FIXTURE official correction','test',['fixture:source'])
    revised=audit_result_corrections(store)
    assert len(revised)==1 and revised[0]['correct']==1
    assert store.get('prediction_evaluations',p['id'])==old
    assert store.get('actual_results',g['game_id'])==g
    assert resolved_evaluations(store)[0]['payload']['correct']==1
    assert audit_result_corrections(store)==[]
    assert performance(store)['excluded_unverified_originals']==[p['id']]


def test_failed_restore_has_backoff_and_is_not_verified(store,tmp_path):
    from sporton.durability import periodic
    p=policy(tmp_path);b=create_backup(store,p);Path(b['path']).write_bytes(b'TEST/FIXTURE broken')
    with pytest.raises(ValueError):restore_test(store,b['backup_id'],p)
    r=periodic(store,p)
    assert r['status']=='blocked_after_failed_restore'
    assert not status(store,p)['latest_backup_restore_verified']
