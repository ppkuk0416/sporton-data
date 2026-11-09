from datetime import datetime, timedelta
from uuid import uuid4
import numpy as np
from .contracts import Game, Fixture, utcnow
from .features import FEATURES, snapshot
from .models import load_model, probabilities, score_predictions, champion, train, promote
from .review import NBACriticalAnalyst
from .storage import digest


class NBAAnalyst:
    league='NBA'
    def draft(self,fixture,snap,artifact,meta,evidence_id):
        X=np.array([[snap['values'][k] for k in FEATURES]])
        scores=score_predictions(artifact,X)[0].tolist()
        p=float(probabilities(artifact,X)[0])
        return {'id':str(uuid4()),'created_at':utcnow().isoformat(),'fixture':fixture.model_dump(mode='json'),
                'snapshot':snap,'model_id':meta['id'],'code_hash':meta['code_hash'],
                'training_hash':meta['training_hash'],'evidence_id':evidence_id,
                'home_win_probability':p,'probability_status':meta['probability_status'],
                'scores':scores,'margin':scores[0]-scores[1],'total':sum(scores),
                'score_intervals':[[max(0,s-r),s+r] for s,r in zip(scores,artifact['interval_radius'])],
                'scope':'including_overtime','quarter_predictions':None,'half_predictions':None,
                'limitations':['Research model; insufficient live validation for official recommendation.',
                    'Verified injuries, lineups, player usage, and travel data not connected.',
                    'Quarter/half/regulation-only heads not trained; no invented splits.',
                    'Point intervals depend on calibration residual stationarity.'],
                'analyst':'nba-team-form-v1'}


# Other sports intentionally have no NBA model fallback.
ANALYSTS={'NBA':NBAAnalyst()}


def forecast(store,fixture_payload,model_id=None,cutoff=None,market_id=None,test_mode=False):
    if fixture_payload.get('league','NBA')!='NBA':
        from .sports.lifecycle import forecast_sport
        return forecast_sport(store,fixture_payload,model_id,cutoff,market_id,test_mode)
    fixture=Fixture.model_validate(fixture_payload)
    now=utcnow(); cutoff=cutoff or now
    if cutoff>now or cutoff>=fixture.starts_at or now>=fixture.starts_at:
        raise ValueError('pre-game forecast requires cutoff <= now < tipoff')
    if store.get('official_recommendations', fixture.game_id):
        raise ValueError('duplicate publication for fixture')
    attempts=sum(r['payload']['fixture']['game_id']==fixture.game_id for r in store.rows('analyst_reports'))
    if attempts>=3:
        raise ValueError('maximum three review attempts exceeded; publication withheld')
    model_id=model_id or champion(store)
    if not model_id:
        raise ValueError('no champion; train and select a research model explicitly')
    artifact,meta=load_model(store,model_id)
    if (meta['data_mode']=='TEST/FIXTURE')!=test_mode:
        raise ValueError('model data mode mismatch')
    if datetime.fromisoformat(meta['trained_at'])>cutoff or datetime.fromisoformat(meta['selection_end'])>cutoff:
        raise ValueError('model unavailable at cutoff')
    if meta['league']!=fixture.league:
        raise ValueError('league-specific model required')
    if store.get('actual_results',fixture.game_id):
        raise ValueError('cannot forecast a completed game')
    store.append('fixtures',fixture.game_id,fixture.model_dump(mode='json'),idempotent=True)
    from .governance import games_as_of
    history=games_as_of(store,'NBA',cutoff)
    snap=snapshot(history,fixture,cutoff,test_mode)
    market=store.get('market_snapshots',market_id) if market_id else None
    if market_id and market is None:
        raise ValueError('unknown market snapshot')
    if market and ((market['quality']=='TEST/FIXTURE')!=test_mode):
        raise ValueError('market data mode mismatch')
    critic=NBACriticalAnalyst()
    evidence_id,_=critic.blind_assessment(store,fixture,cutoff,test_mode)
    report=ANALYSTS[fixture.league].draft(fixture,snap,artifact,meta,evidence_id)
    report['market_id']=market_id
    store.append('feature_snapshots',report['id'],snap)
    store.append('model_predictions',report['id'],report)
    store.append('analyst_reports',report['id'],report)
    review_id,review=critic.review(store,report,evidence_id,meta,market)
    # Default fail-closed. Actual audited post-selection performance is required for prices.
    state='NO BET'; ev=None
    from .analytics import resolved_evaluations
    live=[r['payload'] for r in resolved_evaluations(store)
          if r['payload']['model_id']==model_id and r['payload']['data_mode']=='real']
    from .governance import availability_verified
    eligible=(availability_verified(store,report) and len(live)>=100 and np.mean([r['brier'] for r in live])<.25
              and np.mean([r['log_loss'] for r in live])<.693147 and not drift(store,model_id)['warning'])
    if review['verdict']=='PASS':
        state='WATCH'
        if eligible and market and market['market_type']=='moneyline' and market['quality']=='verified' and not test_mode and champion(store)==model_id:
            p=report['home_win_probability'] if market['selection']=='home' else 1-report['home_win_probability']
            ev=p*market['decimal_odds']-1
            if ev>=.05:
                state='RECOMMEND'
    decision={'prediction_id':report['id'],'review_id':review_id,'fixture_id':fixture.game_id,
              'review':review['verdict'],'state':state,'expected_value':ev,'market_id':market_id,
              'at':utcnow().isoformat(),'data_mode':meta['data_mode'],
              'note':'Accuracy does not guarantee a price advantage or profit.'}
    if review['verdict']=='PASS':
        publish(store,report['id'],review_id,decision)
    return {'report':report,'review':review,'decision':decision}


