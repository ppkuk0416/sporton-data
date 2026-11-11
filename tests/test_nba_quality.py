import copy,json
from pathlib import Path
from datetime import datetime,timedelta,timezone,date
import httpx
import pytest
from sporton import dispatch
from sporton.nba_quality import code,match_schedule,history,independent_review,grade,efficiency
from sporton.contracts import utcnow
from test_dispatch import event,games


def fixture_events():return json.loads((Path(__file__).parent/'fixtures/nba_20261110.json').read_text())['events']


def test_A_real_recorded_nov11_regular_schedule_no_duplicates():
    es=fixture_events();target=date(2026,11,11)
    assert len({e['id'] for e in es if dispatch.parse_time(e['date']).astimezone(dispatch.KST).date()==target})==6
    assert {e['name'] for e in es}=={'Milwaukee Bucks at New York Knicks','Boston Celtics at Philadelphia 76ers','LA Clippers at Phoenix Suns',
        'Miami Heat at Utah Jazz','Orlando Magic at Sacramento Kings','Oklahoma City Thunder at Portland Trail Blazers'}


def test_B_pregame_vs_late_manual_and_no_false_schedule_failure(store,monkeypatch):
    es=fixture_events();morning=datetime(2026,11,11,6,30,tzinfo=dispatch.KST);night=morning.replace(hour=23,minute=22)
    # Pure time classification of recorded official game starts, not backfilled forecasts.
    assert sum(dispatch.parse_time(e['date'])>morning for e in es)==6
    assert sum(dispatch.parse_time(e['date'])>night for e in es)==0
    monkeypatch.setattr(dispatch,'collect',lambda *a:([],[]))
    dispatch.run(store,now=night,dry_run=True,origin='workflow_dispatch')
    assert not any(r['payload']['state']=='MISSED_SCHEDULE' for r in store.rows('job_executions'))


def schedule_event(i,when,team='A',final=True):
    return {'id':str(i),'date':when.isoformat(),'seasonType':{'type':2},'competitions':[{'status':{'type':{'completed':final}},'competitors':[
        {'id':team,'homeAway':'home','score':{'value':110},'team':{'abbreviation':'NY','displayName':'New York Knicks'}},
        {'id':'B','homeAway':'away','score':{'value':100},'team':{'abbreviation':'MIL','displayName':'Milwaukee Bucks'}}]}]}


def test_C_last5_last10_and_season_from_schedule_no_lastFiveGames():
    now=utcnow();body={'events':[schedule_event(i,now-timedelta(days=i+1)) for i in range(15)]+[schedule_event('future',now+timedelta(days=1))]}
    f=history(body,'A',now,now)
    assert f['n']==5 and f['last10']['n']==10 and f['season']['n']==15
    assert f['for']==110 and 'future' not in f['source_event_ids']


def test_D_alias_matching_failure_is_not_conflict():
    e=schedule_event(1,utcnow());h,a=dispatch.__dict__.get('teams',lambda _:None)(e) if False else (None,None)
    official={'gameId':'NBA-1','gameTimeUTC':e['date'],'homeTeam':{'teamTricode':'NYK'},'awayTeam':{'teamTricode':'MIL'}}
    assert match_schedule(e,[official],True)[0]=='VERIFIED'
    assert match_schedule(e,[],False)[0]=='SOURCE_UNAVAILABLE'
    assert match_schedule(e,[{**official,'gameTimeUTC':(utcnow()+timedelta(hours=2)).isoformat()}],True)[0]=='SOURCE_CONFLICT'
    assert code('UTAH')=='UTA' and code('GS')=='GSW'


def test_D_retry_valid_cache_and_schema_drift(store,monkeypatch):
    url=dispatch.ESPN+'scoreboard?dates=20261110'
    store.append('raw_data','TEST-cached',{'source_url':url,'collected_at':utcnow().isoformat(),'body':{'events':[]}})
    class Client:
        def __init__(self,*a,**k):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def get(self,*a,**k):return httpx.Response(200,json={'changed':[]})
    monkeypatch.setattr(dispatch.httpx,'Client',Client);monkeypatch.setattr(dispatch.clock,'sleep',lambda *a:None)
    assert dispatch.fetch(store,url)=={'events':[]}
    assert store.rows('source_failures')[-1]['payload']['status']=='SOURCE_UNAVAILABLE'
    assert store.rows('data_quality_logs')[-1]['payload']['state']=='CACHED_FALLBACK'


def test_F_spread_total_push_and_post_start_exclusion():
    now=utcnow();p={'game_id':'TEST','created_at':now.isoformat(),'starts_at':(now+timedelta(hours=1)).isoformat(),'scores':[100,110],
        'market':{'home_line':-5,'total':205},'selection_class':'OBSERVATION'}
    r={'home_score':105,'away_score':100,'source_grade':'TEST/FIXTURE'}
    g=grade(p,r);assert g['spread']=='Push' and g['total']=='Push' and g['direction_correct'] is True and g['roi'] is None
    with pytest.raises(ValueError,match='post-start'):grade({**p,'created_at':p['starts_at']},r)


def test_H_numeric_error_rejected():
    now=utcnow();row=dispatch.describe(event(now),{},now);row.update(baseline_scores_away_home=[100.,110.],predicted_total=100.,schedule_status='VERIFIED')
    r=independent_review(row,{'margin':-2},now)
    assert r['verdict']=='NO' and 'predicted_total inconsistent' in r['reasons'] and r['llm_expert_review']=='NOT_CONFIGURED'


