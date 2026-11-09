from datetime import datetime,timedelta,timezone
import json
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
import sporton.briefing as b
from sporton.storage import digest
from sporton.contracts import utcnow
from conftest import make_games


def dt(v):return datetime.fromisoformat(v)


@pytest.mark.parametrize('now,latest,upcoming',[
 ('2026-11-04T06:29:59+09:00','2026-11-03T17:00:00+09:00','2026-11-04T06:30:00+09:00'),
 ('2026-11-04T06:30:00+09:00','2026-11-04T06:30:00+09:00','2026-11-04T17:00:00+09:00'),
 ('2026-11-04T16:59:59+09:00','2026-11-04T06:30:00+09:00','2026-11-04T17:00:00+09:00'),
 ('2026-11-04T17:00:00+09:00','2026-11-04T17:00:00+09:00','2026-11-05T06:30:00+09:00'),
 ('2026-12-31T23:00:00+09:00','2026-12-31T17:00:00+09:00','2027-01-01T06:30:00+09:00'),
 ('2026-11-03T21:30:00+00:00','2026-11-04T06:30:00+09:00','2026-11-04T17:00:00+09:00'),
])
def test_kst_schedule_boundaries(now,latest,upcoming):
    assert b.latest_slot(dt(now))==dt(latest)
    assert b.next_slot(dt(now))==dt(upcoming)


def test_timezone_required():
    with pytest.raises(ValueError,match='timezone'):b.next_slot(datetime(2026,11,4))


def test_immutable_slot_duplicate_and_export_recovery(store,tmp_path,monkeypatch):
    now=dt('2026-11-04T06:31:00+09:00');monkeypatch.setattr(b,'utcnow',lambda:now)
    first=b.create_briefing(store,b.latest_slot(now),refresh=False)
    assert first['id']=='2026-11-04-0630' and first['late'] is False
    assert first['snapshot']['data_management']['total_verified_games']==0
    assert first['snapshot']['models'][0]['live_accuracy'] is None
    assert '실제 데이터' in first['markdown']
    path=tmp_path/'briefings'/(first['id']+'.md');path.unlink()
    assert b.create_briefing(store,b.latest_slot(now),refresh=False)==first
    assert path.read_text()==first['markdown'] and len(store.rows('briefings'))==1
    with pytest.raises(DatabaseError),store.engine.begin() as c:c.execute(text("UPDATE briefings SET payload='{}'"))


def test_delayed_restart_is_not_backdated(store,monkeypatch):
    now=dt('2026-11-04T12:00:00+09:00');monkeypatch.setattr(b,'utcnow',lambda:now)
    monkeypatch.setattr('sporton.maintenance.tick',lambda *a,**k:{'failures':[],'imported':[]})
    r=b.run_due(store)
    assert r['late'] is True
    assert dt(r['generated_at'])==now
    assert dt(r['snapshot']['as_of_timestamp'])==now
    assert dt(r['scheduled_for']).astimezone(b.KST).hour==6
    assert '소급' in r['markdown']
    assert b.run_due(store) is None


def test_previous_upcoming_becomes_reviewed_past(store,monkeypatch):
    now=dt('2026-11-04T06:30:00+09:00');clock=[now];monkeypatch.setattr(b,'utcnow',lambda:clock[0]);monkeypatch.setattr('sporton.storage.utcnow',lambda:clock[0])
    game=make_games(1)[0].model_dump(mode='json')
    start=now+timedelta(hours=1);end=start+timedelta(hours=2)
    game.update(game_id='TEST-follow-up',starts_at=start.isoformat(),ends_at=end.isoformat(),published_at=end.isoformat(),collected_at=end.isoformat())
    fixture={k:game[k] for k in ('game_id','league','season','starts_at','home_team','away_team')}
    store.append('fixtures',fixture['game_id'],fixture)
    b.add_note(store,'TEST/FIXTURE: 이전에 논의한 선발 정보 미확인을 계속 추적')
    first=b.create_briefing(store,b.latest_slot(now),test_mode=True,refresh=False)
    assert first['snapshot']['upcoming'][0]['analysis_status']=='unavailable'
    store.append('actual_results',game['game_id'],game);store.append('historical_game_results',game['game_id'],game)
    clock[0]=now+timedelta(hours=10,minutes=30)
    second=b.create_briefing(store,b.latest_slot(clock[0]),test_mode=True,refresh=False)
    changes=second['snapshot']['changes_since_previous']
    assert changes['previous_briefing_id']==first['id']
    assert changes['previously_announced_now_finished']==[game['game_id']]
    assert second['snapshot']['past'][0]['no_prior_prediction'] is True
    assert '사후에 적중 기록' in second['markdown']
    assert any('text' in r['payload'] for r in second['snapshot']['tracked_topics'])
    assert digest(store.get('briefings',first['id']))==digest(first)


def test_pending_fixture_transparent_and_fixture_data_excluded(store,monkeypatch):
    now=dt('2026-11-04T17:00:00+09:00');monkeypatch.setattr(b,'utcnow',lambda:now)
    fixture={'game_id':'unresolved','league':'MLB','season':'TEST/FIXTURE','home_team':'FIXTURE-A','away_team':'FIXTURE-B','starts_at':(now-timedelta(hours=2)).isoformat()}
    store.append('fixtures',fixture['game_id'],fixture)
    r=b.create_briefing(store,refresh=False)
    assert r['snapshot']['past'][0]['status']=='started_or_awaiting_verified_result'
    assert r['snapshot']['past'][0]['actual_result'] is None
    assert r['snapshot']['past'][0]['evaluation'] is None


