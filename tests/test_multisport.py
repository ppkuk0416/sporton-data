"""All generated records are TEST/FIXTURE, never real sport results."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import json
import numpy as np
import pytest
from pydantic import ValidationError
from sporton.contracts import utcnow,Market
from sporton.data import ingest,add_baseball_context,add_market
from sporton.engine import forecast,audit,daily
from sporton.models import champion,promote,rollback
from sporton.sports import plugin
from sporton.sports.contracts import FootballGame,BaseballGame,parse_fixture
from sporton.sports.common import rows_for
from sporton.sports.baseball import context_as_of
from sporton.sports.lifecycle import train_sport,backtest_sport
from sporton.storage import digest
from conftest import write_csv


def sport_games(league,n=220):
    rng=np.random.default_rng(134);start=datetime(2022,1,1,18,tzinfo=timezone.utc);games=[]
    for i in range(n):
        h,a=('FIXTURE-A','FIXTURE-B') if i%2==0 else ('FIXTURE-B','FIXTURE-A')
        t=start+timedelta(days=i)
        base=dict(game_id=f'TEST-{league}-{i}',league=league,season='TEST-SEASON',starts_at=t,ends_at=t+timedelta(hours=3),
            published_at=t+timedelta(hours=4),collected_at=t+timedelta(hours=5),source='TEST/FIXTURE generator',
            source_id=f'fixture:{league}:{i}',data_version='TEST-v1',quality='TEST/FIXTURE',home_team=h,away_team=a)
        if league=='EPL':
            x,y=map(int,rng.poisson([1.6,1.2]));base.update(home_score=x,away_score=y,
                home_periods=[x//2,x-x//2],away_periods=[y//2,y-y//2],home_xg=float(x+.2),away_xg=float(y+.2),home_ppda=12.,away_ppda=14.)
            games.append(FootballGame(**base))
        else:
            x,y=map(int,rng.poisson([4.3,3.8]))
            if league=='MLB' and x==y:x+=1
            base.update(home_score=x,away_score=y,home_periods=[x]+[0]*8,away_periods=[y]+[0]*8,rules_version='TEST/FIXTURE-only')
            games.append(BaseballGame(**base))
    return games


@pytest.fixture(params=['EPL','MLB','KBO'])
def sport_history(request,store,tmp_path):
    league=request.param;games=sport_games(league)
    ingest(store,write_csv(tmp_path/f'{league}.csv',games),True)
    return league,games


@pytest.fixture
def sport_model(store,sport_history):
    league,games=sport_history
    return train_sport(store,league,True)


def future(league):
    return dict(game_id='TEST-'+league+'-future',league=league,season='TEST-SEASON',starts_at=(utcnow()+timedelta(hours=2)).isoformat(),home_team='FIXTURE-A',away_team='FIXTURE-B')


def test_independent_features_and_training(store,sport_history,sport_model):
    league,games=sport_history;adapter=plugin(league);meta=sport_model
    assert meta['league']==league
    assert meta['sport'] in ('football','baseball')
    assert meta['feature_version']!='nba-rolling-v1'
    assert 'home_pace' not in meta['features']
    assert set(meta['candidate_validation'])==set(adapter.kinds)
    assert meta['test']['n']>0
    assert 'draw' in meta['outcomes'] if league!='MLB' else 'draw' not in meta['outcomes']
    with pytest.raises(ValueError,match='TEST/FIXTURE'):promote(store,meta['id'])
    assert champion(store,league) is None and champion(store) is None
    with pytest.raises(ValueError,match='already evaluated'):train_sport(store,league,True)


def test_sport_forecast_review_and_audit(store,sport_history,sport_model,monkeypatch):
    import sporton.engine as engine
    league,games=sport_history;f=future(league)
    response=forecast(store,f,sport_model['id'],test_mode=True)
    report=response['report'];p=report['outcome_probabilities']
    assert sum(p.values())==pytest.approx(1)
    assert report['analyst']==plugin(league).analyst.name
    assert response['review']['verdict']=='PASS'
    assert response['decision']['state']=='WATCH' and response['decision']['expected_value'] is None
    evidence=store.get('review_evidence',report['evidence_id'])
    assert evidence['created_at']<=report['created_at']
    assert evidence['sport']==plugin(league).sport
    original=digest(report)
    # Advance only the audit clock after storing a TEST/FIXTURE final.
    result=games[-1].model_dump();starts=datetime.fromisoformat(f['starts_at']);ends=starts+timedelta(hours=3)
    result.update(game_id=f['game_id'],starts_at=starts,ends_at=ends,published_at=ends,collected_at=ends,home_team=f['home_team'],away_team=f['away_team'])
    game=FootballGame(**result) if league=='EPL' else BaseballGame(**result)
    store.append('actual_results',f['game_id'],game.model_dump(mode='json'))
    monkeypatch.setattr(engine,'utcnow',lambda:ends+timedelta(minutes=1))
    scored=audit(store)
    assert len(scored)==1 and scored[0]['league']==league
    assert scored[0]['actual_outcome'] in plugin(league).outcomes
    assert audit(store)==[]
    assert digest(store.get('model_predictions',report['id']))==original


def test_cross_league_model_rejected(store,sport_history,sport_model):
    league,_=sport_history;other='MLB' if league=='EPL' else 'EPL'
    with pytest.raises(ValueError,match='league-specific'):forecast(store,future(other),sport_model['id'],test_mode=True)


def test_feature_time_and_league_isolation(store,sport_history,tmp_path):
    league,games=sport_history;adapter=plugin(league)
    target=games[80];fixture=parse_fixture({k:target.model_dump()[k] for k in ('game_id','league','season','starts_at','home_team','away_team')})
    cutoff=target.starts_at-timedelta(minutes=30)
    snap=adapter.snapshot(store,fixture,cutoff,True)
    assert target.game_id not in snap['source_game_ids']
    other='MLB' if league=='EPL' else 'EPL'
    ingest(store,write_csv(tmp_path/'other.csv',sport_games(other,100)),True)
    assert adapter.snapshot(store,fixture,cutoff,True)==snap
    assert all(league in r['game'].game_id for r in rows_for(store,adapter,True))


def test_multisport_dashboard_isolates_league(store,sport_history):
    from fastapi.testclient import TestClient
    from sporton.api import app
    league,games=sport_history
    with TestClient(app) as client:
        selected=client.get('/api/overview',params={'league':league}).json()
        assert selected['league']==league and selected['counts']['test_games']==len(games)
        assert client.get('/api/overview').json()['counts']['test_games']==0
        capabilities=client.get('/api/capabilities').json()
        assert len({x['analyst'] for x in capabilities})==3
        assert all(x['generative_llm_connected'] is False for x in capabilities)
        assert client.get('/api/overview?league=UNSUPPORTED').status_code==400


def test_football_scope_and_draw_contract():
    p=sport_games('EPL',1)[0].model_dump();p.update(home_score=1,away_score=1,home_periods=[0,1],away_periods=[1,0])
    assert FootballGame(**p).home_score==1
    p['scope']='including_overtime'
    with pytest.raises(ValidationError):FootballGame(**p)
    p['scope']='regulation';p['home_periods']=[0,1,1]
    with pytest.raises(ValidationError):FootballGame(**p)


def test_baseball_innings_and_draw_rules():
    p=sport_games('MLB',1)[0].model_dump();p.update(home_score=3,away_score=3,home_periods=[3]+[0]*8,away_periods=[3]+[0]*8)
    with pytest.raises(ValidationError,match='cannot tie'):BaseballGame(**p)
    p['league']='KBO';assert BaseballGame(**p).home_score==3
    p['league']='MLB';p.update(home_score=4,home_periods=[3]+[0]*8+[1],away_periods=[3]+[0]*9)
    assert len(BaseballGame(**p).home_periods)==10
    p['home_periods']=[4]+[0]*9
    with pytest.raises(ValidationError,match='previous tie'):BaseballGame(**p)
    p.update(home_score=4,away_score=3,home_periods=[4]+[0]*7+[None],away_periods=[3]+[0]*8)
    assert BaseballGame(**p).home_periods[-1] is None


def test_baseball_pregame_context_cutoff(store,tmp_path):
    games=sport_games('MLB',20);ingest(store,write_csv(tmp_path/'baseball.csv',games),True)
    f=parse_fixture(future('MLB'));store.append('fixtures',f.game_id,f.model_dump(mode='json'))
    now=utcnow();base=dict(game_id=f.game_id,league='MLB',team_id=f.home_team,starter_id='TEST-P',source='TEST/FIXTURE',source_id='test:starter',
        collected_at=now.isoformat(),published_at=now.isoformat(),data_version='1',quality='TEST/FIXTURE',starter_fip=3.4,starter_k_bb=.15,
        lineup_wrc_plus=105,bullpen_pitches_3d=125,lineup_confirmed=True)
    add_baseball_context(store,base,True)
    late=now+timedelta(hours=3)
    add_baseball_context(store,{**base,'starter_fip':9.9,'data_version':'2','published_at':late.isoformat(),'collected_at':late.isoformat()},True)
    assert context_as_of(store,f,now,True)[f.home_team].starter_fip==3.4
    snap=plugin('MLB').snapshot(store,f,now,True)
    assert snap['values']['home_starter_fip']==3.4 and snap['values']['away_starter_fip_known']==0
    assert len(snap['context_lineage'])==1
    report={'scope':'including_extra_innings','fixture':f.model_dump(),'outcome_probabilities':{'home':.5,'away':.5},'scores':[4.,3.], 'snapshot':snap}
    reasons,_=plugin('MLB').review_checks(report,{'pregame_context':{f.home_team:{**base,'starter_fip':9.9}}})
    assert any('inconsistent' in reason for reason in reasons)


def test_soccer_reviewer_rejects_winner_market_scope(store,tmp_path):
    games=sport_games('EPL');ingest(store,write_csv(tmp_path/'football.csv',games),True)
    model=train_sport(store,'EPL',True);f=parse_fixture(future('EPL'));store.append('fixtures',f.game_id,f.model_dump(mode='json'))
    now=utcnow()
    market=dict(game_id=f.game_id,source='TEST/FIXTURE',source_id='test:price',published_at=now.isoformat(),collected_at=now.isoformat(),data_version='TEST-v1',quality='TEST/FIXTURE',
        market_type='three_way',scope='including_overtime',selection='draw',line=None,decimal_odds=3.2,bookmaker='TEST/FIXTURE')
    key=add_market(store,market,True);r=forecast(store,f.model_dump(),model['id'],market_id=key,test_mode=True)
    assert r['review']['verdict']=='NO' and r['decision']['state']=='NO BET'
    assert not store.rows('official_recommendations')


def test_league_champions_and_rollback_are_independent(store,monkeypatch):
    for league in ('NBA','EPL','MLB'):
        for i in (1,2):
            key=f'TEST-{league}-{i}'
            store.append('model_versions',key,{'league':league,'id':key})
            store.append('model_promotions',key,{'league':league,'to_model':key,'from_model':None if i==1 else f'TEST-{league}-1'})
    assert champion(store)=='TEST-NBA-2'
    assert champion(store,'EPL')=='TEST-EPL-2'
    assert champion(store,'MLB')=='TEST-MLB-2'
    import sporton.models as models
    monkeypatch.setattr(models,'load_model',lambda s,key: ({},{'id':key}))
    assert rollback(store,'EPL')=='TEST-EPL-1'
    assert champion(store,'EPL')=='TEST-EPL-1'
    assert champion(store)=='TEST-NBA-2' and champion(store,'MLB')=='TEST-MLB-2'


def test_football_walkforward_executes(store,tmp_path):
    ingest(store,write_csv(tmp_path/'football.csv',sport_games('EPL')),True)
    result=backtest_sport(store,'EPL',True)
    assert len(result['folds'])==3 and all(f['models']['goal_poisson']['n']>0 for f in result['folds'])


def test_sport_challenger_cannot_promote_without_improvement(store,sport_history,sport_model,monkeypatch):
    import sporton.sports.lifecycle as lifecycle
    league,_=sport_history;rows=rows_for(store,plugin(league),True)
    base={**sport_model,'id':'TEST-champion','data_mode':'real','selection_end':'2020-01-01T00:00:00+00:00'}
    candidate={**sport_model,'id':'TEST-challenger','data_mode':'real'}
    store.append('model_versions',base['id'],base);store.append('model_versions',candidate['id'],candidate)
    store.append('model_promotions','TEST-bootstrap',{'league':league,'to_model':base['id'],'from_model':None})
    monkeypatch.setattr(lifecycle,'rows_for',lambda *a,**kw:rows)
    with pytest.raises(ValueError,match='improvement gate'):promote(store,candidate['id'])
    assert champion(store,league)==base['id']
