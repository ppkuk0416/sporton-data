"""Chronological fit/calibration/selection/test; no test-based promotion."""
import hashlib
import json
import math
import subprocess
from datetime import datetime
from pathlib import Path
from uuid import uuid4
import joblib
import numpy as np
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, log_loss, mean_squared_error, mean_absolute_error
from .contracts import Game, utcnow
from .features import FEATURES, FEATURE_VERSION, dataset
from .storage import digest, home


def metrics(y, p, actual, scores):
    y,p,actual,scores = np.array(y),np.array(p),np.array(actual),np.array(scores)
    n=len(y); correct=int(np.sum((p>=.5)==y)); acc=correct/n
    z=1.96; denom=1+z*z/n
    center=(acc+z*z/(2*n))/denom
    radius=z*math.sqrt(acc*(1-acc)/n+z*z/(4*n*n))/denom
    brier=(p-y)**2
    rng=np.random.default_rng(841)
    boot=np.array([np.mean(brier[rng.integers(0,n,n)]) for _ in range(400)])
    calibration=[]
    for lo in np.arange(0,1,.2):
        mask=(p>=lo)&(p<lo+.2 if lo<.8 else p<=1)
        if mask.any():
            calibration.append({'n':int(mask.sum()),'predicted':float(p[mask].mean()),'observed':float(y[mask].mean())})
    return {'n':n,'accuracy':float(accuracy_score(y,p>=.5)),'accuracy_95ci':[center-radius,center+radius],
            'log_loss':float(log_loss(y,p,labels=[0,1])),'brier':float(brier.mean()),
            'brier_95ci':np.quantile(boot,[.025,.975]).tolist(),'calibration':calibration,
            'score_mae':float(mean_absolute_error(actual,scores)),
            'margin_mae':float(mean_absolute_error(actual[:,0]-actual[:,1],scores[:,0]-scores[:,1])),
            'total_mae':float(mean_absolute_error(actual.sum(axis=1),scores.sum(axis=1))),
            'score_rmse':float(np.sqrt(mean_squared_error(actual,scores)))}


def base_probability(artifact, X):
    if artifact['kind']=='elo':
        h,a=X[:,FEATURES.index('home_elo')],X[:,FEATURES.index('away_elo')]
        return 1/(1+10**((a-h-60)/400))
    return artifact['classifier'].predict_proba(X)[:,1]


def probabilities(artifact,X):
    if artifact['kind']=='pytorch_mlp':return artifact['torch_model'].probabilities(X)[:,1]
    p=np.clip(base_probability(artifact,X),1e-6,1-1e-6)
    logits=np.log(p/(1-p)).reshape(-1,1)
    return artifact['calibrator'].predict_proba(logits)[:,1]


def score_predictions(artifact,X):
    if artifact['kind']=='pytorch_mlp':return artifact['torch_model'].scores(X)
    return np.column_stack([m.predict(X) for m in artifact['regressors']])


def fit_candidate(kind,fit,cal):
    if kind=='pytorch_mlp':
        from .deep_learning import TorchMultitask
        model=TorchMultitask().fit(fit,cal,FEATURES,['away','home'])
        return {'kind':kind,'torch_model':model,'calibration_n':len(cal),'interval_radius':model.radius.tolist()}
    X=np.array([r['x'] for r in fit]); y=np.array([r['y'] for r in fit]); targets=np.array([r['scores'] for r in fit])
    if len(set(y))<2 or len({r['y'] for r in cal})<2:
        raise ValueError('both outcomes required in training and calibration')
    classifier=None
    if kind=='logistic':
        classifier=make_pipeline(StandardScaler(),LogisticRegression(C=1.,max_iter=1000,random_state=17)).fit(X,y)
    elif kind=='lightgbm':
        classifier=LGBMClassifier(n_estimators=80,num_leaves=7,min_child_samples=12,verbosity=-1,n_jobs=2,random_state=17).fit(X,y)
    regressors=[]
    for i in range(2):
        reg=(LGBMRegressor(n_estimators=80,num_leaves=7,min_child_samples=12,verbosity=-1,n_jobs=2,random_state=17)
             if kind=='lightgbm' else make_pipeline(StandardScaler(),Ridge(alpha=10.)))
        regressors.append(reg.fit(X,targets[:,i]))
    artifact={'kind':kind,'classifier':classifier,'regressors':regressors}
    CX=np.array([r['x'] for r in cal]); cy=np.array([r['y'] for r in cal])
    p=np.clip(base_probability(artifact,CX),1e-6,1-1e-6)
    calibrator=LogisticRegression(C=1.,random_state=17).fit(np.log(p/(1-p)).reshape(-1,1),cy)
    artifact['calibrator']=calibrator
    residual=np.abs(np.array([r['scores'] for r in cal])-score_predictions(artifact,CX))
    # Split-conformal marginal intervals; exchangeability may fail under drift.
    q=min(1.,math.ceil((len(cal)+1)*.9)/len(cal))
    artifact['interval_radius']=np.quantile(residual,q,axis=0,method='higher').tolist()
    artifact['calibration_n']=len(cal)
    return artifact


