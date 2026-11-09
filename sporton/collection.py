"""Direct collection of independent licensed archives and an official KBL page.

This is not a relay to blocked sites. Publication timestamps are never invented.
"""
from datetime import datetime,timedelta
from pathlib import Path
from uuid import uuid4
import hashlib
import json
import csv
import io
import httpx
from .contracts import utcnow
from .storage import home,digest

ROOT='https://raw.githubusercontent.com'
SOURCES={
 'kbl-official':{'league':'KBL','url':'https://www.kbl.or.kr/','format':'html','cadence_hours':6,
     'license_url':None,'license':'public_page_training_rights_unverified','grade':'discovery_only'},
 'nba-elo-archive':{'league':'NBA','url':ROOT+'/fivethirtyeight/data/master/nba-elo/nbaallelo.csv','format':'csv','cadence_hours':168,
     'license_url':ROOT+'/fivethirtyeight/data/master/README.md','license':'CC-BY-4.0; attribution: FiveThirtyEight, data from Basketball-Reference','grade':'date_only_archive'},
 **{f'epl-{season}':{'league':'EPL','season':season,'url':ROOT+f'/openfootball/football.json/master/{season}/en.1.json','format':'json','cadence_hours':24,
     'license_url':ROOT+'/openfootball/football.json/master/LICENSE.md','license':'CC0-1.0; attribution: OpenFootball contributors','grade':'date_only_archive'}
    for season in ('2023-24','2024-25','2025-26')},
}


def retain(blob,suffix):
    sha=hashlib.sha256(blob).hexdigest();directory=home()/'raw';directory.mkdir(exist_ok=True)
    path=directory/(sha+'.'+suffix)
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('raw archive checksum mismatch')
    if not path.exists():
        with path.open('xb') as f:f.write(blob)
    return {'path':str(path.resolve()),'sha256':sha,'bytes':len(blob)}


def football_score(game):
    value=game.get('score')
    score=value.get('ft') if isinstance(value,dict) else value
    if not isinstance(score,list) or len(score)!=2 or any(type(v) is not int or v<0 for v in score):return None
    return score


def classify(source,body):
    if source['format']=='csv':
        rows=list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))
        if not rows or not {'game_id','date_game','_iscopy','lg_id'}<=set(rows[0]):raise ValueError('unexpected NBA archive schema')
        games=[r for r in rows if r['lg_id']=='NBA' and r['_iscopy']=='0']
        dates=[r['date_game'] for r in games]
        from datetime import datetime
        parsed=[datetime.strptime(d,'%m/%d/%Y').date().isoformat() for d in dates]
        return {'games':len(games),'raw_rows':len(rows),'period':[min(parsed),max(parsed)],'published_at':None,'tipoff_timezone_known':False}
    if source['format']=='json':
        data=json.loads(body);matches=data.get('matches')
        if not isinstance(matches,list) or not matches:raise ValueError('empty/malformed football archive')
        completed=[g for g in matches if football_score(g) is not None]
        dates=[g['date'] for g in completed]
        if not dates:raise ValueError('archive contains no completed results')
        return {'games':len(completed),'scheduled_matches':len(matches),'period':[min(dates),max(dates)],'published_at':None,'tipoff_timezone_known':False}
    return {'games':0,'extractor':'not_implemented','reason':'Official app endpoint/terms must be inspected after access works.','published_at':None}


def collect_source(store,key,client=None,force=False):
    if key not in SOURCES:raise ValueError('unknown source')
    source=SOURCES[key];now=utcnow()
    store.append('data_sources',key,{**source,'source_key':key},idempotent=True)
    history=[r['payload'] for r in store.rows('source_collections') if r['payload']['source_key']==key]
    if history and not force and (now-datetime.fromisoformat(history[-1]['attempted_at'])).total_seconds()<source['cadence_hours']*3600:
        return {'source_key':key,'status':'not_due','previous_status':history[-1]['status']}
    own=client is None;client=client or httpx.Client(timeout=25,follow_redirects=False)
    attempt={'source_key':key,'league':source['league'],'url':source['url'],'attempted_at':now.isoformat(),'data_grade':source['grade']}
    try:
        license_meta=None
        if source['license_url']:
            response=client.get(source['license_url']);response.raise_for_status()
            text=response.text
            expected='Creative Commons Attribution 4.0' if key=='nba-elo-archive' else 'CC0 1.0 Universal'
            if expected not in text:raise ValueError('expected source license statement missing; collection stopped')
            license_meta=retain(response.content,'license.txt')
        response=client.get(source['url']);response.raise_for_status()
        if len(response.content)>32*1024*1024:raise ValueError('source response exceeds 32 MiB limit')
        raw=retain(response.content,source['format'])
        meta=classify(source,response.content)
        dataset={'source_key':key,'league':source['league'],'season':source.get('season'),'source_url':source['url'],
            'collected_at':utcnow().isoformat(),'original_published_at':None,'license':source['license'],'license_evidence':license_meta,
            'raw':raw,'metadata':meta,'data_grade':source['grade'],'production_eligible':False,
            'limitations':['Exact historical publication times unavailable.','Snapshot may contain later corrections.','Not a verified live pre-game dataset.']}
        dataset_id=key+':'+raw['sha256']
        # Repeated same bytes preserve the first observation and original source metadata.
        if not store.get('archive_datasets',dataset_id):store.append('archive_datasets',dataset_id,dataset)
        attempt.update(status='collected',dataset_id=dataset_id,sha256=raw['sha256'],games=meta['games'])
    except Exception as exc:
        attempt.update(status='failed',error_type=type(exc).__name__,error=str(exc))
        store.append('source_failures',str(uuid4()),{'league':source['league'],'source':source['url'],'source_key':key,'at':utcnow().isoformat(),
            'error_type':type(exc).__name__,'error':str(exc)})
    finally:
        if own:client.close()
    store.append('source_collections',str(uuid4()),attempt)
    return attempt


def collect_due(store,force=False,keys=None):
    results=[]
    with httpx.Client(timeout=25,follow_redirects=False) as client:
        for key in (keys or SOURCES):results.append(collect_source(store,key,client,force))
    return results


def status(store):
    datasets=store.rows('archive_datasets');attempts=store.rows('source_collections')
    result=[]
    for key,source in SOURCES.items():
        versions=[r for r in datasets if r['payload']['source_key']==key]
        checks=[r for r in attempts if r['payload']['source_key']==key]
        result.append({'source_key':key,'league':source['league'],'url':source['url'],'cadence_hours':source['cadence_hours'],
            'license':source['license'],'data_grade':source['grade'],'production_eligible':False,
            'last_attempt':checks[-1]['payload'] if checks else None,'latest_archive':versions[-1] if versions else None})
    return result
