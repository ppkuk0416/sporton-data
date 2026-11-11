from datetime import datetime,timedelta,timezone,date
import json
import pytest
from sporton import dispatch
from sporton.contracts import utcnow


def event(now):
    return {'id':'TEST-GAME','date':(now+timedelta(hours=3)).isoformat(),'name':'TEST away at home',
        'status':{'type':{'state':'pre','completed':False}},'competitions':[{'competitors':[
            {'id':'A','homeAway':'away','team':{'displayName':'TEST AWAY'}},
            {'id':'H','homeAway':'home','team':{'displayName':'TEST HOME'}}]}]}


def games(team,now):
    return {'team':{'id':team},'events':[{'id':str(i),'gameDate':(now-timedelta(days=i)).isoformat(),
        'homeTeamId':team,'awayTeamId':'OTHER','homeTeamScore':'110','awayTeamScore':'100','gameResult':'W'} for i in range(1,6)]}


def test_baseline_no_future_leakage_and_minimum_sample():
    now=utcnow();t=games('A',now);t['events'].append({**t['events'][0],'gameDate':(now+timedelta(days=2)).isoformat(),'homeTeamScore':'999'})
    result=dispatch.baseline(t,now+timedelta(hours=3))
    assert result['for']==110 and result['n']==5
    assert dispatch.baseline({**t,'events':t['events'][:2]},now) is None


def test_started_game_has_no_pre_game_score():
    now=utcnow();e=event(now-timedelta(days=1));s={'lastFiveGames':[games('A',now),games('H',now)]}
    assert dispatch.describe(e,s,now)['baseline_scores_away_home'] is None


def test_run_archives_before_send_and_retry_reuses_original(store,monkeypatch):
    now=utcnow();row=dispatch.describe(event(now),{'lastFiveGames':[games('A',now),games('H',now)]},now);row['schedule_crosschecked']=True
    monkeypatch.setattr(dispatch,'settings',lambda:('TEST','123'))
    calls=[]
    monkeypatch.setattr(dispatch,'collect',lambda *a:([row],[]))
    def fail(s,r):
        assert s.get('briefings',r['id'])['markdown']==r['markdown']
        calls.append(r['markdown']);return {'status':'failed'}
    monkeypatch.setattr(dispatch,'deliver',fail)
    with pytest.raises(ValueError,match='incomplete'):dispatch.run(store)
    monkeypatch.setattr(dispatch,'collect',lambda *a:pytest.fail('must not regenerate original'))
    def send(s,r):
        calls.append(r['markdown']);s.append('briefing_events','telegram:sent:'+r['id'],{'message_id':1});return {'status':'sent'}
    monkeypatch.setattr(dispatch,'deliver',send)
    assert dispatch.run(store)['status']=='sent'
    assert dispatch.run(store)['status']=='already_sent'
    assert calls[0]==calls[1]
    assert len(store.rows('professional_reports'))==1


def test_preview_never_publishes_or_sends(store,monkeypatch):
    monkeypatch.setattr(dispatch,'collect',lambda *a:([],['TEST missing data']))
    monkeypatch.setattr(dispatch,'deliver',lambda *a:pytest.fail('preview must not send'))
    assert dispatch.run(store,dry_run=True)['status']=='preview'
    assert store.rows('briefings')==[] and store.rows('professional_reports')==[]


def test_schedule_kst_midnight_and_cutoff():
    # UTC 21:00 = next day KST 06:00.
    now=datetime(2026,11,11,21,tzinfo=timezone.utc)
    assert dispatch.next_run(now).isoformat()=='2026-11-12T06:15:00+09:00'
    assert dispatch.next_run(now+timedelta(minutes=16)).date()==date(2026,11,13)


def test_direct_telegram_env(monkeypatch):
    from sporton.telegram import settings,TelegramError
    monkeypatch.setenv('SPORTON_TELEGRAM_BOT_TOKEN','TEST_TOKEN')
    monkeypatch.setenv('SPORTON_TELEGRAM_CHAT_ID','123')
    assert settings()==('TEST_TOKEN','123')
    monkeypatch.setenv('SPORTON_TELEGRAM_CHAT_ID','-123')
    with pytest.raises(TelegramError):settings()


def test_collection_deduplicates_dates_and_preserves_detail_failure(store,monkeypatch):
    now=utcnow();e=event(now);target=dispatch.parse_time(e['date']).astimezone(dispatch.KST).date()
    def fake_fetch(s,url):
        if 'scoreboard?' in url:return {'events':[e,e]}
        if 'scoreboardv3' in url:return {'scoreboard':{'games':[]}}
        raise ValueError('TEST/FIXTURE unavailable')
    monkeypatch.setattr(dispatch,'fetch',fake_fetch)
    rows,issues=dispatch.collect(store,target,now)
    assert len(rows)==1 and rows[0]['baseline_scores_away_home'] is None
    assert any('상세 수집 실패' in issue for issue in issues)
    assert rows[0]['schedule_crosschecked'] is False