def evaluate(artifact,rows):
    X=np.array([r['x'] for r in rows]); actual=np.array([r['scores'] for r in rows]); scores=score_predictions(artifact,X)
    result=metrics([r['y'] for r in rows],probabilities(artifact,X),actual,scores)
    radius=np.array(artifact['interval_radius'])
    result['score_interval_coverage']=float(np.mean(np.abs(actual-scores)<=radius))
    result['period']=[rows[0]['game'].starts_at.isoformat(),rows[-1]['game'].starts_at.isoformat()]
    result['sources']=sorted({r['game'].source for r in rows})
    return result


def chronological_blocks(rows):
    # Split by unique calendar days; same-day games stay in one block.
    dates=sorted({r['game'].starts_at.date() for r in rows})
    if len(dates)<40:
        raise ValueError('need at least 40 game dates and 100 usable games')
    cuts=[dates[int(len(dates)*f)] for f in (.5,.65,.8)]
    groups=[[r for r in rows if (i==0 or r['game'].starts_at.date()>=cuts[i-1])
             and (i==3 or r['game'].starts_at.date()<cuts[i])] for i in range(4)]
    # Embargo labels not published before the next block's earliest feature cutoff.
    for i in range(3):
        cutoff=datetime.fromisoformat(groups[i+1][0]['snapshot']['feature_cutoff_timestamp'])
        groups[i]=[r for r in groups[i] if r['game'].published_at<=cutoff]
    if any(len(g)<10 for g in groups):
        raise ValueError('insufficient chronological fit/calibration/validation/test samples')
    return groups


