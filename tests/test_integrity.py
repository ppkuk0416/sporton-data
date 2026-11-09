from datetime import timedelta
import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from sporton.contracts import Game, Fixture, Market, utcnow
from sporton.data import ingest, add_market, collect_nba
from sporton.features import snapshot, dataset
from sporton.storage import digest
from conftest import make_games, write_csv


def test_no_data_no_prediction():
    f=Fixture(game_id='x',season='x',starts_at=utcnow()+timedelta(days=1),home_team='a',away_team='b')
    with pytest.raises(ValueError,match='insufficient'):snapshot([],f,utcnow())


def test_leakage_current_and_future_excluded(history):
    g=history[100]; f=Fixture.model_validate({k:v for k,v in g.model_dump().items() if k in Fixture.model_fields})
    cutoff=g.starts_at-timedelta(minutes=30)
    a=snapshot(history,f,cutoff,True)
    changed=history.copy();p=g.model_dump();p['home_score']+=20;p['home_periods'][-1]+=20;changed[100]=Game(**p)
    assert snapshot(changed,f,cutoff,True)==a
    assert all(int(x.split('-')[1])<100 for x in a['source_game_ids'])


def test_late_publication_excluded(history):
    g=history[100];f=Fixture.model_validate({k:v for k,v in g.model_dump().items() if k in Fixture.model_fields})
    late=history[99].model_dump();late['published_at']=g.starts_at+timedelta(hours=1);late['collected_at']=late['published_at']
    games=history[:99]+[Game(**late)]+history[100:]
    s=snapshot(games,f,g.starts_at-timedelta(minutes=30),True)
    assert 'TEST-99' not in s['source_game_ids']


@pytest.mark.parametrize('mutation',[
    {'home_score':1}, {'home_periods':[1,2,3,4]}, {'home_periods':[-1,2,3,4]},
    {'away_team':'FIXTURE-A'}, {'published_at':'2020-01-01T00:00:00Z'},
    {'starts_at':'2023-01-01T18:00:00'},
])
def test_bad_game(mutation):
    p=make_games(1)[0].model_dump();p.update(mutation)
    with pytest.raises(ValidationError):Game(**p)


def test_overtime_contract():
    p=make_games(1)[0].model_dump();p.update(home_score=110,away_score=108,home_periods=[25,25,25,25,10],away_periods=[25,25,25,25,8])
    assert sum(Game(**p).home_periods[:4])==100
    p['away_periods']=[24,25,25,25,9]
    with pytest.raises(ValidationError,match='tied regulation'):Game(**p)


def test_store_immutable_and_duplicate(store):
    store.append('model_predictions','p',{'v':1})
    with pytest.raises(ValueError,match='duplicate'):store.append('model_predictions','p',{'v':2})
    for operation in ("UPDATE model_predictions SET payload='{}'",'DELETE FROM model_predictions'):
        with pytest.raises(DatabaseError),store.engine.begin() as c:c.execute(text(operation))
    assert store.get('model_predictions','p')=={'v':1}


def test_ingest_idempotent_and_modes(store,tmp_path):
    path=write_csv(tmp_path/'test.csv',make_games(4))
    with pytest.raises(ValueError,match='separate'):ingest(store,path)
    assert ingest(store,path,True)==4
    assert ingest(store,path,True)==4
    assert len(store.rows('historical_game_results'))==4
    assert len(store.rows('source_failures'))==1


def test_market_exact_and_post_tipoff_rejected(store):
    now=utcnow();f=Fixture(game_id='future',season='2026-27',starts_at=now+timedelta(hours=1),home_team='a',away_team='b')
    store.append('fixtures',f.game_id,f.model_dump(mode='json'))
    p=dict(source='TEST/FIXTURE',source_id='test',collected_at=now.isoformat(),published_at=now.isoformat(),data_version='1',quality='TEST/FIXTURE',game_id='future',market_type='spread',scope='including_overtime',selection='home',line=-3.5,decimal_odds=1.91,bookmaker='TEST')
    key=add_market(store,p,True)
    assert store.get('market_snapshots',key)['line']==-3.5
    assert store.get('market_snapshots',key)['decimal_odds']==1.91
    p['collected_at']=(now+timedelta(hours=2)).isoformat()
    with pytest.raises(ValueError,match='tipoff'):add_market(store,p,True)


def test_unknown_publication_data_excluded():
    games=make_games(5)
    p=games[0].model_dump();p['quality']='unverified';games[0]=Game(**p)
    f=Fixture(game_id='f',season='s',starts_at=utcnow()+timedelta(days=1),home_team='FIXTURE-A',away_team='FIXTURE-B')
    with pytest.raises(ValueError,match='insufficient'):snapshot(games,f,utcnow())


def test_provider_failure_visible(store,monkeypatch):
    import sporton.data
    def fail(*a,**kw):raise TimeoutError('TEST/FIXTURE timeout')
    monkeypatch.setattr(sporton.data.httpx,'get',fail)
    result=collect_nba(store,['2025-26'])
    assert result[0]['status']=='failed'
    assert 'timeout' in store.rows('source_failures')[0]['payload']['error']


def test_injury_timestamp_prevents_post_game_knowledge(store):
    from sporton.data import add_injury,injuries_as_of
    now=utcnow();f=Fixture(game_id='injury-game',season='x',starts_at=now+timedelta(hours=1),home_team='a',away_team='b')
    store.append('fixtures',f.game_id,f.model_dump(mode='json'))
    p=dict(source='TEST/FIXTURE',source_id='injury:1',collected_at=now.isoformat(),published_at=now.isoformat(),data_version='1',quality='TEST/FIXTURE',game_id=f.game_id,player_id='FIXTURE-P',team_id='a',status='questionable')
    add_injury(store,p,True)
    later=now+timedelta(hours=3)
    p.update(status='out',source_id='injury:2',data_version='2',collected_at=later.isoformat(),published_at=later.isoformat())
    add_injury(store,p,True)
    known=injuries_as_of(store,f.game_id,now,True)
    assert len(known)==1 and known[0]['status']=='questionable'
    assert injuries_as_of(store,f.game_id,now-timedelta(seconds=1),True)==[]
