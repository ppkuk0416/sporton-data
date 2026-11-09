"""Immutable Parquet datasets and fixed DuckDB analytics; no production-quality upgrade."""
from pathlib import Path
from datetime import datetime
import csv,json,hashlib
from uuid import uuid4
import pyarrow as pa
import pyarrow.parquet as pq
import duckdb
from .storage import home,digest
from .contracts import utcnow
from .collection import football_score


def export_archive(store,dataset_id):
    dataset=store.get('archive_datasets',dataset_id)
    if not dataset:raise ValueError('archive dataset not found')
    raw=dataset['raw'];path=Path(raw['path']);body=path.read_bytes()
    if hashlib.sha256(body).hexdigest()!=raw['sha256']:raise ValueError('source checksum mismatch')
    key=digest(['archive-parquet-v1',dataset_id,raw['sha256']])
    old=store.get('lake_exports',key)
    if old:
        if hashlib.sha256(Path(old['path']).read_bytes()).hexdigest()!=old['sha256']:raise ValueError('Parquet checksum mismatch')
        return old
    rows=[]
    if dataset['league']=='EPL':
        for index,game in enumerate(json.loads(body)['matches']):
            score=football_score(game)
            if score is None:continue
            rows.append({'game_id':dataset['season']+':'+str(index),'league':'EPL','season':dataset['season'],'game_date':game['date'],
                'team1':game['team1'],'team2':game['team2'],'score1':score[0],'score2':score[1],'location':'home',
                'data_grade':'date_only_archive','source_url':dataset['source_url'],'source_sha256':raw['sha256'],'published_at':None})
    elif dataset['league']=='NBA':
        import io
        for g in csv.DictReader(io.StringIO(body.decode('utf-8-sig'))):
            if g['lg_id']!='NBA' or g['_iscopy']!='0':continue
            rows.append({'game_id':g['game_id'],'league':'NBA','season':g['year_id'],
                'game_date':datetime.strptime(g['date_game'],'%m/%d/%Y').date().isoformat(),
                'team1':g['team_id'],'team2':g['opp_id'],'score1':int(g['pts']),'score2':int(g['opp_pts']),'location':g['game_location'],
                'data_grade':'date_only_archive','source_url':dataset['source_url'],'source_sha256':raw['sha256'],'published_at':None})
    else:raise ValueError('archive adapter not implemented for this league')
    if not rows:raise ValueError('zero-row export is not validation')
    directory=home()/'lake'/('dataset='+key);directory.mkdir(parents=True,exist_ok=True)
    output=directory/'games.parquet'
    if output.exists():raise ValueError('unregistered output exists; preserve and investigate')
    table=pa.Table.from_pylist(rows)
    pq.write_table(table,output,compression='zstd')
    result={'id':key,'source_key':dataset['source_key'],'dataset_id':dataset_id,'path':str(output.resolve()),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
        'row_count':len(rows),'league':dataset['league'],'source_sha256':raw['sha256'],'at':utcnow().isoformat(),
        'data_grade':'date_only_archive','production_eligible':False}
    store.append('lake_exports',key,result)
    return result


def export_events(store,table_name):
    rows=store.rows(table_name)
    if not rows:raise ValueError('no rows to export')
    key=digest([table_name,rows]);old=store.get('lake_exports',key)
    if old:return old
    directory=home()/'lake'/('events='+table_name)/key;directory.mkdir(parents=True,exist_ok=True)
    path=directory/'records.parquet'
    table=pa.Table.from_pylist([{'id':r['id'],'created_at':r['created_at'],'payload_json':json.dumps(r['payload'],sort_keys=True,ensure_ascii=False),'payload_sha256':digest(r['payload'])} for r in rows])
    pq.write_table(table,path,compression='zstd')
    result={'id':key,'entity':table_name,'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'row_count':len(rows),'at':utcnow().isoformat(),'data_grade':'event_snapshot'}
    store.append('lake_exports',key,result)
    return result


def summary(store):
    exports=[r['payload'] for r in store.rows('lake_exports') if r['payload'].get('dataset_id')]
    latest={r.get('source_key',r['dataset_id']):r for r in exports};paths=[]
    for item in latest.values():
        path=Path(item['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('lake integrity failure')
        paths.append(str(path))
    if not paths:return []
    with duckdb.connect() as conn:
        result=conn.execute('SELECT league, season, data_grade, COUNT(*) AS games, MIN(game_date) AS first_date, MAX(game_date) AS last_date FROM read_parquet(?) GROUP BY league,season,data_grade ORDER BY league,season',[paths])
        keys=[d[0] for d in result.description]
        return [dict(zip(keys,r)) for r in result.fetchall()]