def train(store,test_mode=False,deep_learning=False):
    from .governance import games_as_of
    games=games_as_of(store,'NBA')
    rows=dataset(games,test_mode,store)
    if len(rows)<100:
        raise ValueError(f'need >=100 usable games, found {len(rows)}')
    fit,cal,val,test=chronological_blocks(rows)
    training_hash=digest([{'game':r['game'].model_dump(mode='json'),'features':r['snapshot']} for r in fit+cal])
    selection_hash=digest([r['game'].model_dump(mode='json') for r in val])
    # Test identity persists to prevent repeated tuning against a supposedly sealed set.
    test_hash=digest([r['game'].model_dump(mode='json') for r in test])
    if any(r['payload'].get('test_hash')==test_hash for r in store.rows('training_runs')):
        raise ValueError('test window already evaluated; add a new future holdout rather than retuning on it')
    candidates=[]
    for kind in (('elo','logistic','lightgbm','pytorch_mlp') if deep_learning else ('elo','logistic','lightgbm')):
        artifact=fit_candidate(kind,fit,cal)
        candidates.append((evaluate(artifact,val)['brier'],kind,artifact))
    _,kind,artifact=min(candidates,key=lambda r:r[0])
    run_id=str(uuid4()); model_id=str(uuid4())
    now=utcnow().isoformat()
    code_files=sorted(Path(__file__).parent.glob('*.py'))
    code_hash=hashlib.sha256(b''.join(p.read_bytes() for p in code_files)).hexdigest()
    metadata={'id':model_id,'league':'NBA','kind':kind,'created_at':now,'trained_at':now,
              'feature_version':FEATURE_VERSION,'features':FEATURES,'code_hash':code_hash,
              'training_hash':training_hash,'selection_hash':selection_hash,'test_hash':test_hash,
              'training_period':[fit[0]['game'].starts_at.isoformat(),cal[-1]['game'].starts_at.isoformat()],
              'trained_through':max(r['game'].published_at for r in fit+cal).isoformat(),
              'validation_game_ids':[r['game'].game_id for r in val],
              'validation_start':val[0]['game'].starts_at.isoformat(),
              'selection_end':max(r['game'].published_at for r in val).isoformat(),
              'validation':evaluate(artifact,val),'test':evaluate(artifact,test),
              'candidate_validation':{k:float(b) for b,k,_ in candidates},
              'deep_learning_compared':deep_learning,'calibration_n':artifact['calibration_n'],'data_mode':'TEST/FIXTURE' if test_mode else 'real',
              'probability_status':'calibrated_research_only','operational_validation':False}
    artifact['metadata']=metadata
    directory=home()/'models'; directory.mkdir(exist_ok=True)
    path=directory/f'{model_id}.joblib'; joblib.dump(artifact,path)
    metadata['artifact_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    metadata['artifact_path']=str(path.resolve())
    store.append('model_versions',model_id,metadata)
    store.append('training_runs',run_id,{'model_id':model_id,'test_hash':test_hash,'at':now,
        'splits':{k:len(v) for k,v in zip(('fit','calibration','validation','test'),(fit,cal,val,test))},
        'train_hash':training_hash,'test_policy':'one evaluation per immutable holdout',
        'validation':metadata['validation'],'test':metadata['test']})
    return metadata


def load_model(store,model_id):
    meta=store.get('model_versions',model_id)
    if not meta:
        raise ValueError('model not found')
    path=Path(meta['artifact_path'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=meta['artifact_sha256']:
        raise ValueError('model checksum mismatch')
    # Only load artifacts produced by this trusted local instance, never user uploads.
    return joblib.load(path),meta


def walk_forward(store,test_mode=False,deep_learning=False):
    from .governance import games_as_of
    games=games_as_of(store,'NBA')
    rows=dataset(games,test_mode,store)
    if len(rows)<150:
        raise ValueError('walk-forward needs >=150 usable games')
    # Three expanding-window backtests with disjoint future scoring windows.
    dates=sorted({r['game'].starts_at.date() for r in rows})
    folds=[]
    for a,b in ((.55,.70),(.70,.85),(.85,1.)):
        start=dates[int(len(dates)*a)]; end=dates[int(len(dates)*b)] if b<1 else None
        future=[r for r in rows if r['game'].starts_at.date()>=start and (end is None or r['game'].starts_at.date()<end)]
        cutoff=datetime.fromisoformat(future[0]['snapshot']['feature_cutoff_timestamp'])
        past=[r for r in rows if r['game'].starts_at.date()<start and r['game'].published_at<=cutoff]
        cal_start=sorted({r['game'].starts_at.date() for r in past})[int(len({r['game'].starts_at.date() for r in past})*.8)]
        cal=[r for r in past if r['game'].starts_at.date()>=cal_start]
        cal_cutoff=datetime.fromisoformat(cal[0]['snapshot']['feature_cutoff_timestamp'])
        fit=[r for r in past if r['game'].starts_at.date()<cal_start and r['game'].published_at<=cal_cutoff]
        models={kind:evaluate(fit_candidate(kind,fit,cal),future) for kind in (('elo','logistic','lightgbm','pytorch_mlp') if deep_learning else ('elo','logistic','lightgbm'))}
        folds.append({'fit_n':len(fit),'calibration_n':len(cal),'models':models})
    result={'purpose':'development backtest; these folds are not a sealed promotion test','folds':folds,'data_mode':'TEST/FIXTURE' if test_mode else 'real'}
    store.append('training_runs',str(uuid4()),{'backtest':result,'at':utcnow().isoformat()})
    return result


def promotion_events(store,league='NBA'):
    return [r for r in store.rows('model_promotions') if r['payload'].get('league', (store.get('model_versions',r['payload']['to_model']) or {}).get('league','NBA'))==league]


def champion(store,league='NBA'):
    events=promotion_events(store,league)
    return events[-1]['payload']['to_model'] if events else None


def promote(store,model_id):
    artifact,meta=load_model(store,model_id)
    if meta['league']!='NBA':
        from .sports.lifecycle import promote_sport
        return promote_sport(store,model_id)
    if meta['data_mode']!='real':
        raise ValueError('TEST/FIXTURE models cannot become production champion')
    if meta['validation']['n']<30 or meta['calibration_n']<30:
        raise ValueError('at least 30 calibration and 30 validation games required')
    current=champion(store)
    if current==model_id:
        raise ValueError('already champion')
    evidence={'bootstrap':current is None}
    if current:
        old,oldmeta=load_model(store,current)
        if datetime.fromisoformat(oldmeta['selection_end'])>=datetime.fromisoformat(meta['validation_start']):
            raise ValueError('comparison must occur on a new future validation window')
        from .governance import games_as_of
        games=games_as_of(store,'NBA')
        rows=[r for r in dataset(games,store=store) if r['game'].game_id in meta['validation_game_ids']]
        if len(rows)!=meta['validation']['n']:
            raise ValueError('comparison evidence incomplete')
        old_metrics=evaluate(old,rows); new_metrics=evaluate(artifact,rows)
        # Predeclared gate: absolute Brier improvement >= .005, no log-loss/MAE regression.
        if not (new_metrics['brier']<=old_metrics['brier']-.005 and new_metrics['log_loss']<=old_metrics['log_loss'] and new_metrics['score_mae']<=old_metrics['score_mae']):
            raise ValueError('challenger failed predeclared improvement gate')
        evidence={'champion':old_metrics,'challenger':new_metrics}
    store.append('model_promotions',str(uuid4()),{'league':'NBA','from_model':current,'to_model':model_id,'at':utcnow().isoformat(),'action':'promote','evidence':evidence})
    return model_id


def rollback(store,league='NBA'):
    events=promotion_events(store,league)
    if not events or not events[-1]['payload']['from_model']:
        raise ValueError('no previous champion')
    latest=events[-1]['payload']
    load_model(store,latest['from_model'])
    store.append('model_promotions',str(uuid4()),{'league':league,'from_model':latest['to_model'],'to_model':latest['from_model'],'at':utcnow().isoformat(),'action':'rollback'})
    return latest['from_model']
