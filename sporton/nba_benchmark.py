"""Retrospective three-season NBA comparison, separate from operational promotion.

Publication timestamps are unavailable: no live validation or verified training claim.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
import json
import math
from pathlib import Path
from uuid import uuid4
import joblib
import numpy as np
from .contracts import utcnow
from .storage import Store,home,digest
from .features import FEATURES
from sklearn.linear_model import LogisticRegression
from .models import fit_candidate,probabilities,score_predictions,metrics
from .nba_quality import final_result,regular,dt
from .dispatch import fetch


def dataset(results):
    by_day={}
    for g in results:by_day.setdefault(dt(g['date']).date(),[]).append(g)
    histories={};elo={};out=[]
    for day in sorted(by_day):
        pending=by_day[day]
        for g in sorted(pending,key=lambda g:g['date']):
            h,a=g['home_id'],g['away_id'];hs=histories.get(h,[])[-10:];ass=histories.get(a,[])[-10:]
            if min(len(hs),len(ass))<5:continue
            values={'possessions_known':0.}
            for prefix,tid,rows in [('home',h,hs),('away',a,ass)]:
                pf=np.mean([r['pf'] for r in rows]);pa=np.mean([r['pa'] for r in rows])
                values.update({prefix+'_for':float(pf),prefix+'_against':float(pa),prefix+'_net':float(pf-pa),prefix+'_rest':min(14.,(dt(g['date'])-dt(rows[-1]['date'])).total_seconds()/86400),
                    prefix+'_elo':elo.get(tid,1500.),prefix+'_pace':0.,prefix+'_rtg':0.})
            simple=[(values['home_for']+values['away_against'])/2,(values['away_for']+values['home_against'])/2]
            out.append({'game_id':g['id'],'date':g['date'],'x':[values[k] for k in FEATURES],'y':int(g['home_score']>g['away_score']),
                'scores':[g['home_score'],g['away_score']],'baseline_scores':simple,'source_prior_ids':[r['id'] for r in hs+ass]})
        # All games on a UTC date are applied only after that date's feature rows are built.
        for g in pending:
            h,a=g['home_id'],g['away_id'];eh,ea=elo.get(h,1500.),elo.get(a,1500.)
            delta=20*(int(g['home_score']>g['away_score'])-1/(1+10**((ea-eh-60)/400)))
            elo[h]=eh+delta;elo[a]=ea-delta
            for tid,pf,pa in [(h,g['home_score'],g['away_score']),(a,g['away_score'],g['home_score'])]:
                histories.setdefault(tid,[]).append({'id':g['id'],'date':g['date'],'pf':pf,'pa':pa})
    return out


def compare(rows,kinds=('elo','logistic','lightgbm')):
    days=sorted({r['date'][:10] for r in rows})
    if len(rows)<200 or len(days)<60:raise ValueError('insufficient chronological research samples')
    # Two expanding folds; each has its own fit/calibration/test boundaries.
    folds=[]
    for fit_fraction,end_fraction in ((.55,.75),(.75,1.)):
        fit_end=days[int(len(days)*fit_fraction)];cal_end=days[int(len(days)*(fit_fraction+.08))];end=days[min(len(days)-1,int(len(days)*end_fraction))]
        fit=[r for r in rows if r['date'][:10]<fit_end]
        cal=[r for r in rows if fit_end<=r['date'][:10]<cal_end]
        test=[r for r in rows if cal_end<=r['date'][:10]<=end]
        X=np.array([r['x'] for r in test]);actual=[r['scores'] for r in test];y=[r['y'] for r in test]
        base=np.array([r['baseline_scores'] for r in test])
        cal_margins=np.array([r['baseline_scores'][0]-r['baseline_scores'][1] for r in cal]).reshape(-1,1)
        calibrator=LogisticRegression(C=1.,random_state=17).fit(cal_margins,[r['y'] for r in cal])
        p=calibrator.predict_proba((base[:,0]-base[:,1]).reshape(-1,1))[:,1]
        result={'recent_average':metrics(y,p,actual,base)}
        for kind in kinds:
            artifact=fit_candidate(kind,fit,cal);result[kind]=metrics(y,probabilities(artifact,X),actual,score_predictions(artifact,X))
        folds.append({'fit_end_exclusive':fit_end,'calibration_end_exclusive':cal_end,'test_end_inclusive':end,'test_game_ids':[r['game_id'] for r in test],
            'n_fit':len(fit),'n_calibration':len(cal),'n_test':len(test),'metrics':result})
    return folds


def run(store,seasons=(2024,2025,2026),deep_learning=False):
    results={};failures=[]
    def source(item):
        season,team=item;url=f'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team}/schedule?season={season}&seasontype=2'
        try:
            cached=store.latest_source(url)
            body=cached['payload']['body'] if cached and cached['payload'].get('collected_at') and utcnow()-dt(cached['payload']['collected_at'])<timedelta(hours=24) else fetch(store,url)
            games=[]
            for e in body['events']:
                if regular(e):
                    try:r=final_result(e)
                    except (ValueError,KeyError,TypeError):continue
                    if r:games.append(r)
            return games,None
        except ValueError:return [],url
    with ThreadPoolExecutor(max_workers=8) as pool:
        for games,error in pool.map(source,[(s,t) for s in seasons for t in range(1,31)]):
            if error:failures.append(error)
            for r in games:
                if r['id'] in results and any(results[r['id']][k]!=r[k] for k in ('home_score','away_score','date')):raise ValueError('historical source score conflict')
                results[r['id']]=r
    rows=dataset(list(results.values()));kinds=['elo','logistic','lightgbm'];torch_status='NOT_REQUESTED'
    if deep_learning:
        import importlib.util
        if importlib.util.find_spec('torch'):kinds.append('pytorch_mlp');torch_status='COMPARED'
        else:torch_status='DEPENDENCY_UNAVAILABLE'
    key='nba-research:'+digest({'rows':rows,'kinds':kinds,'version':'walk-forward-v2-calibrated-average'})
    existing=store.get('training_runs',key)
    if existing:return existing
    folds=compare(rows,tuple(kinds))
    result={'id':key,'kind':'NBA_RETROSPECTIVE_COMPARISON','created_at':utcnow().isoformat(),'seasons':list(seasons),'source_games':len(results),'usable_samples':len(rows),
        'source_failures':failures,'torch_status':torch_status,'folds':folds,'operational_promotion':False,
        'limitations':['Historical publication times unknown; hindsight-source research, not point-in-time live validation.',
            'Possession/rtg unavailable in schedule data: feature zeros are explicit imputation with possessions_known=0, not observed statistics.',
            'All probability candidates use the same isolated calibration block; retrospective calibration is not live certification.','No model promoted solely because of complexity or training set size.']}
    store.append('training_runs',key,result)
    directory=home()/'research';directory.mkdir(exist_ok=True);(directory/'nba-walk-forward.json').write_text(json.dumps(result,indent=2))
    return result


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--deep-learning',action='store_true');args=p.parse_args()
    result=run(Store(),deep_learning=args.deep_learning)
    print(json.dumps({k:result[k] for k in ('id','source_games','usable_samples','torch_status','operational_promotion')},indent=2))
if __name__=='__main__':main()
