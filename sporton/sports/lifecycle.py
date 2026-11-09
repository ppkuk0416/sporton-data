"""Shared audit/lifecycle mechanics; sport-specific statistics remain in each plugin."""
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
import hashlib
import math
import joblib
import numpy as np
from sklearn.metrics import log_loss
from ..contracts import utcnow
from ..storage import home,digest
from ..models import chronological_blocks,load_model,champion
from . import plugin
from .contracts import parse_fixture,parse_game
from .common import rows_for,available


def evaluate(model,rows,adapter):
    X=np.array([r['x'] for r in rows]);p=model.probabilities(X);scores=model.scores(X)
    y=np.array([r['y'] for r in rows]);actual=np.array([r['scores'] for r in rows])
    n=len(y);loss=.5*np.sum((p-np.eye(len(adapter.outcomes))[y])**2,axis=1)
    correct=(p.argmax(axis=1)==y).astype(float)
    score_error=np.abs(actual-scores).mean(axis=1);margin_error=np.abs((actual[:,0]-actual[:,1])-(scores[:,0]-scores[:,1]))
    total_error=np.abs(actual.sum(axis=1)-scores.sum(axis=1));log_error=-np.log(np.maximum(p[np.arange(n),y],1e-12))
    result={'n':n,'accuracy':float(correct.mean()),'brier':float(loss.mean()),'log_loss':float(log_error.mean()),
        'score_mae':float(score_error.mean()),'margin_mae':float(margin_error.mean()),'total_mae':float(total_error.mean()),
        'score_rmse':float(np.sqrt(np.mean((actual-scores)**2))),
        'score_interval_coverage':float(np.mean(np.abs(actual-scores)<=model.radius)),
        'period':[rows[0]['game'].starts_at.isoformat(),rows[-1]['game'].starts_at.isoformat()],
        'sources':sorted({r['game'].source for r in rows}),'outcomes':adapter.outcomes,
        'brier_definition':'half sum squared class errors; binary equivalent to home-probability Brier'}
    rng=np.random.default_rng(841);indices=rng.integers(0,n,size=(400,n))
    for name,values in [('accuracy',correct),('brier',loss),('log_loss',log_error),('score_mae',score_error),('margin_mae',margin_error),('total_mae',total_error)]:
        result[name+'_95ci']=np.quantile(values[indices].mean(axis=1),[.025,.975]).tolist()
    result['calibration']={label:[{'n':int(mask.sum()),'predicted':float(p[mask,k].mean()),'observed':float((y[mask]==k).mean())}
        for lo in np.arange(0,1,.2) if (mask:=(p[:,k]>=lo)&(p[:,k]<lo+.2 if lo<.8 else p[:,k]<=1)).any()]
        for k,label in enumerate(adapter.outcomes)}
    return result


def require_outcomes(rows,adapter):
    if set(r['y'] for r in rows)!=set(range(len(adapter.outcomes))):
        raise ValueError(f'all outcomes {adapter.outcomes} required in fit and calibration; no omitted draw class')


def fit_sport_candidate(adapter,kind,fit,cal):
    if kind=='pytorch_mlp':
        from ..deep_learning import TorchMultitask
        return TorchMultitask().fit(fit,cal,adapter.features,adapter.outcomes)
    return adapter.fit(kind,fit,cal)


