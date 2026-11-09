import csv
import json
from datetime import datetime, timedelta, timezone
import numpy as np
import pytest
from sporton.contracts import Game
from sporton.storage import Store


@pytest.fixture
def store(tmp_path,monkeypatch):
    monkeypatch.setattr('sporton.collection.collect_due',lambda *a,**kw:[{'status':'TEST/FIXTURE external collection disabled'}])
    monkeypatch.setenv('SPORTON_HOME',str(tmp_path))
    monkeypatch.setenv('SPORTON_DATABASE_URL',f'sqlite:///{tmp_path}/test.db')
    return Store()


def make_games(n=300):
    """TEST/FIXTURE only. Team names intentionally fictitious."""
    rng=np.random.default_rng(4321)
    start=datetime(2023,1,1,18,tzinfo=timezone.utc)
    games=[]
    for i in range(n):
        a,b=(('FIXTURE-A','FIXTURE-B') if i%2==0 else ('FIXTURE-B','FIXTURE-A'))
        scores=[int(100+rng.integers(-10,15)),int(100+rng.integers(-10,15))]
        if scores[0]==scores[1]: scores[0]+=1
        periods=[[s//4]*3+[s-3*(s//4)] for s in scores]
        t=start+timedelta(days=i)
        games.append(Game(source='TEST/FIXTURE generator',source_id=f'fixture:{i}',collected_at=t+timedelta(hours=4),
            published_at=t+timedelta(hours=3),data_version='fixture-v1',quality='TEST/FIXTURE',
            game_id=f'TEST-{i}',season='TEST-SEASON',starts_at=t,ends_at=t+timedelta(hours=2),
            home_team=a,away_team=b,home_score=scores[0],away_score=scores[1],
            home_periods=periods[0],away_periods=periods[1],home_possessions=99,away_possessions=99))
    return games


def write_csv(path,games):
    rows=[g.model_dump(mode='json') for g in games]
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=rows[0].keys());writer.writeheader()
        for row in rows:
            for key in ('home_periods','away_periods'): row[key]=json.dumps(row[key])
            writer.writerow(row)
    return path


@pytest.fixture
def history(store,tmp_path):
    from sporton.data import ingest
    games=make_games()
    path=write_csv(tmp_path/'TEST_FIXTURE.csv',games)
    ingest(store,path,True)
    return games
