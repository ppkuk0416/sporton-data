from datetime import timedelta
import pytest
from sporton.contracts import Fixture, Game, utcnow
from sporton.engine import forecast, audit, publish, daily
from sporton.models import train, load_model, promote, walk_forward, chronological_blocks
from sporton.features import dataset
from sporton.review import NBACriticalAnalyst
from sporton.storage import digest
from conftest import make_games, write_csv


@pytest.fixture
def trained(store,history):
    return train(store,True)


def future():
    return Fixture(game_id='TEST-future',season='TEST-SEASON',starts_at=utcnow()+timedelta(hours=2),home_team='FIXTURE-A',away_team='FIXTURE-B')


def test_end_to_end_training_forecast_audit(store,trained,monkeypatch):
    import sporton.engine as engine
    m=trained
    assert set(m['candidate_validation'])=={'elo','logistic','lightgbm'}
    assert m['test']['n']>0 and m['test']['brier_95ci'][0]<=m['test']['brier']<=m['test']['brier_95ci'][1]
    result=forecast(store,future().model_dump(mode='json'),m['id'],test_mode=True)
    assert result['review']['verdict']=='PASS'
    assert result['decision']['state']=='WATCH'
    assert result['decision']['expected_value'] is None
    prediction=result['report'];original=digest(prediction)
    f=Fixture(**prediction['fixture']);ends=f.starts_at+timedelta(hours=2)
    game=Game(**f.model_dump(),ends_at=ends,home_score=104,away_score=100,home_periods=[26]*4,away_periods=[25]*4,
        source='TEST/FIXTURE',source_id='final',published_at=ends,collected_at=ends,data_version='1',quality='TEST/FIXTURE')
    store.append('actual_results',f.game_id,game.model_dump(mode='json'))
    monkeypatch.setattr(engine,'utcnow',lambda:ends+timedelta(minutes=1))
    evaluations=audit(store)
    assert len(evaluations)==1 and evaluations[0]['data_mode']=='TEST/FIXTURE'
    assert audit(store)==[]
    assert digest(store.get('model_predictions',prediction['id']))==original


def test_split_time_order(history):
    blocks=chronological_blocks(dataset(history,True))
    for a,b in zip(blocks,blocks[1:]):
        assert max(r['game'].published_at for r in a)<min(r['game'].starts_at for r in b)
    assert not set(r['game'].game_id for r in blocks[0]) & set(r['game'].game_id for r in blocks[-1])


def test_holdout_cannot_be_reused(store,trained):
    with pytest.raises(ValueError,match='already evaluated'):train(store,True)


def test_fixture_model_cannot_promote(store,trained):
    with pytest.raises(ValueError,match='TEST/FIXTURE'):promote(store,trained['id'])


def test_no_post_start_forecast(store,trained):
    f=future();p=f.model_dump();p['starts_at']=utcnow()-timedelta(minutes=1)
    with pytest.raises(ValueError,match='tipoff'):forecast(store,p,trained['id'],test_mode=True)


def test_no_manual_no_publish(store):
    with pytest.raises(ValueError,match='PASS'):publish(store,'missing','missing',{})


def test_negative_review_cannot_publish(store,trained):
    report=forecast(store,future().model_dump(),trained['id'],test_mode=True)['report']
    store.append('critical_reviews','NO',{'prediction_id':report['id'],'verdict':'NO','report_hash':digest(report)})
    with pytest.raises(ValueError,match='PASS'):publish(store,report['id'],'NO',{})


def test_duplicate_recommendation(store,trained):
    f=future().model_dump()
    forecast(store,f,trained['id'],test_mode=True)
    with pytest.raises(ValueError,match='duplicate'):forecast(store,f,trained['id'],test_mode=True)


def test_artifact_tampering(store,trained):
    from pathlib import Path
    Path(trained['artifact_path']).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='checksum'):load_model(store,trained['id'])


def test_challenger_regression_blocked(store,trained,history,monkeypatch):
    # TEST/FIXTURE registry fixtures model a worse candidate; not a real-data claim.
    from sporton.models import promote
    base=dict(trained);base['data_mode']='real';base['id']='test-champion';base['selection_end']='2022-01-01T00:00:00+00:00'
    store.append('model_versions',base['id'],base)
    store.append('model_promotions','test-bootstrap',{'to_model':base['id'],'from_model':None,'action':'TEST/FIXTURE'})
    candidate=dict(trained);candidate['id']='test-challenger';candidate['data_mode']='real'
    store.append('model_versions',candidate['id'],candidate)
    import sporton.models as models
    monkeypatch.setattr(models, 'dataset', lambda games,*args,**kwargs: dataset(history, True))
    # Same artifact cannot clear the strict improvement margin on identical future games.
    with pytest.raises(ValueError,match='improvement gate'):promote(store,candidate['id'])


def test_walk_forward_executes(store,history):
    result=walk_forward(store,True)
    assert len(result['folds'])==3
    assert all(f['models']['lightgbm']['n']>0 for f in result['folds'])


def test_api_empty_and_fixture_exclusion(store,trained):
    from fastapi.testclient import TestClient
    from sporton.api import app
    with TestClient(app) as c:
        assert c.get('/health').json()['status']=='ok'
        assert 'SPORTON' in c.get('/').text
        overview=c.get('/api/overview').json()
        assert overview['counts']['verified_games']==0
        assert overview['performance']['brier'] is None
        assert c.get('/api/predictions/missing').status_code==404
