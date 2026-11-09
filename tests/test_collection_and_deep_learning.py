"""Mock source bodies and model samples below are TEST/FIXTURE only."""
from datetime import date,timedelta
import hashlib,json
from pathlib import Path
import httpx
import numpy as np
import pytest
from sporton.collection import collect_source,classify,SOURCES
from sporton.archive_research import archive_rows,run_epl_research
from sporton.storage import digest


def mock_client(payload,license_text='CC0 1.0 Universal'):
    def handle(request):
        if request.url.path.endswith('LICENSE.md'):return httpx.Response(200,text=license_text)
        return httpx.Response(200,json=payload)
    return httpx.Client(transport=httpx.MockTransport(handle))


def source_body():
    return {'name':'TEST/FIXTURE','matches':[
        {'date':'2023-08-01','team1':'FIXTURE-A','team2':'FIXTURE-B','score':{'ft':[1,0]}},
        {'date':'2023-08-02','team1':'FIXTURE-A','team2':'FIXTURE-B','score':[0,0]},
        {'date':'2023-08-03','team1':'FIXTURE-A','team2':'FIXTURE-B','score':None}]}


def test_collect_license_integrity_duplicate_and_grade(store):
    with mock_client(source_body()) as c:
        first=collect_source(store,'epl-2023-24',c,True)
        assert first['status']=='collected' and first['games']==2
        collect_source(store,'epl-2023-24',c,True)
        assert collect_source(store,'epl-2023-24',c)['status']=='not_due'
    assert len(store.rows('archive_datasets'))==1
    record=store.rows('archive_datasets')[0]['payload']
    assert record['original_published_at'] is None and record['production_eligible'] is False
    assert record['data_grade']=='date_only_archive'
    assert hashlib.sha256(Path(record['raw']['path']).read_bytes()).hexdigest()==record['raw']['sha256']
    assert not store.rows('historical_game_results') and not store.rows('model_versions')


def test_missing_license_stops_collection(store):
    with mock_client(source_body(),'TEST/FIXTURE no license') as c:
        result=collect_source(store,'epl-2023-24',c,True)
    assert result['status']=='failed' and 'license' in result['error']
    assert not store.rows('archive_datasets')
    assert store.rows('source_failures')


def test_proxy_failure_is_preserved(store):
    def deny(request):raise httpx.ProxyError('TEST/FIXTURE 403 Forbidden')
    with httpx.Client(transport=httpx.MockTransport(deny)) as c:result=collect_source(store,'kbl-official',c,True)
    assert result['status']=='failed' and result['error_type']=='ProxyError'
    assert not store.rows('archive_datasets')


def build_archive(tmp_path,year,n=80):
    matches=[]
    for i in range(n):
        score=([1,0],[1,1],[0,1])[i%3]
        matches.append({'date':str(date(year,8,1)+timedelta(days=i)),'team1':'FIXTURE-A','team2':'FIXTURE-B','score':{'ft':score}})
    raw=json.dumps({'name':'TEST/FIXTURE','matches':matches}).encode();path=tmp_path/f'TEST_{year}.json';path.write_bytes(raw)
    return {'source_key':f'epl-{year}-{str(year+1)[-2:]}','season':f'{year}-{str(year+1)[-2:]}','source_url':'fixture:TEST/FIXTURE',
        'raw':{'path':str(path),'sha256':hashlib.sha256(raw).hexdigest()},'grade':'TEST/FIXTURE date-only'}


def test_archive_features_exclude_current_date_and_future(tmp_path):
    archive=build_archive(tmp_path,2023)
    rows=archive_rows([archive])
    assert rows
    for row in rows:assert all(key.split(':')[0]<row['feature_cutoff_date'] for key in row['source_prior_game_ids'])
    target=rows[10];raw=json.loads(Path(archive['raw']['path']).read_text())
    # Changing the current result cannot alter its own features.
    raw['matches'][target['source_row']]['score']['ft']=[9,8]
    content=json.dumps(raw).encode();Path(archive['raw']['path']).write_bytes(content)
    with pytest.raises(ValueError,match='checksum'):archive_rows([archive])
    archive['raw']['sha256']=hashlib.sha256(content).hexdigest()
    changed=next(r for r in archive_rows([archive]) if r['date']==target['date'])
    assert changed['x']==target['x']


def samples(n=120,classes=3,features=2):
    return [{'x':[float(i%classes),float(i%7)]+[0.]*(features-2),'y':i%classes,'scores':[float(i%classes+1),1.]} for i in range(n)]


def test_torch_actual_fit_calibration_serialization_and_shape(tmp_path):
    pytest.importorskip('torch')
    import joblib
    from sporton.deep_learning import TorchMultitask
    rows=samples();model=TorchMultitask().fit(rows[:90],rows[90:],['form','rest'],['away','draw','home'])
    X=np.array([r['x'] for r in rows[-15:]])
    p=model.probabilities(X);scores=model.scores(X)
    assert p.shape==(15,3) and scores.shape==(15,2)
    assert np.isfinite(p).all() and np.allclose(p.sum(axis=1),1) and (scores>=0).all()
    assert model.loss_history[-1]<model.loss_history[0]
    assert model.scaler.mean_[1]==np.mean([r['x'][1] for r in rows[:90]])
    path=tmp_path/'TEST-model.joblib';joblib.dump(model,path)
    assert np.allclose(joblib.load(path).probabilities(X),p)
    with pytest.raises(ValueError,match='feature matrix'):model.probabilities(np.zeros((1,3)))


def test_nba_deep_head_uses_existing_contract():
    pytest.importorskip('torch')
    from sporton.models import fit_candidate,probabilities,score_predictions
    from sporton.features import FEATURES
    rows=samples(classes=2,features=len(FEATURES));artifact=fit_candidate('pytorch_mlp',rows[:90],rows[90:])
    X=np.array([r['x'] for r in rows[-10:]])
    assert probabilities(artifact,X).shape==(10,)
    assert score_predictions(artifact,X).shape==(10,2)


def test_research_remains_separate_and_holdout_cannot_be_reused(store,tmp_path,monkeypatch):
    pytest.importorskip('torch')
    datasets=[]
    for year in (2023,2024,2025):
        archive=build_archive(tmp_path,year);datasets.append(archive)
        store.append('archive_datasets',archive['source_key'],archive)
    result=run_epl_research(store)
    assert result['production_eligible'] is False
    assert set(result['test'])=={'goal_poisson','form_logistic','pytorch_mlp'}
    assert not store.rows('model_versions') and not store.rows('official_recommendations')
    from sporton.deep_learning import TorchMultitask
    def fail(*a,**kw):raise AssertionError('TEST/FIXTURE must not retrain the same holdout')
    monkeypatch.setattr(TorchMultitask,'fit',fail)
    assert run_epl_research(store)==result
    # A later correction to an earlier season is not a new final-test window.
    d=dict(datasets[0]);raw=json.loads(Path(d['raw']['path']).read_text());raw['matches'][0]['score']['ft']=[2,1]
    content=json.dumps(raw).encode();new_path=tmp_path/'TEST_corrected.json';new_path.write_bytes(content)
    d={**d,'raw':{'path':str(new_path),'sha256':hashlib.sha256(content).hexdigest()}}
    store.append('archive_datasets','TEST-corrected',d)
    with pytest.raises(ValueError,match='same final-test games'):run_epl_research(store)