def publish(store,prediction_id,review_id,decision):
    report=store.get('model_predictions',prediction_id)
    review=store.get('critical_reviews',review_id)
    if not report or not review or review['prediction_id']!=prediction_id or review['verdict']!='PASS' or review['report_hash']!=digest(report):
        raise ValueError('publication requires matching independent PASS')
    if utcnow()>=datetime.fromisoformat(report['fixture']['starts_at']):
        raise ValueError('publication after tipoff forbidden')
    # This MVP publishes once per game. Rejected drafts remain immutable and visible.
    if decision['prediction_id']!=prediction_id or decision['review_id']!=review_id:
        raise ValueError('decision linkage mismatch')
    if decision['review']!='PASS' or decision['state'] not in ('WATCH','RECOMMEND','NO BET'):raise ValueError('invalid publication decision')
    if decision['state']=='RECOMMEND':
        from .governance import availability_verified
        if not availability_verified(store,report):raise ValueError('confirmed lineup/player availability missing')
        market=store.get('market_snapshots',decision.get('market_id'))
        from .analytics import resolved_evaluations
        live=[r['payload'] for r in resolved_evaluations(store) if r['payload']['model_id']==report['model_id'] and r['payload']['data_mode']=='real']
        if decision['data_mode']!='real' or not market or market['quality']!='verified' or market['market_type']!='moneyline' or len(live)<100 or champion(store)!=report['model_id'] or drift(store,report['model_id'])['warning']:
            raise ValueError('official priced selection gate failed')
        if np.mean([r['brier'] for r in live])>=.25 or np.mean([r['log_loss'] for r in live])>=.693147:raise ValueError('live performance gate failed')
        p=report['home_win_probability'] if market['selection']=='home' else 1-report['home_win_probability']
        if p*market['decimal_odds']-1<.05:raise ValueError('price edge gate failed')
        if report['margin']*(1 if market['selection']=='home' else -1)<=0:raise ValueError('score direction conflicts with recommendation')
    from .governance import register_report
    with store.transaction() as conn:
        register_report(store,'PRE_GAME',report['fixture']['game_id'],prediction_id,review_id,data_mode=decision['data_mode'],conn=conn)
        store.append('official_recommendations',report['fixture']['game_id'],decision,conn=conn)
        selection_class='OFFICIAL_PRICED' if decision['state']=='RECOMMEND' else 'CONDITIONAL_WATCH' if decision.get('market_id') else 'DIRECTION_ONLY' if decision['state']=='WATCH' else 'PASS'
        store.append('market_selections',prediction_id,{**decision,'selection_class':selection_class},conn=conn)


