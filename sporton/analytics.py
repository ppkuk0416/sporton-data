"""Verified original predictions only; direction/watch results are separate from priced ROI."""
from collections import Counter,defaultdict
import math
from .storage import digest


def resolved_evaluations(store):
    result={r['id']:r for r in store.rows('prediction_evaluations')}
    for r in store.rows('settlement_events'):
        p=r['payload']
        if p.get('corrected_evaluation'):result[p['prediction_id']]={'id':p['prediction_id'],'created_at':r['created_at'],'payload':p['corrected_evaluation']}
    return list(result.values())


def performance(store):
    groups=defaultdict(list);unverified=[]
    for row in resolved_evaluations(store):
        e=row['payload'];pid=e['prediction_id'];p=store.get('model_predictions',pid);selection=store.get('market_selections',pid)
        report=store.get('professional_reports','PRE_GAME:'+pid)
        if not p or not report or report['original_hash']!=digest(p) or not selection:
            unverified.append(pid);continue
        if e['data_mode']!='real':continue
        groups[(p['fixture']['league'],p['model_id'],selection['selection_class'])].append(e)
    stats=[]
    for (league,model,kind),rows in sorted(groups.items()):
        n=len(rows);accuracy=sum(r['correct'] for r in rows)/n
        z=1.96;denom=1+z*z/n;center=(accuracy+z*z/(2*n))/denom;radius=z*math.sqrt(accuracy*(1-accuracy)/n+z*z/(4*n*n))/denom
        bets=[r['market_settlement'] for r in rows if r.get('market_settlement') and r['market_settlement']['official_bet'] and kind=='OFFICIAL_PRICED']
        stats.append({'league':league,'model_id':model,'selection_class':kind,'n':n,'period':[min(r['period'] for r in rows),max(r['period'] for r in rows)],
            'sources':sorted({r['result_source'] for r in rows}),'accuracy':accuracy,'accuracy_95ci':[center-radius,center+radius],
            'team_score_mae':sum(r['score_mae'] for r in rows)/n,'margin_mae':sum(abs(r['margin_error']) for r in rows)/n,
            'total_mae':sum(abs(r['total_error']) for r in rows)/n,'log_loss':sum(r['log_loss'] for r in rows)/n,'brier':sum(r['brier'] for r in rows)/n,
            'official_bet_n':len(bets),'unit_roi':sum(b['unit_profit'] for b in bets)/len(bets) if bets else None,
            'wins':sum(b['outcome']=='win' for b in bets),'losses':sum(b['outcome']=='loss' for b in bets),'pushes':sum(b['outcome']=='push' for b in bets)})
    return {'groups':stats,'excluded_unverified_originals':unverified,
        'error_types':dict(Counter(r['payload'].get('hypothesis','unknown') for r in store.rows('error_memory'))),
        'unsupported_segment_metrics':None}


def audit_result_corrections(store):
    """Append revised evaluations; never replace the initial evaluation or prediction."""
    import numpy as np
    from datetime import datetime
    from .contracts import utcnow
    from .governance import register_report
    from .sports.contracts import parse_game
    added=[]
    for row in store.rows('record_revisions'):
        change=row['payload']
        if change['target_table']!='actual_results':continue
        result=change['replacement'];game=parse_game(result)
        if game.ends_at>utcnow() or game.published_at>utcnow():continue
        for old_row in store.rows('prediction_evaluations'):
            old=old_row['payload'];pid=old['prediction_id'];p=store.get('model_predictions',pid)
            if not p or p['fixture']['game_id']!=change['target_id']:continue
            key=pid+':'+row['id']
            if store.get('settlement_events',key):continue
            if game.quality!=('TEST/FIXTURE' if old['data_mode']=='TEST/FIXTURE' else 'verified'):raise ValueError('corrected result quality mismatch')
            outcome='draw' if game.home_score==game.away_score else 'home' if game.home_score>game.away_score else 'away'
            probabilities=p.get('outcome_probabilities',{'home':p['home_win_probability'],'away':1-p['home_win_probability']})
            if outcome not in probabilities:raise ValueError('corrected result outcome incompatible with forecast')
            scores=np.array(p['scores']);actual=np.array([game.home_score,game.away_score])
            revised={**old,'at':utcnow().isoformat(),'result_source':game.source,'actual_outcome':outcome,
                'correct':int(max(probabilities,key=probabilities.get)==outcome),
                'brier':float(.5*sum((v-float(k==outcome))**2 for k,v in probabilities.items())),
                'log_loss':-math.log(max(probabilities[outcome],1e-12)),
                'score_mae':float(np.abs(actual-scores).mean()),'margin_error':float((scores[0]-scores[1])-(actual[0]-actual[1])),
                'total_error':float(scores.sum()-actual.sum()),'market_settlement':None,'result_revision_id':row['id']}
            decision=store.get('official_recommendations',game.game_id)
            if decision and decision.get('market_id'):
                m=store.get('market_snapshots',decision['market_id'])
                if m['market_type'] in ('moneyline','three_way'):
                    if outcome=='draw' and m['market_type']=='moneyline':raise ValueError('explicit draw settlement rules required')
                    value=1 if m['selection']==outcome else -1
                elif m['market_type']=='spread':value=(actual[0]-actual[1])*(1 if m['selection']=='home' else -1)+m['line']
                else:value=(actual.sum()-m['line'])*(1 if m['selection']=='over' else -1)
                revised['market_settlement']={'outcome':'push' if value==0 else 'win' if value>0 else 'loss',
                    'unit_profit':float(0 if value==0 else m['decimal_odds']-1 if value>0 else -1),
                    'official_bet':decision['state']=='RECOMMEND','break_even_probability':1/m['decimal_odds']}
            with store.transaction() as c:
                store.append('settlement_events',key,{'prediction_id':pid,'original_evaluation_id':old_row['id'],
                    'result_revision_id':row['id'],'corrected_evaluation':revised,'at':utcnow().isoformat()},conn=c)
                register_report(store,'AUDIT',fixture_id=game.game_id,content={'amends_prediction_id':pid,'result_revision_id':row['id'],'corrected_evaluation':revised},data_mode=old['data_mode'],conn=c)
            added.append(revised)
    return added
