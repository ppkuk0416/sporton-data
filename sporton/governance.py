"""Evidence, explicit corrections and typed immutable report publication."""
from datetime import datetime
from typing import Any,Literal
from uuid import uuid4
from urllib.parse import urlparse
import math
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator,create_model
from sqlalchemy import text
from .contracts import utcnow,aware
from .storage import digest
from .schema import report_index,observation_index,sport_tables,SPORT_METRICS

PROTECTED={'model_predictions','professional_reports','critical_reviews','official_recommendations','analyst_reports','feature_snapshots'}


def append_revision(store,table,key,replacement,reason,actor,evidence):
    if table in PROTECTED:raise ValueError('frozen judgment: append a new POST_GAME/AUDIT report instead')
    if not reason.strip() or not actor.strip() or not isinstance(evidence,list) or not evidence or not all(isinstance(e,str) and e.strip() for e in evidence):raise ValueError('correction requires reason, actor and evidence')
    original=store.get(table,key)
    if original is None:raise ValueError('unknown original')
    if table in ('historical_game_results','actual_results'):
        from .sports.contracts import parse_game
        replacement=parse_game(replacement).model_dump(mode='json')
        if any(replacement[k]!=original[k] for k in ('game_id','league','starts_at','home_team','away_team')):raise ValueError('result identity change requires a new canonical fixture')
    elif table=='fixtures':
        from .sports.contracts import parse_fixture
        replacement=parse_fixture(replacement).model_dump(mode='json')
        if replacement['game_id']!=original['game_id']:raise ValueError('cannot change canonical game id')
    else:raise ValueError('corrections supported for fixtures and game results only')
    with store.engine.connect() as conn:
        database_actor=conn.execute(text('SELECT current_user')).scalar_one() if store.engine.dialect.name=='postgresql' else 'local-sqlite-process'
    key_revision=str(uuid4())
    payload={'target_table':table,'target_id':str(key),'original_sha256':digest(original),'replacement':replacement,
        'reason':reason,'actor':actor,'recorded_by_database_role':database_actor,'evidence':evidence,'recorded_at':utcnow().isoformat(),
        'availability_rule':'correction visible only after recorded_at; original source publication is retained'}
    store.append('record_revisions',key_revision,payload)
    return key_revision


def effective_get(store,table,key,as_of=None,conn=None):
    cutoff=as_of or utcnow();p=store.get(table,key,conn=conn)
    if p is None:return None
    changes=[r for r in store.rows('record_revisions',conn=conn) if r['payload']['target_table']==table and r['payload']['target_id']==str(key)
        and datetime.fromisoformat(r['payload']['recorded_at'])<=cutoff]
    return changes[-1]['payload']['replacement'] if changes else p


def games_as_of(store,league,cutoff=None,table='historical_game_results'):
    from .sports.contracts import parse_game
    cutoff=cutoff or utcnow();changes=store.rows('record_revisions');out=[]
    for r in store.rows(table):
        original=r['payload']
        if original['league']!=league:continue
        versions=[v for v in changes if v['payload']['target_table']==table and v['payload']['target_id']==r['id'] and datetime.fromisoformat(v['payload']['recorded_at'])<=cutoff]
        p=versions[-1]['payload']['replacement'] if versions else original
        if datetime.fromisoformat(p['published_at'])>cutoff:continue
        g=parse_game(p);object.__setattr__(g,'_knowledge_at',datetime.fromisoformat(versions[-1]['payload']['recorded_at']) if versions else g.published_at);object.__setattr__(g,'_record_version',versions[-1]['id'] if versions else 'original')
        out.append(g)
    return out


def identity_key(kind,league,provider,external_id):return digest([kind,league,provider,external_id])


def register_alias(store,kind,league,provider,external_id,canonical_id,evidence):
    if kind not in ('team','player','fixture') or not all((league,provider,external_id,canonical_id,evidence)):raise ValueError('complete, evidenced identity mapping required')
    key=identity_key(kind,league,provider,external_id);p={'kind':kind,'league':league,'provider':provider,'external_id':external_id,'canonical_id':canonical_id,'evidence':evidence}
    prior=store.get('identity_aliases',key)
    if prior and prior['canonical_id']!=canonical_id:
        store.append('validation_events',str(uuid4()),{'status':'CONFLICTED','kind':'identity_collision','existing':prior,'incoming':p,'at':utcnow().isoformat()})
        raise ValueError('conflicting identity mapping; no fuzzy replacement')
    store.append('identity_aliases',key,p,idempotent=True)
    return key


class Observation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source:str=Field(min_length=1)
    source_url:str
    source_record_id:str
    data_version:str
    collected_at:datetime
    published_at:datetime|None=None
    effective_at:datetime
    league:str
    season:str
    entity_id:str
    field:str
    value:Any=None
    fixture_id:str|None=None
    raw_sha256:str|None=None
    test_fixture:bool=False
    @field_validator('collected_at','published_at','effective_at')
    @classmethod
    def times(cls,v):return aware(v) if v else v
    @model_validator(mode='after')
    def validate_metadata(self):
        if urlparse(self.source_url).scheme!='https' or not urlparse(self.source_url).hostname:raise ValueError('HTTPS source URL required')
        if self.published_at and self.published_at>self.collected_at:raise ValueError('publication after collection')
        if not self.test_fixture and self.collected_at>utcnow():raise ValueError('future collection')
        digest(self.value) # Reject NaN/Infinity and non-JSON values.
        return self