def train_sport(store,league,test_mode=False,deep_learning=False):
    adapter=plugin(league);rows=rows_for(store,adapter,test_mode)
    if len(rows)<100:raise ValueError(f'{league}: need >=100 usable games, found {len(rows)}')
    fit,cal,val,test=chronological_blocks(rows)
    require_outcomes(fit,adapter);require_outcomes(cal,adapter)
    test_hash=digest({'league':league,'games':[r['game'].model_dump(mode='json') for r in test]})
    if any(r['payload'].get('test_hash')==test_hash for r in store.rows('training_runs')):
        raise ValueError('test window already evaluated; new future holdout required')
    candidates=[]
    for kind in (*adapter.kinds,'pytorch_mlp') if deep_learning else adapter.kinds:
        model=fit_sport_candidate(adapter,kind,fit,cal);metrics=evaluate(model,val,adapter)
        candidates.append((metrics['brier'],kind,model,metrics))
    _,kind,model,validation=min(candidates,key=lambda x:x[0])
    now=utcnow().isoformat();model_id=str(uuid4())
    training_hash=digest([{'game':r['game'].model_dump(mode='json'),'features':r['snapshot']} for r in fit+cal])
    source_root=Path(__file__).parents[1]
    code_hash=hashlib.sha256(b''.join(p.read_bytes() for p in sorted(source_root.rglob('*.py')))).hexdigest()
    meta={'id':model_id,'league':league,'sport':adapter.sport,'kind':kind,'created_at':now,'trained_at':now,
        'feature_version':adapter.version,'features':adapter.features,'outcomes':adapter.outcomes,'scope':adapter.scope,
        'code_hash':code_hash,'training_hash':training_hash,'test_hash':test_hash,
        'training_period':[fit[0]['game'].starts_at.isoformat(),cal[-1]['game'].starts_at.isoformat()],
        'trained_through':max(r['game'].published_at for r in fit+cal).isoformat(),
        'selection_end':max(r['game'].published_at for r in val).isoformat(),'validation_start':val[0]['game'].starts_at.isoformat(),
        'validation_game_ids':[r['game'].game_id for r in val],'calibration_n':len(cal),
        'validation':validation,'test':evaluate(model,test,adapter),'candidate_validation':{k:float(b) for b,k,_,_ in candidates},
        'deep_learning_compared':deep_learning,'data_mode':'TEST/FIXTURE' if test_mode else 'real','probability_status':'calibrated_research_only','operational_validation':False}
    directory=home()/'models'/league;directory.mkdir(parents=True,exist_ok=True)
    path=directory/f'{model_id}.joblib';joblib.dump({'sport_model':model,'metadata':meta},path)
    meta['artifact_path']=str(path.resolve());meta['artifact_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    store.append('model_versions',model_id,meta)
    store.append('training_runs',str(uuid4()),{'league':league,'model_id':model_id,'at':now,'test_hash':test_hash,
        'splits':dict(zip(('fit','calibration','validation','test'),map(len,(fit,cal,val,test)))),
        'validation':meta['validation'],'test':meta['test']})
    return meta


def backtest_sport(store,league,test_mode=False,deep_learning=False):
    adapter=plugin(league);rows=rows_for(store,adapter,test_mode)
    if len(rows)<150:raise ValueError('walk-forward needs >=150 usable games')
    dates=sorted({r['game'].starts_at.date() for r in rows});folds=[]
    for a,b in ((.55,.70),(.70,.85),(.85,1.)):
        start=dates[int(len(dates)*a)];end=dates[int(len(dates)*b)] if b<1 else None
        future=[r for r in rows if r['game'].starts_at.date()>=start and (end is None or r['game'].starts_at.date()<end)]
        cutoff=datetime.fromisoformat(future[0]['snapshot']['feature_cutoff_timestamp'])
        past=[r for r in rows if r['game'].starts_at.date()<start and r['game'].published_at<=cutoff]
        past_dates=sorted({r['game'].starts_at.date() for r in past});cal_start=past_dates[int(len(past_dates)*.8)]
        cal=[r for r in past if r['game'].starts_at.date()>=cal_start]
        fit_cutoff=datetime.fromisoformat(cal[0]['snapshot']['feature_cutoff_timestamp'])
        fit=[r for r in past if r['game'].starts_at.date()<cal_start and r['game'].published_at<=fit_cutoff]
        require_outcomes(fit,adapter);require_outcomes(cal,adapter)
        folds.append({'fit_n':len(fit),'calibration_n':len(cal),
            'models':{k:evaluate(fit_sport_candidate(adapter,k,fit,cal),future,adapter) for k in ((*adapter.kinds,'pytorch_mlp') if deep_learning else adapter.kinds)}})
    result={'league':league,'sport':adapter.sport,'data_mode':'TEST/FIXTURE' if test_mode else 'real',
        'purpose':'development backtest, not a sealed promotion test','folds':folds}
    store.append('training_runs',str(uuid4()),{'league':league,'backtest':result,'at':utcnow().isoformat()})
    return result


class IndependentSportReviewer:
    def __init__(self,adapter):self.adapter=adapter
    def blind(self,store,fixture,cutoff,test_mode):
        history=available(store,fixture.league,cutoff,test_mode)
        evidence=self.adapter.review_evidence(history,fixture)
        if self.adapter.sport=='baseball':
            from .baseball import context_as_of
            evidence['pregame_context']={team:c.model_dump(mode='json') for team,c in context_as_of(store,fixture,cutoff,test_mode).items()}
        evidence.update({'fixture_id':fixture.game_id,'league':fixture.league,'sport':self.adapter.sport,
            'cutoff':cutoff.isoformat(),'created_at':utcnow().isoformat(),'reviewer':self.adapter.sport+'-independent-rules-v1'})
        key=str(uuid4());store.append('review_evidence',key,evidence)
        return key,evidence
    def review(self,store,report,evidence_id,meta,market):
        evidence=store.get('review_evidence',evidence_id);reasons=[];counter=[]
        if not evidence or evidence['created_at']>report['created_at'] or evidence['fixture_id']!=report['fixture']['game_id']:
            reasons.append('blind evidence missing or created after draft')
        if evidence:
            local,opposing=self.adapter.review_checks(report,evidence);reasons+=local;counter+=opposing
            if min(evidence['home']['n'],evidence['away']['n'])<3:reasons.append('insufficient independent history')
        probs=report['outcome_probabilities']
        if set(probs)!=set(self.adapter.outcomes) or any(not math.isfinite(x) or not 0<x<1 for x in probs.values()) or abs(sum(probs.values())-1)>1e-6:
            reasons.append('invalid outcome probability distribution')
        cutoff=datetime.fromisoformat(report['snapshot']['feature_cutoff_timestamp'])
        if utcnow()>=datetime.fromisoformat(report['fixture']['starts_at']) or cutoff>utcnow():reasons.append('pre-game time invalid')
        if datetime.fromisoformat(meta['trained_at'])>cutoff or datetime.fromisoformat(meta['selection_end'])>cutoff:reasons.append('model unavailable at cutoff')
        if any(not math.isfinite(v) or v<0 for v in report['scores']):reasons.append('invalid score expectation')
        if abs(report['margin']-(report['scores'][0]-report['scores'][1]))>1e-8:reasons.append('inconsistent margin')
        if market:
            if market['game_id']!=report['fixture']['game_id'] or market['scope']!=self.adapter.scope:reasons.append('market fixture/scope mismatch')
            collected=datetime.fromisoformat(market['collected_at'])
            if collected>cutoff or (cutoff-collected).total_seconds()>900:reasons.append('market not timely at cutoff')
            if market['quality'] not in ('verified','TEST/FIXTURE'):reasons.append('unverified market')
            if market['market_type'] in ('moneyline','three_way'):
                expected='three_way' if 'draw' in self.adapter.outcomes else 'moneyline'
                if market['market_type']!=expected or market['selection'] not in self.adapter.outcomes:reasons.append('market outcome rules mismatch; draw cannot be ignored')
        review={'prediction_id':report['id'],'evidence_id':evidence_id,'report_hash':digest(report),'league':meta['league'],
            'verdict':'NO' if reasons else 'PASS','reasons':reasons or ['Sport-specific scope, time lineage and probability classes verified.'],
            'counter_evidence':counter,'at':utcnow().isoformat(),'reviewer':self.adapter.sport+'-independent-rules-v1',
            'meaning':'Publication approval, not verified outcome accuracy.'}
        key=str(uuid4());store.append('critical_reviews',key,review)
        return key,review


def forecast_sport(store,payload,model_id=None,cutoff=None,market_id=None,test_mode=False):
    from ..engine import publish
    fixture=parse_fixture(payload);adapter=plugin(fixture.league);now=utcnow();cutoff=cutoff or now
    if cutoff>now or now>=fixture.starts_at or cutoff>=fixture.starts_at:raise ValueError('pre-game cutoff <= now < start required')
    if store.get('official_recommendations',fixture.game_id):raise ValueError('duplicate publication for fixture')
    attempts=sum(r['payload']['fixture']['game_id']==fixture.game_id for r in store.rows('analyst_reports'))
    if attempts>=3:raise ValueError('maximum three review attempts exceeded')
    model_id=model_id or champion(store,fixture.league)
    if not model_id:raise ValueError('no league champion; select a trained research model explicitly')
    artifact,meta=load_model(store,model_id)
    if meta['league']!=fixture.league:raise ValueError('league-specific model required; no cross-sport fallback')
    if meta['feature_version']!=adapter.version or meta['features']!=adapter.features:raise ValueError('feature version mismatch')
    if (meta['data_mode']=='TEST/FIXTURE')!=test_mode:raise ValueError('model data mode mismatch')
    if datetime.fromisoformat(meta['trained_at'])>cutoff or datetime.fromisoformat(meta['selection_end'])>cutoff:raise ValueError('model unavailable at cutoff')
    if store.get('actual_results',fixture.game_id):raise ValueError('completed game cannot be forecast')
    store.append('fixtures',fixture.game_id,fixture.model_dump(mode='json'),idempotent=True)
    snap=adapter.snapshot(store,fixture,cutoff,test_mode)
    market=store.get('market_snapshots',market_id) if market_id else None
    if market_id and market is None:raise ValueError('unknown market snapshot')
    if market and ((market['quality']=='TEST/FIXTURE')!=test_mode):raise ValueError('market data mode mismatch')
    critic=IndependentSportReviewer(adapter);evidence_id,evidence=critic.blind(store,fixture,cutoff,test_mode)
    model=artifact['sport_model'];X=np.array([[snap['values'][k] for k in adapter.features]])
    scores=model.scores(X)[0].tolist();p=model.probabilities(X)[0]
    report={'id':str(uuid4()),'created_at':utcnow().isoformat(),'fixture':fixture.model_dump(mode='json'),'sport':adapter.sport,
        'snapshot':snap,'model_id':model_id,'code_hash':meta['code_hash'],'training_hash':meta['training_hash'],
        'evidence_id':evidence_id,'market_id':market_id,'scope':adapter.scope,'analyst':adapter.analyst.name,
        'outcome_probabilities':dict(zip(adapter.outcomes,map(float,p))),'home_win_probability':float(p[adapter.outcomes.index('home')]),
        'probability_status':meta['probability_status'],'scores':scores,'margin':scores[0]-scores[1],'total':sum(scores),
        'score_intervals':[[max(0,s-r),s+r] for s,r in zip(scores,map(float,model.radius))],
        'reasoning':adapter.analyst.explain(snap),'limitations':adapter.limitations,
        'segment_predictions':None}
    store.append('feature_snapshots',report['id'],snap);store.append('model_predictions',report['id'],report);store.append('analyst_reports',report['id'],report)
    review_id,review=critic.review(store,report,evidence_id,meta,market)
    # New sports begin in research WATCH. No price EV before a separately validated live policy.
    decision={'prediction_id':report['id'],'review_id':review_id,'fixture_id':fixture.game_id,'league':fixture.league,
        'review':review['verdict'],'state':'WATCH' if review['verdict']=='PASS' else 'NO BET','expected_value':None,
        'market_id':market_id,'at':utcnow().isoformat(),'data_mode':meta['data_mode'],
        'note':'Research publication only. No live calibration/price advantage claimed.'}
    if review['verdict']=='PASS':publish(store,report['id'],review_id,decision)
    return {'report':report,'review':review,'decision':decision}


def audit_sport_prediction(store,decision,report,result,now=None):
    now=now or utcnow();game=parse_game(result);adapter=plugin(game.league)
    if game.quality!=('TEST/FIXTURE' if decision['data_mode']=='TEST/FIXTURE' else 'verified'):raise ValueError('audit quality/mode mismatch')
    if any(result[k]!=report['fixture'][k] for k in ('game_id','league','starts_at','home_team','away_team')):raise ValueError('audit fixture mismatch')
    if game.ends_at>now or game.published_at>now:return None
    outcome='draw' if game.home_score==game.away_score else 'home' if game.home_score>game.away_score else 'away'
    actual=np.array([game.home_score,game.away_score]);scores=np.array(report['scores'])
    p=report['outcome_probabilities'];brier=.5*sum((prob-float(label==outcome))**2 for label,prob in p.items())
    evaluation={'prediction_id':report['id'],'model_id':report['model_id'],'league':game.league,'sport':adapter.sport,
        'data_mode':decision['data_mode'],'at':now.isoformat(),'result_source':game.source,'period':game.starts_at.isoformat(),
        'actual_outcome':outcome,'correct':int(max(p,key=p.get)==outcome),'brier':float(brier),
        'log_loss':float(-math.log(max(p[outcome],1e-12))),'score_mae':float(np.abs(actual-scores).mean()),
        'margin_error':float((scores[0]-scores[1])-(actual[0]-actual[1])),'total_error':float(scores.sum()-actual.sum()),'market_settlement':None}
    if decision.get('market_id'):
        m=store.get('market_snapshots',decision['market_id'])
        if m['scope']!=adapter.scope:raise ValueError('settlement scope mismatch')
        if m['market_type'] in ('moneyline','three_way'):
            if m['market_type']=='moneyline' and outcome=='draw':raise ValueError('two-way draw settlement requires explicit provider rules')
            value=1 if m['selection']==outcome else -1
        elif m['market_type']=='spread':value=(actual[0]-actual[1])*(1 if m['selection']=='home' else -1)+m['line']
        else:value=(actual.sum()-m['line'])*(1 if m['selection']=='over' else -1)
        net=0. if value==0 else m['decimal_odds']-1 if value>0 else -1.
        evaluation['market_settlement']={'outcome':'push' if value==0 else 'win' if value>0 else 'loss','unit_profit':float(net),
            'official_bet':False,'break_even_probability':1/m['decimal_odds']}
    store.append('prediction_evaluations',report['id'],evaluation)
    threshold=2 if adapter.sport=='football' else 4
    if not evaluation['correct'] or abs(evaluation['margin_error'])>threshold:
        store.append('error_memory',report['id'],{'prediction_id':report['id'],'league':game.league,'sport':adapter.sport,
            'hypothesis':'Unmodeled tactical/xG variation' if adapter.sport=='football' else 'Unverified starter/lineup or bullpen availability',
            'status':'unverified_hypothesis','usable_as_training_label':False,'at':now.isoformat()})
    return evaluation


def promote_sport(store,model_id):
    artifact,meta=load_model(store,model_id);adapter=plugin(meta['league'])
    if meta['data_mode']!='real':raise ValueError('TEST/FIXTURE models cannot become production champion')
    if min(meta['calibration_n'],meta['validation']['n'])<30:raise ValueError('30 calibration and validation games required')
    current=champion(store,meta['league']);evidence={'bootstrap':current is None}
    if current==model_id:raise ValueError('already champion')
    if current:
        old,oldmeta=load_model(store,current)
        if oldmeta['feature_version']!=meta['feature_version'] or oldmeta['features']!=meta['features']:
            raise ValueError('compatible feature versions required for paired comparison')
        if datetime.fromisoformat(oldmeta['selection_end'])>=datetime.fromisoformat(meta['validation_start']):raise ValueError('new future comparison window required')
        rows=[r for r in rows_for(store,adapter) if r['game'].game_id in meta['validation_game_ids']]
        if len(rows)!=meta['validation']['n']:raise ValueError('comparison evidence incomplete')
        before=evaluate(old['sport_model'],rows,adapter);after=evaluate(artifact['sport_model'],rows,adapter)
        if not(after['brier']<=before['brier']-.005 and after['log_loss']<=before['log_loss'] and after['score_mae']<=before['score_mae']):raise ValueError('challenger failed improvement gate')
        evidence={'champion':before,'challenger':after}
    store.append('model_promotions',str(uuid4()),{'league':meta['league'],'from_model':current,'to_model':model_id,'at':utcnow().isoformat(),'action':'promote','evidence':evidence})
    return model_id
