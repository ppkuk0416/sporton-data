"""Retrospective EPL experiment with explicitly low-trust date-only availability.

Never writes production fixtures, model_versions or recommendations. Public dates
are not fabricated tipoff/publication timestamps. Archived corrections can leak
later knowledge; results are research evidence, not a verified betting edge.
"""
from collections import defaultdict
from pathlib import Path
import json
import hashlib
import math
import joblib
import numpy as np
from .contracts import utcnow
from .storage import home,digest
from .sports.football import FootballPlugin
from .deep_learning import TorchMultitask
from .collection import football_score


def archive_rows(datasets):
    matches=[]
    for dataset in datasets:
        raw=dataset['raw'];body=Path(raw['path']).read_bytes()
        if hashlib.sha256(body).hexdigest()!=raw['sha256']:raise ValueError('archive checksum mismatch')
        for i,g in enumerate(json.loads(body)['matches']):
            score=football_score(g)
            if not score or len(score)!=2:continue
            matches.append({'date':g['date'],'home':g['team1'],'away':g['team2'],'scores':score,
                'season':dataset['season'],'source':dataset['source_url'],'source_sha256':raw['sha256'],'source_row':i})
    matches.sort(key=lambda g:(g['date'],g['home'],g['away']))
    by_day=defaultdict(list)
    for g in matches:by_day[g['date']].append(g)
    history=defaultdict(list);rows=[];features=FootballPlugin.features
    for day,games in sorted(by_day.items()):
        # Every fixture on the day sees only results from strictly earlier days.
        for g in games:
            values={};ids=[]
            for side in ('home','away'):
                prior=history[g[side]][-8:]
                if len(prior)<3:break
                scored=[p['for'] for p in prior];allowed=[p['against'] for p in prior]
                from datetime import date
                days=(date.fromisoformat(day)-date.fromisoformat(prior[-1]['date'])).days
                values.update({side+'_goals_for':float(np.mean(scored)),side+'_goals_against':float(np.mean(allowed)),
                    side+'_draw_rate':float(np.mean(np.array(scored)==allowed)),side+'_rest':float(min(21,days)),
                    side+'_xg_for':0.,side+'_xg_against':0.,side+'_ppda':0.,side+'_advanced_known':0.})
                ids.extend(p['id'] for p in prior)
            else:
                h,a=g['scores'];y=1 if h==a else 2 if h>a else 0
                rows.append({**g,'y':y,'x':[values[k] for k in features],
                    'feature_cutoff_date':day,'source_prior_game_ids':sorted(set(ids))})
        for g in games:
            key=g['date']+':'+g['home']+':'+g['away'];h,a=g['scores']
            history[g['home']].append({'date':day,'for':h,'against':a,'id':key})
            history[g['away']].append({'date':day,'for':a,'against':h,'id':key})
    return rows


def metrics(model,rows):
    X=np.array([r['x'] for r in rows]);y=np.array([r['y'] for r in rows]);p=model.probabilities(X)
    scores=model.scores(X);actual=np.array([r['scores'] for r in rows]);n=len(y)
    losses=.5*np.sum((p-np.eye(3)[y])**2,axis=1)
    correct=(p.argmax(axis=1)==y).astype(float);logs=-np.log(np.clip(p[np.arange(n),y],1e-12,1))
    score_error=np.abs(actual-scores).mean(axis=1)
    result={'n':n,'accuracy':float(correct.mean()),'brier':float(losses.mean()),'log_loss':float(logs.mean()),
        'score_mae':float(score_error.mean()),'period':[rows[0]['date'],rows[-1]['date']],
        'sources':sorted({r['source'] for r in rows}),'data_grade':'date_only_archive','production_eligible':False}
    indices=np.random.default_rng(2026).integers(0,n,(400,n))
    for key,values in [('accuracy',correct),('brier',losses),('log_loss',logs),('score_mae',score_error)]:
        result[key+'_95ci']=np.quantile(values[indices].mean(axis=1),[.025,.975]).tolist()
    return result


def run_epl_research(store):
    datasets=[]
    for season in ('2023-24','2024-25','2025-26'):
        versions=[r['payload'] for r in store.rows('archive_datasets') if r['payload']['source_key']=='epl-'+season]
        if not versions:raise ValueError('missing independently collected EPL archive '+season)
        datasets.append(versions[-1])
    data_hash=digest([d['raw']['sha256'] for d in datasets]);key='epl-archive-research:'+data_hash
    existing=store.get('training_runs',key)
    if existing:return existing
    rows=archive_rows(datasets)
    fit=[r for r in rows if r['season']=='2023-24'];mid=[r for r in rows if r['season']=='2024-25'];test=[r for r in rows if r['season']=='2025-26']
    days=sorted({r['date'] for r in mid});boundary=days[len(days)//2]
    cal=[r for r in mid if r['date']<boundary];val=[r for r in mid if r['date']>=boundary]
    if min(map(len,(fit,cal,val,test)))<30:raise ValueError('insufficient independent chronological blocks')
    for block in (fit,cal):
        if {r['y'] for r in block}!={0,1,2}:raise ValueError('missing football outcome class')
    test_identity=digest([(r['date'],r['home'],r['away']) for r in test])
    if any(r['payload'].get('test_game_ids_hash')==test_identity for r in store.rows('training_runs')):
        raise ValueError('same final-test games already used; source corrections are not a new future holdout')
    plan={'test_game_ids_hash':test_identity,'data_hash':data_hash,'league':'EPL','purpose':'date_only_retrospective_research','production_eligible':False,
        'splits':dict(zip(('fit','calibration','validation','test'),map(len,(fit,cal,val,test)))),
        'fixed_candidates':['goal_poisson','form_logistic','pytorch_mlp'],'mlp':{'epochs':80,'seed':2026,'hidden':[32,16],'lr':.003},
        'selection':'validation Brier; final test once for predeclared candidates; never promotes a production model',
        'limitations':['No original publication timestamps or tipoff timezone evidence.',
            'Strict earlier-date rolling features reduce same-day/result leakage but cannot exclude later archive corrections.',
            'This is not a verified historical as-of simulation or a live NBA/KBL prediction.',
            'Bootstrap confidence intervals assume independent games.']}
    store.append('training_runs','plan:'+key,plan,idempotent=True)
    adapter=FootballPlugin('EPL');models={};validation={}
    for kind in plan['fixed_candidates']:
        model=TorchMultitask() if kind=='pytorch_mlp' else None
        model=model.fit(fit,cal,adapter.features,adapter.outcomes) if model else adapter.fit(kind,fit,cal)
        models[kind]=model;validation[kind]=metrics(model,val)
    chosen=min(validation,key=lambda k:validation[k]['brier'])
    # Choice is frozen before any final test scores are computed.
    result={**plan,'at':utcnow().isoformat(),'selected_on_validation':chosen,'validation':validation,
        'test':{kind:metrics(model,test) for kind,model in models.items()},'artifacts':{}}
    directory=home()/'research'/data_hash;directory.mkdir(parents=True,exist_ok=True)
    for kind,model in models.items():
        path=directory/(kind+'.joblib');joblib.dump(model,path)
        result['artifacts'][kind]={'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    result['deep_learning_training_loss']=[models['pytorch_mlp'].loss_history[0],models['pytorch_mlp'].loss_history[-1]]
    result['deep_learning_best_on_validation']=chosen=='pytorch_mlp'
    store.append('training_runs',key,result)
    (directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result