def record_observation(store,payload):
    o=Observation.model_validate(payload)
    if o.fixture_id:
        fixture=store.get('fixtures',o.fixture_id)
        if not fixture or fixture['league']!=o.league or fixture['season']!=o.season:raise ValueError('observation fixture/league/season mismatch')
    p=o.model_dump(mode='json');key=digest([o.source,o.source_record_id,o.data_version,o.league,o.entity_id,o.field,o.effective_at.isoformat(),digest(o.value)])
    existing=store.get('source_observations',key)
    if existing:return key,observation_state(store,o.league,o.entity_id,o.field,o.effective_at)
    archived=[r['payload'] for r in store.rows('archive_datasets') if r['payload'].get('raw',{}).get('sha256')==o.raw_sha256 and r['payload'].get('source_url')==o.source_url]
    raw_verified=False
    if archived:
        from pathlib import Path
        import hashlib
        path=Path(archived[-1]['raw']['path'])
        raw_verified=path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==o.raw_sha256
    status='MISSING' if o.value is None else 'TEST/FIXTURE' if o.test_fixture else 'VERIFIED' if o.published_at and raw_verified else 'UNVERIFIED'
    p.update(status=status,validator='source-clock-hash-v1',source_evidence_verified=raw_verified)
    with store.transaction() as conn:
        store.append('source_observations',key,p,conn=conn)
        conn.execute(observation_index.insert().values(id=key,league=o.league,entity_id=o.entity_id,field=o.field,source=o.source,
            source_url=o.source_url,collected_at=o.collected_at,published_at=o.published_at,status=status,
            validator=p['validator'],fixture_id=o.fixture_id,value_hash=digest(o.value)))
    state=observation_state(store,o.league,o.entity_id,o.field,o.effective_at)
    store.append('validation_events',str(uuid4()),{'observation_id':key,'status':state['status'],'evidence_ids':state['evidence_ids'],'at':utcnow().isoformat(),'validator':p['validator']})
    return key,state


def observation_state(store,league,entity_id,field,effective_at):
    at=effective_at.isoformat() if isinstance(effective_at,datetime) else effective_at
    rows=[r for r in store.rows('source_observations') if all(r['payload'][k]==v for k,v in [('league',league),('entity_id',entity_id),('field',field)]) and datetime.fromisoformat(r['payload']['effective_at'])==datetime.fromisoformat(at)]
    values={digest(r['payload']['value']) for r in rows if r['payload']['value'] is not None}
    if len(values)>1:status='CONFLICTED'
    elif not values:status='MISSING'
    elif all(r['payload']['status']=='TEST/FIXTURE' for r in rows):status='TEST/FIXTURE'
    else:status='VERIFIED' if any(r['payload']['status']=='VERIFIED' for r in rows) else 'UNVERIFIED'
    return {'status':status,'evidence_ids':[r['id'] for r in rows],'validator':'cross-source-value-v1'}


RATIOS={'three_par':1.,'oreb_pct':1.,'dreb_pct':1.,'tov_pct':1.,'usage_pct':1.,'efg_pct':1.5,'ts_pct':1.5,'hard_hit_pct':1.}


def record_statistics(store,sport,observation_payload,measurements):
    if sport not in SPORT_METRICS:raise ValueError('unsupported sport schema')
    league=observation_payload['league']
    allowed={'basketball':{'NBA','KBL'},'baseball':{'MLB','KBO','NPB'},'football':{'EPL','LA_LIGA','BUNDESLIGA','SERIE_A','K_LEAGUE','UEFA'},'volleyball':{'KOVO','V_LEAGUE'},'esports':{'LOL','VALORANT'}}
    if league not in allowed[sport]:raise ValueError('sport/league schema mismatch')
    from sqlalchemy import String,Integer,Boolean
    fields={}
    for k,t in SPORT_METRICS[sport].items():
        pytype=str if t is String else int if t is Integer else bool if t is Boolean else float
        fields[k]=(pytype|None,None)
    model=create_model(sport+'Metrics',__config__=ConfigDict(extra='forbid',allow_inf_nan=False),**fields)
    values=model.model_validate(measurements).model_dump()
    for k,upper in RATIOS.items():
        if values.get(k) is not None and not 0<=values[k]<=upper:raise ValueError(f'invalid ratio {k}')
    if sport=='basketball' and all(values.get(k) is not None for k in ('ortg','drtg','net_rtg')):
        if abs(values['ortg']-values['drtg']-values['net_rtg'])>.05:raise ValueError('NetRtg != ORtg - DRtg')
    if sport=='esports' and (not values['game_title'] or not values['patch_version']):raise ValueError('game and patch version required')
    key,state=record_observation(store,{**observation_payload,'field':sport+'_statistics','value':values if any(v is not None for v in values.values()) else None})
    table=sport_tables[sport]
    with store.engine.begin() as conn:
        exists=conn.execute(table.select().where(table.c.id==key)).first()
        if not exists:conn.execute(table.insert().values(id=key,observation_id=key,league=observation_payload['league'],entity_id=observation_payload['entity_id'],**values))
    return key,state


