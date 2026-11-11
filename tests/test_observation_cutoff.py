"""TEST/FIXTURE: reproduce late evidence and cross-fixture contamination."""
from datetime import timedelta

from sporton.contracts import utcnow
from sporton.governance import record_observation, observation_state, availability_verified
from sporton.storage import digest
from test_persistence import observation


def test_same_source_record_is_separate_for_each_season(store):
    a=observation()
    first,_=record_observation(store,a)
    second,state=record_observation(store,{**a,'season':'OTHER-SEASON','value':99})
    assert first!=second
    assert state['status']=='TEST/FIXTURE'
    assert observation_state(store,'NBA',a['entity_id'],a['field'],a['effective_at'],season=a['season'])['evidence_ids']==[first]


def test_cutoff_excludes_late_collected_conflicts_and_backfilled_rows(store,monkeypatch):
    now=utcnow();a=observation()
    key,_=record_observation(store,a)
    cutoff=utcnow()
    # A later source contradicts the same effective instant.
    later=cutoff+timedelta(hours=1)
    record_observation(store,{**a,'source':'TEST/FIXTURE-B','value':99,'collected_at':later.isoformat(),'published_at':later.isoformat()})
    # Even backdated source clocks cannot bypass the database insertion clock.
    monkeypatch.setattr('sporton.storage.utcnow',lambda:later)
    record_observation(store,{**a,'source':'TEST/FIXTURE-C','value':98})
    historical=observation_state(store,'NBA',a['entity_id'],a['field'],a['effective_at'],as_of=cutoff)
    assert historical['status']=='TEST/FIXTURE' and historical['evidence_ids']==[key]
    assert observation_state(store,'NBA',a['entity_id'],a['field'],a['effective_at'])['status']=='CONFLICTED'


def test_cutoff_excludes_unpublished_and_future_effective_evidence(store):
    a=observation();future=utcnow()+timedelta(days=1)
    record_observation(store,{**a,'published_at':future.isoformat(),'collected_at':future.isoformat()})
    assert observation_state(store,'NBA',a['entity_id'],a['field'],a['effective_at'],as_of=utcnow())['status']=='MISSING'
    record_observation(store,{**a,'effective_at':future.isoformat()})
    assert observation_state(store,'NBA',a['entity_id'],a['field'],future,as_of=utcnow())['status']=='MISSING'


def seed_verified_availability(store):
    """Direct TEST/FIXTURE evidence; never a claim of real source verification."""
    now=utcnow();fixtures=[]
    for game in ('TEST-A','TEST-B'):
        f={'game_id':game,'league':'NBA','season':'TEST','starts_at':(now+timedelta(hours=2)).isoformat(),'home_team':'FIXTURE-H','away_team':'FIXTURE-A'}
        store.append('fixtures',game,f);fixtures.append(f)
    for entity in ('FIXTURE-H','FIXTURE-A'):
        for field in ('confirmed_lineup','player_availability'):
            a={**observation(),'fixture_id':'TEST-A','entity_id':entity,'field':field,'status':'VERIFIED','value':['TEST-PLAYER']}
            store.append('source_observations',digest([entity,field]),a)
    report={'fixture':fixtures[0],'snapshot':{'feature_cutoff_timestamp':utcnow().isoformat()}}
    return report,fixtures[1]


def test_other_fixture_conflict_does_not_change_availability(store):
    report,other=seed_verified_availability(store)
    assert availability_verified(store,report)
    old=store.rows('source_observations')[0]['payload']
    record_observation(store,{k:v for k,v in {**old,'fixture_id':other['game_id'],'value':['TEST-OTHER']}.items() if k!='status'})
    report['snapshot']['feature_cutoff_timestamp']=utcnow().isoformat()
    assert availability_verified(store,report)


def test_newer_missing_availability_blocks_old_verified_evidence(store):
    report,_=seed_verified_availability(store)
    assert availability_verified(store,report)
    a={**observation(),'fixture_id':report['fixture']['game_id'],'entity_id':'FIXTURE-H','field':'confirmed_lineup','value':None}
    record_observation(store,a)
    report['snapshot']['feature_cutoff_timestamp']=utcnow().isoformat()
    assert not availability_verified(store,report)


def test_legacy_observation_ids_remain_idempotent(store):
    from sporton.governance import Observation
    a=observation();o=Observation.model_validate(a);p=o.model_dump(mode='json')
    legacy=digest([o.source,o.source_record_id,o.data_version,o.league,o.entity_id,o.field,o.effective_at.isoformat(),digest(o.value)])
    store.append('source_observations',legacy,{**p,'status':'TEST/FIXTURE','validator':'legacy-test'})
    key,_=record_observation(store,a)
    assert key==legacy and len(store.rows('source_observations'))==1
    other,_=record_observation(store,{**a,'season':'OTHER-SEASON'})
    assert other!=legacy and len(store.rows('source_observations'))==2