def test_pipeline_failure_appears_in_report(store,monkeypatch):
    def fail(*a,**kw):raise ValueError('TEST/FIXTURE supplier failed')
    monkeypatch.setattr('sporton.maintenance.tick',fail)
    r=b.create_briefing(store)
    assert r['snapshot']['data_management']['issues'][0]['kind']=='maintenance_failed'
    assert 'TEST/FIXTURE supplier failed' in r['markdown']


def test_hourly_maintenance_and_scheduler_lock(store,monkeypatch):
    now=dt('2026-11-04T06:30:00+09:00');monkeypatch.setattr(b,'utcnow',lambda:now)
    calls=[]
    def tick(*a,**kw):calls.append('tick');return {'failures':[],'imported':[]}
    monkeypatch.setattr('sporton.maintenance.tick',tick)
    b.daemon(store,once=True,retrain=True)
    count=len(calls)
    b.daemon(store,once=True,retrain=True)
    assert len(calls)==count
    assert len(store.rows('briefings'))==1
    assert store.get('briefing_events','backup:2026-11-04-0630')['action']=='backup_completed'
    assert b.scheduler_status()['running_observed'] is False
    with b.scheduler_lock():
        assert b.scheduler_status()['process_lock_held'] is True
        with pytest.raises(ValueError,match='already running'):
            with b.scheduler_lock():pass


def test_missed_slots_logged_without_fake_reports(store,monkeypatch):
    now=dt('2026-11-04T06:30:00+09:00');clock=[now];monkeypatch.setattr(b,'utcnow',lambda:clock[0]);monkeypatch.setattr('sporton.storage.utcnow',lambda:clock[0])
    monkeypatch.setattr('sporton.maintenance.tick',lambda *a,**kw:{'failures':[],'imported':[]})
    b.run_due(store)
    clock[0]=now+timedelta(days=2)
    r=b.run_due(store)
    assert len(store.rows('briefings'))==2
    event=store.get('briefing_events','missed:'+r['id'])
    assert len(event['slots'])==3


def test_briefing_api_and_fixture_isolation(store):
    from fastapi.testclient import TestClient
    from sporton.api import app
    actual=b.create_briefing(store,refresh=False)
    fixture=b.create_briefing(store,test_mode=True,refresh=False)
    with TestClient(app) as c:
        rows=c.get('/api/briefings').json()
        assert [r['id'] for r in rows]==[actual['id']]
        assert c.get('/api/briefings/'+fixture['id']).status_code==404
        assert c.get('/api/briefings/'+actual['id']+'/markdown').text==actual['markdown']
        assert c.get('/api/briefing-status').json()['policy']['times']==['06:30','17:00']
        assert '데일리 브리핑' in c.get('/').text


def test_new_information_flags_reassessment_without_rewriting(store,monkeypatch):
    now=dt('2026-11-04T06:30:00+09:00');monkeypatch.setattr(b,'utcnow',lambda:now)
    fixture={'game_id':'TEST-new-info','league':'MLB','season':'TEST/FIXTURE','home_team':'FIXTURE-A','away_team':'FIXTURE-B','starts_at':(now+timedelta(hours=2)).isoformat()}
    store.append('fixtures',fixture['game_id'],fixture)
    report={'id':'TEST-original','created_at':(now-timedelta(hours=1)).isoformat(),'fixture':fixture,'model_id':'TEST-model',
        'snapshot':{'data_mode':'TEST/FIXTURE'},'scores':[4.,3.],'home_win_probability':.55,'scope':'including_extra_innings','analyst':'TEST/FIXTURE','limitations':[]}
    store.append('model_predictions',report['id'],report)
    store.append('official_recommendations',fixture['game_id'],{'fixture_id':fixture['game_id'],'prediction_id':report['id'],'at':report['created_at'],'review':'PASS','state':'WATCH','data_mode':'TEST/FIXTURE'})
    store.append('injuries','TEST-new-injury',{'game_id':fixture['game_id'],'quality':'TEST/FIXTURE','collected_at':(now-timedelta(minutes=30)).isoformat(),'status':'out'})
    before=digest(report);record=b.create_briefing(store,test_mode=True,refresh=False)
    item=record['snapshot']['upcoming'][0]
    assert item['requires_reassessment'] is True
    assert len(item['new_information_since_prediction'])==1
    assert '재심사 필요' in record['markdown']
    assert digest(store.get('model_predictions',report['id']))==before


def test_briefing_refresh_includes_newly_reviewed_prediction(store,history,monkeypatch):
    from sporton.models import train
    model=train(store,True);now=utcnow()
    from sporton.sports.contracts import parse_fixture
    fixture=parse_fixture({'game_id':'TEST-auto-analysis','league':'NBA','season':'TEST/FIXTURE','home_team':'FIXTURE-A','away_team':'FIXTURE-B','starts_at':(now+timedelta(hours=2)).isoformat()}).model_dump(mode='json')
    store.append('fixtures',fixture['game_id'],fixture)
    monkeypatch.setattr(b,'champion',lambda s,league:model['id'] if league=='NBA' else None)
    record=b.create_briefing(store,test_mode=True)
    item=record['snapshot']['upcoming'][0]
    assert item['analysis_status']=='published', record['snapshot']['data_management']['issues']
    assert item['decision']['state']=='WATCH'
    assert item['reviews'][0]['verdict']=='PASS'
    assert dt(record['snapshot']['as_of_timestamp'])>=dt(item['prediction']['created_at'])