def test_failed_storage_does_not_claim_success(store,monkeypatch):
    now=utcnow();row=dispatch.describe(event(now),{'lastFiveGames':[games('A',now),games('H',now)]},now);row['schedule_crosschecked']=True
    monkeypatch.setattr(dispatch,'collect',lambda *a:([row],[]));monkeypatch.setattr(dispatch,'settings',lambda:('TEST','123'))
    append=store.append
    def fail(table,*a,**k):
        if table=='briefings':raise RuntimeError('TEST DB failure')
        return append(table,*a,**k)
    monkeypatch.setattr(store,'append',fail)
    with pytest.raises(RuntimeError):dispatch.run(store)
    assert store.rows('professional_reports')==[]
    assert store.rows('job_executions')[-1]['payload']['state']=='FAILED'
    assert not any(r['payload']['phase']=='report_stored' for r in store.rows('job_executions'))


def test_no_games_sends_no_new_analysis(store,monkeypatch):
    monkeypatch.setattr(dispatch,'collect',lambda *a:([],[]));monkeypatch.setattr(dispatch,'settings',lambda:('TEST','123'))
    monkeypatch.setattr(dispatch,'deliver',lambda *a:pytest.fail('no games must not notify'))
    assert dispatch.run(store)['status']=='no_games'


def test_official_recorded_all_six_crosscheck():
    b=json.loads((Path(__file__).parent/'fixtures/nba_official_20261110.json').read_text())
    assert len(b['scoreboard']['games'])==6
    assert all(match_schedule(e,b['scoreboard']['games'],True)[0]=='VERIFIED' for e in fixture_events())


def test_cup_regular_games_retained_championship_excluded():
    from sporton.nba_quality import regular
    e=schedule_event('cup',utcnow())
    e['competitions'][0]['notes']=[{'headline':'NBA Cup Semifinal'}]
    assert regular(e)
    e['competitions'][0]['notes']=[{'headline':'NBA In-Season Tournament Championship'}]
    assert not regular(e)


def test_G_identical_temporal_folds_and_no_target_leakage():
    from sporton.nba_benchmark import dataset,compare
    from conftest import make_games
    gs=make_games(300)
    results=[{'id':g.game_id,'date':g.starts_at.isoformat(),'home_id':g.home_team,'away_id':g.away_team,'home_score':g.home_score,'away_score':g.away_score} for g in gs]
    rows=dataset(results)
    changed=copy.deepcopy(results);changed[100]['home_score']+=30
    revised=dataset(changed)
    assert next(r for r in rows if r['game_id']=='TEST-100')['x']==next(r for r in revised if r['game_id']=='TEST-100')['x']
    folds=compare(rows,kinds=('elo','lightgbm'))
    assert len(folds)==2
    assert all(set(f['metrics'])=={'recent_average','elo','lightgbm'} and len(f['test_game_ids'])==f['n_test'] for f in folds)
    assert set(folds[0]['test_game_ids']).isdisjoint(folds[1]['test_game_ids'])


def test_manual_run_does_not_consume_scheduled_delivery_slot(store,monkeypatch):
    now=utcnow();row=dispatch.describe(event(now),{'lastFiveGames':[games('A',now),games('H',now)]},now);row['schedule_crosschecked']=True
    monkeypatch.setattr(dispatch,'collect',lambda *a:([copy.deepcopy(row)],[]));monkeypatch.setattr(dispatch,'settings',lambda:('TEST','123'))
    keys=[]
    def send(s,r):keys.append(r['id']);s.append('briefing_events','telegram:sent:'+r['id'],{'message_id':len(keys)});return {'status':'sent'}
    monkeypatch.setattr(dispatch,'deliver',send)
    dispatch.run(store,origin='workflow_dispatch');dispatch.run(store,origin='schedule')
    assert len(keys)==2 and '-manual-' in keys[0] and '-manual-' not in keys[1]


def test_H_conflict_rejection_reasons_persist(store):
    from sporton.nba_quality import enrich
    now=utcnow();e=event(now);row=dispatch.describe(e,{'lastFiveGames':[games('A',now),games('H',now)]},now)
    row['schedule_status']='SOURCE_CONFLICT'
    def missing(*a):raise ValueError('TEST/FIXTURE unavailable')
    row=enrich(store,e,row,{},missing,now)
    review=store.get('critical_reviews','review:'+row['prediction_id'])
    assert review['verdict']=='NO' and 'fixture identity/time conflict' in review['reasons']
    assert row['recommendation_status']=='NO_BET'


def test_I_restore_contains_immutable_report_and_prediction(store,tmp_path):
    from test_persistence import policy
    from sporton.governance import register_report
    from sporton.durability import create_backup,restore_test
    rid=register_report(store,'RESEARCH',content={'source':'TEST/FIXTURE','prediction':[101,99]},data_mode='TEST/FIXTURE')
    store.append('forecast_components','TEST-original',{'report_id':rid,'scores':[101,99],'data_mode':'TEST/FIXTURE'})
    p=policy(tmp_path);b=create_backup(store,p);result=restore_test(store,b['backup_id'],p)
    assert result['status']=='RESTORE_VERIFIED'
    assert store.get('forecast_components','TEST-original')['scores']==[101,99]
    assert store.get('professional_reports',rid)['content']['prediction']==[101,99]


def test_successful_schedule_retry_is_not_a_missed_schedule(store,monkeypatch):
    target=date(2026,11,11);late=datetime(2026,11,11,7,tzinfo=dispatch.KST)
    store.append('briefing_events','telegram:sent:nba-daily-v2-'+str(target),{'message_id':1})
    monkeypatch.setattr(dispatch,'utcnow',lambda:late)
    assert dispatch.run(store,target,now=late,origin='schedule')['status']=='already_sent'
    assert not any(r['payload']['state']=='MISSED_SCHEDULE' for r in store.rows('job_executions'))