def audit(store):
    added=[]
    for row in store.rows('official_recommendations'):
        decision=row['payload']; prediction_id=decision['prediction_id']
        if store.get('prediction_evaluations',prediction_id):
            continue
        report=store.get('model_predictions',prediction_id)
        result=store.get('actual_results',report['fixture']['game_id'])
        if not result:
            continue
        if report['fixture']['league']!='NBA':
            from .sports.lifecycle import audit_sport_prediction
            evaluation=audit_sport_prediction(store,decision,report,result,utcnow())
            if evaluation:
                from .governance import register_report
                register_report(store,'AUDIT',report['fixture']['game_id'],prediction_id,content=evaluation,data_mode=decision['data_mode'])
                added.append(evaluation)
            continue
        game=Game.model_validate(result)
        if (game.quality=='TEST/FIXTURE')!=(decision['data_mode']=='TEST/FIXTURE') or game.quality=='unverified':
            raise ValueError('audit result data mode/quality mismatch')
        if any(result[k]!=report['fixture'][k] for k in ('game_id','starts_at','home_team','away_team','league')):
            raise ValueError('audit fixture mismatch')
        if game.ends_at>utcnow() or game.published_at>utcnow():
            continue
        y=int(game.home_score>game.away_score); p=report['home_win_probability']
        actual=np.array([game.home_score,game.away_score]); scores=np.array(report['scores'])
        evaluation={'prediction_id':prediction_id,'league':'NBA','model_id':report['model_id'],'data_mode':decision['data_mode'],
                    'at':utcnow().isoformat(),'result_source':game.source,'period':game.starts_at.isoformat(),
                    'correct':int((p>=.5)==y),'brier':float((p-y)**2),
                    'log_loss':float(-(y*np.log(max(p,1e-12))+(1-y)*np.log(max(1-p,1e-12)))),
                    'score_mae':float(np.abs(actual-scores).mean()),'margin_error':float((scores[0]-scores[1])-(actual[0]-actual[1])),
                    'total_error':float(scores.sum()-actual.sum()),'market_settlement':None}
        if decision.get('market_id'):
            market=store.get('market_snapshots',decision['market_id'])
            if market['scope']=='including_overtime':
                if market['market_type']=='moneyline':
                    value=(actual[0]-actual[1])*(1 if market['selection']=='home' else -1)
                elif market['market_type']=='spread':
                    value=(actual[0]-actual[1])*(1 if market['selection']=='home' else -1)+market['line']
                else:
                    value=(actual.sum()-market['line'])*(1 if market['selection']=='over' else -1)
                net=0. if value==0 else market['decimal_odds']-1 if value>0 else -1.
                evaluation['market_settlement']={'outcome':'push' if value==0 else 'win' if value>0 else 'loss',
                    'unit_profit':float(net),'official_bet':decision['state']=='RECOMMEND',
                    'break_even_probability':1/market['decimal_odds']}
        from .governance import register_report
        with store.transaction() as conn:
            store.append('prediction_evaluations',prediction_id,evaluation,conn=conn)
            register_report(store,'AUDIT',report['fixture']['game_id'],prediction_id,content=evaluation,data_mode=decision['data_mode'],conn=conn)
        if not evaluation['correct'] or abs(evaluation['margin_error'])>15:
            store.append('error_memory',prediction_id,{'prediction_id':prediction_id,'hypothesis':'Unmodeled lineup, schedule, or shooting variance may contribute.',
                'status':'unverified_hypothesis','usable_as_training_label':False,'margin_error':evaluation['margin_error'],
                'at':utcnow().isoformat()})
        added.append(evaluation)
    from .analytics import audit_result_corrections
    added.extend(audit_result_corrections(store))
    return added


def drift(store,model_id=None):
    model_id=model_id or champion(store)
    from .analytics import resolved_evaluations
    rows=[r['payload'] for r in resolved_evaluations(store) if r['payload']['model_id']==model_id]
    rows=sorted(rows,key=lambda x:x['period'])
    if len(rows)<30 or not model_id:
        return {'warning':False,'status':'insufficient_data','n':len(rows)}
    meta=store.get('model_versions',model_id)
    current=float(np.mean([r['brier'] for r in rows[-30:]]))
    return {'warning':current>meta['validation']['brier']+.05,'recent_brier':current,'n':30,'threshold':meta['validation']['brier']+.05}


def daily(store,test_mode=False,retrain=False,league='NBA',deep_learning=False):
    audited=audit(store); current=champion(store,league)
    result={'audited':len(audited),'league':league,'drift':drift(store,current) if current else {'warning':False,'status':'insufficient_data','n':0},'training':None,'promotion':None}
    # Opt-in maintenance, fixed gate: >=50 new games since the previous training selection.
    versions=[r for r in store.rows('model_versions') if r['payload']['league']==league]
    since=datetime.fromisoformat(versions[-1]['payload']['selection_end']) if versions else datetime.min.replace(tzinfo=utcnow().tzinfo)
    count=sum(datetime.fromisoformat(r['payload']['published_at'])>since for r in store.rows('historical_game_results') if r['payload']['league']==league and r['payload']['quality']==('TEST/FIXTURE' if test_mode else 'verified'))
    result['new_game_count']=count
    if retrain and count>=50:
        if league=='NBA':candidate=train(store,test_mode,deep_learning)
        else:
            from .sports.lifecycle import train_sport
            candidate=train_sport(store,league,test_mode,deep_learning)
        result['training']=candidate['id']
        if not test_mode:
            try:
                result['promotion']=promote(store,candidate['id'])
            except ValueError as exc:
                result['promotion_blocked']=str(exc)
    store.append('training_runs',str(uuid4()),{'maintenance':result,'at':utcnow().isoformat()})
    return result