def register_report(store,kind,fixture_id=None,prediction_id=None,review_id=None,content=None,data_mode='real',conn=None):
    if kind not in ('PRE_GAME','LIVE','POST_GAME','AUDIT','RESEARCH'):raise ValueError('invalid report kind')
    now=utcnow();fixture=effective_get(store,'fixtures',fixture_id,conn=conn) if fixture_id else None
    starts=datetime.fromisoformat(fixture['starts_at']) if fixture else None
    if kind in ('PRE_GAME','LIVE','POST_GAME') and not fixture:raise ValueError('fixture required')
    if kind=='LIVE' and (now<starts or store.get('actual_results',fixture_id,conn=conn)):raise ValueError('LIVE requires a started fixture without confirmed final')
    if kind=='POST_GAME':
        result=effective_get(store,'actual_results',fixture_id,conn=conn)
        if not result or datetime.fromisoformat(result['ends_at'])>now or datetime.fromisoformat(result['published_at'])>now:raise ValueError('verified ended result required')
    model_id=None;original_hash=digest(content or {});body=content or {}
    if prediction_id and kind!='PRE_GAME':
        source_prediction=store.get('model_predictions',prediction_id,conn=conn)
        if not source_prediction:raise ValueError('original prediction missing')
        model_id=source_prediction['model_id'];original_hash=digest(source_prediction)
    if data_mode not in ('real','TEST/FIXTURE'):raise ValueError('invalid data mode')
    if kind=='PRE_GAME':
        if now>=starts:raise ValueError('PRE_GAME publication after start forbidden')
        prediction=store.get('model_predictions',prediction_id,conn=conn);review=store.get('critical_reviews',review_id,conn=conn)
        if not prediction or prediction['fixture']!=fixture or not review or review['verdict']!='PASS' or review['prediction_id']!=prediction_id or review['report_hash']!=digest(prediction):raise ValueError('matching original prediction and independent PASS required')
        if datetime.fromisoformat(prediction['created_at'])>=starts:raise ValueError('original prediction was not pre-game')
        model_id=prediction['model_id'];meta=store.get('model_versions',model_id,conn=conn)
        if not meta or meta['data_mode']!=data_mode or meta['league']!=fixture['league']:raise ValueError('model/version league or data mode mismatch')
        blind=store.get('review_evidence',review.get('evidence_id'),conn=conn)
        if not blind or blind['fixture_id']!=fixture_id or datetime.fromisoformat(blind['created_at'])>datetime.fromisoformat(prediction['created_at']):raise ValueError('independent blind evidence missing or late')
        if datetime.fromisoformat(meta['trained_at'])>datetime.fromisoformat(prediction['snapshot']['feature_cutoff_timestamp']):raise ValueError('model unavailable at prediction cutoff')
        if store.get('feature_snapshots',prediction_id,conn=conn)!=prediction['snapshot']:raise ValueError('feature lineage missing or inconsistent')
        body={'original_prediction':prediction};original_hash=digest(prediction)
    key=kind+':'+prediction_id if prediction_id else str(uuid4())
    payload={'id':key,'kind':kind,'fixture_id':fixture_id,'prediction_id':prediction_id,'review_id':review_id,'model_id':model_id,
        'created_at':now.isoformat(),'data_mode':data_mode,'original_hash':original_hash,'content':body,
        'report_revision_policy':'immutable; corrections require a separately typed follow-up report'}
    def write(c):
        store.append('professional_reports',key,payload,conn=c)
        if store.engine.dialect.name!='postgresql':
            c.execute(report_index.insert().values(id=key,report_kind=kind,fixture_id=fixture_id,prediction_id=prediction_id,
                model_id=model_id,review_id=review_id,created_at=now,data_mode=data_mode,original_hash=original_hash))
    if conn is not None:write(conn)
    else:
        with store.transaction() as c:write(c)
    return key


def availability_verified(store,report):
    """Priced recommendations require confirmed lineups and player availability for both teams."""
    cutoff=datetime.fromisoformat(report['snapshot']['feature_cutoff_timestamp']);fixture=report['fixture']
    for entity in (fixture['home_team'],fixture['away_team']):
        for field in ('confirmed_lineup','player_availability'):
            candidates=[r['payload'] for r in store.rows('source_observations') if r['payload'].get('fixture_id')==fixture['game_id']
                and r['payload']['entity_id']==entity and r['payload']['field']==field and r['payload']['status']=='VERIFIED'
                and datetime.fromisoformat(r['payload']['collected_at'])<=cutoff and r['payload'].get('published_at')
                and datetime.fromisoformat(r['payload']['published_at'])<=cutoff]
            if not candidates:return False
            latest=max(candidates,key=lambda r:r['published_at'])
            if observation_state(store,fixture['league'],entity,field,latest['effective_at'])['status']!='VERIFIED':return False
    return True
