"""Versioned NBA adapters, independent research gates and result settlement.

No LLM impersonation. Source-observed results are distinct from certified point-in-time data.
"""
from datetime import datetime,timedelta
import math
import statistics
import re
from uuid import uuid4
from .contracts import utcnow
from .storage import digest,home
from zoneinfo import ZoneInfo

ALIASES={'NY':'NYK','GS':'GSW','SA':'SAS','NO':'NOP','WSH':'WAS','UTAH':'UTA','PHO':'PHX'}
VERSION='nba-daily-v2'


def dt(v):return datetime.fromisoformat(v.replace('Z','+00:00'))
def code(s):return ALIASES.get(s.upper(),s.upper())
def number(v):
    if isinstance(v,dict):v=v.get('value',v.get('displayValue'))
    n=float(v)
    if not math.isfinite(n) or n<0:raise ValueError('invalid score')
    return n


def teams(e):
    c=e['competitions'][0]['competitors']
    if len(c)!=2:raise ValueError('two teams required')
    return next(t for t in c if t['homeAway']=='home'),next(t for t in c if t['homeAway']=='away')


def regular(e):
    s=e.get('seasonType') or e.get('season') or {}
    notes=' '.join(n.get('headline','').lower() for c in e.get('competitions',[]) for n in c.get('notes',[]))
    cup_final='championship' in notes and any(k in notes for k in ('cup','in-season','tournament'))
    return not cup_final and int(s.get('type',2))==2 and not any(x in e.get('name','').lower() for x in ('all-star','rising stars'))


def final_result(e):
    c=e['competitions'][0];status=c.get('status') or e.get('status') or {}
    if status.get('type',{}).get('completed') is not True:return None
    h,a=teams(e);hp,ap=number(h['score']),number(a['score'])
    if hp==ap:raise ValueError('NBA final cannot tie')
    return {'id':str(e['id']),'date':e['date'],'home_id':str(h['id']),'away_id':str(a['id']),
        'home_score':hp,'away_score':ap,'home_periods':[number(x.get('value')) for x in h.get('linescores',[])],
        'away_periods':[number(x.get('value')) for x in a.get('linescores',[])],
        'status':'FINAL','source_grade':'SOURCE_OBSERVED','published_at':None}


def history(schedule,team_id,start,cutoff):
    if not isinstance(schedule.get('events'),list):raise ValueError('team schedule schema changed')
    rows=[];invalid=0
    for e in schedule['events']:
        try:
            if not regular(e) or dt(e['date'])>=min(start,cutoff):continue
            r=final_result(e)
            if not r:continue
            if str(team_id) not in (r['home_id'],r['away_id']):continue
            home=str(team_id)==r['home_id'];r.update(points_for=r['home_score'] if home else r['away_score'],points_against=r['away_score'] if home else r['home_score'],home=home)
            rows.append(r)
        except (KeyError,ValueError,TypeError,StopIteration):invalid+=1
    rows=sorted({r['id']:r for r in rows}.values(),key=lambda r:r['date'])
    if not rows:return None
    def window(rs):
        if not rs:return None
        return {'n':len(rs),'for':sum(r['points_for'] for r in rs)/len(rs),'against':sum(r['points_against'] for r in rs)/len(rs),
            'wins':sum(r['points_for']>r['points_against'] for r in rs),'median_margin':statistics.median(r['points_for']-r['points_against'] for r in rs)}
    last=window(rows[-5:]);last.update(last10=window(rows[-10:]),season=window(rows),home=window([r for r in rows if r['home']]),away=window([r for r in rows if not r['home']]),
        last_start=rows[-1]['date'],source_event_ids=[r['id'] for r in rows],records=rows,invalid_records=invalid,
        source_grade='SOURCE_OBSERVED_PUBLICATION_UNKNOWN',cutoff=cutoff.isoformat())
    return last


def match_schedule(e,official,available):
    if not available:return 'SOURCE_UNAVAILABLE',None
    h,a=teams(e);hcode=code(h['team'].get('abbreviation',''));acode=code(a['team'].get('abbreviation',''))
    candidates=[]
    for g in official:
        try:
            gh=code(g['homeTeam']['teamTricode']);ga=code(g['awayTeam']['teamTricode'])
            if (gh,ga)==(hcode,acode):candidates.append(g)
        except (KeyError,TypeError):continue
    if not candidates:return 'PARTIALLY_VERIFIED',None
    for g in candidates:
        if dt(g['gameTimeUTC'])==dt(e['date']):return 'VERIFIED',g
    return 'SOURCE_CONFLICT',candidates[0]


def official_games(body):
    if isinstance(body.get('scoreboard',{}).get('games'),list):return body['scoreboard']['games']
    schedule=body.get('leagueSchedule',{})
    if isinstance(schedule.get('gameDates'),list):return [{**g,'gameTimeUTC':g.get('gameTimeUTC') or g.get('gameDateTimeUTC')} for d in schedule['gameDates'] for g in d.get('games',[]) if g.get('gameTimeUTC') or g.get('gameDateTimeUTC')]
    raise ValueError('NBA schedule schema unavailable')


def independent_review(row,evidence,now):
    errors=[];counter=[];scores=row.get('baseline_scores_away_home')
    if row.get('schedule_status') in ('INVALID_EVENT','SOURCE_CONFLICT'):errors.append('fixture identity/time conflict')
    if row.get('already_started') or dt(row['starts_at'])<=now:errors.append('not a pre-game analysis')
    if scores is not None:
        if len(scores)!=2 or any(not math.isfinite(s) or s<0 for s in scores):errors.append('invalid score')
        for field,n in [('predicted_total',sum(scores)),('predicted_margin',scores[1]-scores[0])]:
            if field in row and abs(row[field]-n)>.001:errors.append(field+' inconsistent')
        segments=row.get('quarter_scores')
        if segments and any(abs(sum(q[i] for q in segments)-scores[i])>.001 for i in (0,1)):errors.append('quarter sum inconsistent')
    elif not row.get('already_started'):errors.append('minimum quantitative evidence missing')
    for side in ('away','home'):
        f=row.get(side+'_form')
        if f:
            for r in f.get('records',[]):
                if dt(r['date'])>=min(now,dt(row['starts_at'])):errors.append('future/current result in history')
    if evidence.get('margin') is not None and scores:
        if evidence['margin']*(scores[1]-scores[0])<0:counter.append('Independent median recent-margin evidence favors the opposite team.')
    uncertain=[x['player'] for x in row.get('injuries',[]) if x['status'] not in ('Out','Active')]
    if uncertain:counter.append('출전 불확실: '+', '.join(uncertain))
    for side in ('away','home'):
        f=row.get(side+'_form')
        if f and (dt(row['starts_at'])-dt(f['last_start'])).total_seconds()<36*3600:counter.append(side+' 연속 경기의 출전시간/피로 보정 미완료')
    if row.get('market',{}).get('published_at') is None:counter.append('Market publication time unavailable; no official price eligibility.')
    return {'verdict':'NO' if errors else 'PASS','reasons':errors or ['Identity/time/numeric checks passed for disclosed PARTIAL research scope.'],
        'counter_evidence':counter,'reviewer':'nba-independent-research-rules-v2','llm_expert_review':'NOT_CONFIGURED',
        'scope':'research quality only; no hit/profit guarantee'}


def enrich(store,e,row,summary,fetch,now,persist=True):
    h,a=teams(e);season=e.get('season',{}).get('year') or (dt(e['date']).year+int(dt(e['date']).month>=7))
    source_ids=[]
    for side,t in [('home',h),('away',a)]:
        url=f'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{t["id"]}/schedule?season={season}&seasontype=2'
        try:
            body=fetch(store,url);f=history(body,str(t['id']),dt(e['date']),now)
            if f:
                row[side+'_form']=f
                source_ids.extend(f['source_event_ids'])
        except (ValueError,KeyError,TypeError):row.setdefault('history_issues',[]).append(side+' season schedule unavailable')
    hf,af=row.get('home_form'),row.get('away_form');row['created_at']=utcnow().isoformat()
    # Save independent raw median evidence before constructing the analysis.
    evidence={'fixture_id':row['id'],'created_at':utcnow().isoformat(),'margin':hf.get('median_margin',hf.get('season',{}).get('median_margin'))-af.get('median_margin',af.get('season',{}).get('median_margin')) if hf and af and hf.get('median_margin') is not None and af.get('median_margin') is not None else None,
        'source_event_ids':sorted(set(source_ids))}
    eid='daily-blind:'+digest(evidence)
    if persist:store.append('review_evidence',eid,evidence,idempotent=True)
    if hf and af and min(hf['n'],af['n'])>=3 and not row['already_started']:
        # Compare baseline transparently. Do not label this as a trained expert model.
        row['baseline_scores_away_home']=[round((af['for']+hf['against'])/2,1),round((hf['for']+af['against'])/2,1)]
    scores=row.get('baseline_scores_away_home')
    row['simple_average_scores']=scores
    row['efficiency']={}
    for side,t in [('away',a),('home',h)]:
        row['efficiency'][side]=add_efficiency(store,row.get(side+'_form'),t['id'],fetch)
    ea,eh=row['efficiency']['away'],row['efficiency']['home']
    efficiency_scores=None
    if ea and eh and min(ea['n'],eh['n'])>=3 and not row['already_started']:
        pace=(ea['metrics']['pace']+eh['metrics']['pace'])/2
        efficiency_scores=[round(pace*(ea['metrics']['ortg']+eh['metrics']['drtg'])/200,1),round(pace*(eh['metrics']['ortg']+ea['metrics']['drtg'])/200,1)]
        row['baseline_scores_away_home']=efficiency_scores;scores=efficiency_scores
    row.update(analysis_level='PARTIAL_ANALYSIS' if scores else 'INSUFFICIENT_DATA',recommendation_status='WATCH' if scores else 'NO_BET',
        quarter_scores=None,half_scores=None,model_version='efficiency-possession-baseline-v1' if efficiency_scores else 'last5-opponent-average-v1',trained_model_used=False,
        data_quality={'recent5':bool(hf and af),'recent10':bool(hf and af and hf.get('last10') and af.get('last10')),'season':bool(hf and af and hf.get('season') and af.get('season')),
            'advanced_efficiency':bool(ea and eh and min(ea['n'],eh['n'])>=3),'confirmed_lineups':False,'source_publication_timing':False})
    if scores:row.update(predicted_margin=scores[1]-scores[0],predicted_total=sum(scores))
    # Existing verified Champion pipeline is reused only if its own input gates pass.
    if persist and not row['already_started']:
        from .models import champion
        model=champion(store)
        if model and row.get('nba_fixture'):
            from .engine import forecast
            try:
                result=forecast(store,row['nba_fixture'],model)
                if result['review']['verdict']=='PASS':
                    p=result['report'];row.update(baseline_scores_away_home=[p['scores'][1],p['scores'][0]],trained_model_used=True,model_version=model,model_prediction_id=p['id'])
            except ValueError as exc:row['model_blocked_reason']=str(exc)
    scores=row.get('baseline_scores_away_home')
    if scores:row.update(predicted_margin=scores[1]-scores[0],predicted_total=sum(scores))
    row['created_at']=utcnow().isoformat()
    row['lineups']={'confirmed':None,'expected':None,'status':'UNVERIFIED'}
    row['feature_snapshot_id']='daily-feature:'+digest({'game':row['id'],'form':[hf,af],'cutoff':now.isoformat()})
    row['review']=independent_review(row,evidence,utcnow());row['review']['evidence_id']=eid
    observed=row.get('market',{}).get('observed_at')
    if observed and utcnow()-dt(observed)>timedelta(minutes=15) and scores:row['recommendation_status']='CONDITIONAL'
    if row['review']['verdict']=='NO':row['recommendation_status']='NO_BET'
    row['prediction_id']='daily-prediction:'+digest({'id':row['id'],'created_at':row['created_at'],'scores':scores})
    if persist:
        row['market_snapshot_id']='daily-market:'+digest(row['market'])
        store.append('historical_market_odds',row['market_snapshot_id'],{'game_id':row['id'],'market':row['market'],'quality':'UNVERIFIED_PUBLICATION_AND_SCOPE'},idempotent=True)
        store.append('feature_snapshots',row['feature_snapshot_id'],{'game_id':row['id'],'cutoff':now.isoformat(),'home':hf,'away':af,'source_event_ids':source_ids},idempotent=True)
        store.append('data_quality_logs',str(uuid4()),{'game_id':row['id'],'at':utcnow().isoformat(),'schedule_status':row.get('schedule_status'),'analysis_level':row['analysis_level'],'quality':row['data_quality']})
        store.append('critical_reviews','review:'+row['prediction_id'],{'prediction_id':row['prediction_id'],'report_hash':digest(row),'at':utcnow().isoformat(),**row['review']},idempotent=True)
    return row


def save_predictions(store,rows,report_id,generated_at,conn=None,origin="manual"):
    for row in rows:
        if not row.get('baseline_scores_away_home') or row.get('review',{}).get('verdict')!='PASS':continue
        if dt(row['starts_at'])<=dt(generated_at):continue
        p={'kind':'daily_research_prediction','game_id':row['id'],'report_id':report_id,'created_at':generated_at,'starts_at':row['starts_at'],
            'scores':row['baseline_scores_away_home'],'model_version':row.get('model_version'),'feature_snapshot_id':row.get('feature_snapshot_id'),'market_snapshot_id':row.get('market_snapshot_id'),
            'review_id':'review:'+row['prediction_id'],'recommendation_status':row.get('recommendation_status','WATCH'),'market':row['market'],'selection_class':'SCHEDULED_OBSERVATION' if origin=='schedule' else 'MANUAL_OBSERVATION',
            'quarter_scores':row.get('quarter_scores'),'half_scores':row.get('half_scores'),'analysis_hash':digest(row)}
        store.append('forecast_components',row['prediction_id'],p,idempotent=True,conn=conn)


def settlement(value):return 'Push' if abs(value)<1e-9 else 'W' if value>0 else 'L'


def grade(p,r):
    if dt(p['created_at'])>=dt(p['starts_at']):raise ValueError('post-start prediction excluded')
    a,h=p['scores'];ap,hp=r['away_score'],r['home_score'];margin=h-a;actual=hp-ap
    market=p.get('market',{});line=market.get('home_line');total=market.get('total')
    spread=None if line is None or abs(margin+line)<1e-9 else settlement((actual+line)*(1 if margin+line>0 else -1))
    total_grade=None if total is None or abs(a+h-total)<1e-9 else settlement((ap+hp-total)*(1 if a+h-total>0 else -1))
    segments=None
    if p.get('quarter_scores') and r.get('home_periods') and len(p['quarter_scores'])<=min(len(r['home_periods']),len(r['away_periods'])):
        segments=[{'period':i+1,'team_mae':(abs(q[0]-r['away_periods'][i])+abs(q[1]-r['home_periods'][i]))/2,
            'margin_mae':abs((q[1]-q[0])-(r['home_periods'][i]-r['away_periods'][i])),
            'total_mae':abs(sum(q)-r['home_periods'][i]-r['away_periods'][i])} for i,q in enumerate(p['quarter_scores'])]
    return {'prediction_id':p.get('prediction_id'),'prediction_created_at':p['created_at'],'game_id':p['game_id'],'actual_scores':[ap,hp],'predicted_scores':[a,h],
        'direction_correct':None if abs(margin)<1e-9 else (margin>0)==(actual>0),'spread':spread,'total':total_grade,
        'team_mae':(abs(a-ap)+abs(h-hp))/2,'margin_mae':abs(margin-actual),'total_mae':abs(a+h-ap-hp),
        'selection_class':p.get('selection_class','OBSERVATION'),'roi':None,'segment_metrics':segments,'away_score_error':a-ap,'home_score_error':h-hp,'result_grade':r['source_grade']}


def audit_predictions(store,fetch,now):
    out=[];excluded=[];cache={}
    for row in store.rows('forecast_components'):
        p=row['payload']
        if p.get('kind')!='daily_research_prediction':continue
        key='daily-audit:'+row['id'];old=store.get('settlement_events',key)
        if dt(p['created_at'])>=dt(p['starts_at']):excluded.append(row['id']);continue
        if dt(p['starts_at'])>now:continue
        try:
            original=store.get('professional_reports',p['report_id'])
            source_row=next((r for r in original.get('content',{}).get('games',[]) if r.get('prediction_id')==row['id']),None) if original else None
            review=store.get('critical_reviews',p.get('review_id'))
            if not source_row or digest(source_row)!=p.get('analysis_hash') or not review or review['verdict']!='PASS' or review['report_hash']!=p['analysis_hash']:
                excluded.append(row['id']);continue
            if old:out.append(old['evaluation']);continue
            gid=p['game_id']
            if gid not in cache:cache[gid]=fetch(store,'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event='+gid)
            summary=cache[gid];e=summary.get('header',{});competitions=e.get('competitions',[])
            if not competitions:continue
            result=final_result({'id':gid,'date':p['starts_at'],'competitions':competitions})
            if not result:continue
            evaluation=grade({**p,'prediction_id':row['id']},result)
            with store.transaction() as c:
                store.append('settlement_events',key,{'kind':'daily_research_audit','evaluation':evaluation,'result':result,'at':utcnow().isoformat()},conn=c)
                store.append('learning_feedback',key,{'kind':'error_hypothesis','prediction_id':row['id'],'margin_error':evaluation['margin_mae'],'total_error':evaluation['total_mae'],
                    'hypothesis':'Check lineup availability, possession efficiency and opponent strength before any weight changes.','verified_improvement':False},conn=c)
            out.append(evaluation)
        except (ValueError,KeyError,TypeError):continue
    scopes={scope:{'n':sum(e['selection_class']==scope for e in out),'direction_n':sum(e['selection_class']==scope and e['direction_correct'] is not None for e in out),'direction_wins':sum(e['selection_class']==scope and e['direction_correct'] is True for e in out)} for scope in {e['selection_class'] for e in out}}
    all_count=len(out)
    main_scope='SCHEDULED_OBSERVATION' if any(e['selection_class']=='SCHEDULED_OBSERVATION' for e in out) else 'MANUAL_OBSERVATION' if any(e['selection_class']=='MANUAL_OBSERVATION' for e in out) else 'OBSERVATION'
    first_by_game={}
    for e in sorted(out,key=lambda e:e.get('prediction_created_at','')):
        if e['selection_class']==main_scope:first_by_game.setdefault(e['game_id'],e)
    out=list(first_by_game.values())
    resolved=[e for e in out if e['direction_correct'] is not None]
    return {'n':len(out),'main_scope':main_scope,'all_evaluation_count':all_count,'direction_n':len(resolved),'direction_wins':sum(e['direction_correct'] for e in resolved),
        'accuracy':sum(e['direction_correct'] for e in resolved)/len(resolved) if resolved else None,
        'team_mae':statistics.mean(e['team_mae'] for e in out) if out else None,'margin_mae':statistics.mean(e['margin_mae'] for e in out) if out else None,
        'total_mae':statistics.mean(e['total_mae'] for e in out) if out else None,'worst_three':sorted(out,key=lambda e:e['margin_mae'],reverse=True)[:3],
        'official_priced_n':0,'observation_groups':scopes,'observation_n':len(out),'roi':None,'excluded_post_start':excluded,'result_verification':'ESPN_SOURCE_OBSERVED; official result reconciliation pending'}


def efficiency(summary,team_id):
    """Derived possessions, never labeled as official tracking/lineup efficiency."""
    c=summary.get('header',{}).get('competitions',[{}])[0]
    if not c.get('status',{}).get('type',{}).get('completed'):return None
    ts=summary.get('boxscore',{}).get('teams',[])
    if len(ts)!=2:return None
    values={str(t['team']['id']):{x['name']:x.get('displayValue') for x in t.get('statistics',[])} for t in ts}
    if str(team_id) not in values:return None
    own=values[str(team_id)];opp=next(v for k,v in values.items() if k!=str(team_id))
    try:
        def stats(d):
            fg,fga=map(float,d['fieldGoalsMade-fieldGoalsAttempted'].split('-'))
            th,tha=map(float,d['threePointFieldGoalsMade-threePointFieldGoalsAttempted'].split('-'))
            ft,fta=map(float,d['freeThrowsMade-freeThrowsAttempted'].split('-'))
            ore=number(d['offensiveRebounds']);dre=number(d['defensiveRebounds']);to=number(d.get('totalTurnovers',d.get('turnovers')))
            if fga<=0 or not (0<=fg<=fga and 0<=th<=tha<=fga and 0<=ft<=fta):raise ValueError('invalid boxscore')
            return dict(fg=fg,fga=fga,th=th,tha=tha,ft=ft,fta=fta,ore=ore,dre=dre,to=to,points=2*fg+th+ft,poss=fga-ore+to+.44*fta)
        x,y=stats(own),stats(opp);poss=(x['poss']+y['poss'])/2
        if poss<=0:return None
        # Reconcile derived points with the independently supplied header final.
        header={str(t['id']):number(t['score']) for t in c['competitors']}
        if header[str(team_id)]!=x['points'] or next(v for k,v in header.items() if k!=str(team_id))!=y['points']:return None
        minutes=48+max(0,int(c.get('status',{}).get('period',4))-4)*5
        out={'ortg':100*x['points']/poss,'drtg':100*y['points']/poss,'net_rtg':100*(x['points']-y['points'])/poss,'pace':poss*48/minutes,
            'efg_pct':(x['fg']+.5*x['th'])/x['fga'],'ts_pct':x['points']/(2*(x['fga']+.44*x['fta'])),'three_par':x['tha']/x['fga'],
            'ftr':x['fta']/x['fga'],'oreb_pct':x['ore']/(x['ore']+y['dre']) if x['ore']+y['dre'] else None,
            'dreb_pct':x['dre']/(x['dre']+y['ore']) if x['dre']+y['ore'] else None,'tov_pct':x['to']/(x['fga']+.44*x['fta']+x['to'])}
        if any(v is not None and not math.isfinite(v) for v in out.values()):return None
        return out
    except (ValueError,KeyError,TypeError,ZeroDivisionError):return None


def add_efficiency(store,form,team_id,fetch):
    if not form or not form.get('records'):return None
    metrics=[];ids=[]
    # Bounded to ten completed games. Reuse only recent, structurally verified final snapshots.
    for r in form['records'][-10:]:
        url='https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event='+r['id'];body=None
        raw=store.latest_source(url)
        if raw:
            p=raw['payload']
            if p.get('collected_at') and utcnow()-dt(p['collected_at'])<timedelta(hours=24):body=p.get('body')
        try:body=body or fetch(store,url)
        except ValueError:continue
        m=efficiency(body,team_id)
        if m:
            metrics.append(m);ids.append(r['id'])
            store.append('team_statistics','derived:'+digest([r['id'],str(team_id),m]),{'game_id':r['id'],'team_id':str(team_id),'source_url':url,
                'grade':'DERIVED_POSSESSION_ESTIMATE','method':'mean(FGA-OREB+TOV+0.44*FTA), OT-adjusted pace','metrics':m},idempotent=True)
    if not metrics:return None
    return {'n':len(metrics),'games':ids,'metrics':{k:statistics.mean(m[k] for m in metrics if m[k] is not None) if any(m[k] is not None for m in metrics) else None for k in metrics[0]},
        'grade':'DERIVED_POSSESSION_ESTIMATE','official_tracking_data':False}


def learning_cycle(store,now):
    """Reuse existing Champion/Challenger gates, never train on timestamp-unknown archives."""
    key='nba-learning:'+str(now.date());old=store.get('training_runs',key)
    if old:return old
    verified=[r for r in store.rows('historical_game_results') if r['payload'].get('league')=='NBA' and r['payload'].get('quality')=='verified']
    if len(verified)<100:
        result={'kind':'NBA_LEARNING_GATE','at':now.isoformat(),'state':'BLOCKED','verified_games':len(verified),'reason':'Need >=100 verified point-in-time games; public archive research is not eligible.','promoted':False}
    else:
        from .engine import daily
        try:result={'kind':'NBA_LEARNING_GATE','at':now.isoformat(),'state':'CHECKED','result':daily(store,retrain=True,league='NBA')}
        except ValueError as exc:result={'kind':'NBA_LEARNING_GATE','at':now.isoformat(),'state':'BLOCKED','reason':str(exc),'promoted':False}
    store.append('training_runs',key,result,idempotent=True)
    return result


def quoted_prices(market):
    def decimal(value):
        try:
            n=float(value)
            if not math.isfinite(n) or abs(n)<100:return None
            return round(1+n/100 if n>0 else 1+100/abs(n),4)
        except (TypeError,ValueError):return None
    out={}
    for kind,sides in [('moneyline',('away','home')),('pointSpread',('away','home')),('total',('over','under'))]:
        for side in sides:
            raw=market.get(kind,{}).get(side,{}).get('close',{}).get('odds')
            if raw is None:
                if kind=='moneyline':raw=market.get(side+'TeamOdds',{}).get('moneyLine')
                elif kind=='pointSpread':raw=market.get(side+'TeamOdds',{}).get('spreadOdds')
                else:raw=market.get(side+'Odds')
            out[kind+'_'+side]={'american':raw,'decimal':decimal(raw)}
    return out


def official_cache(store,target,now):
    from pathlib import Path
    import json
    paths=[Path(__file__).resolve().parents[1]/'data/nba-official-schedule-cache.json', home()/'official-schedule-cache.json']
    candidates=[]
    for path in paths:
        if not path.exists():continue
        try:
            data=json.loads(path.read_text());at=dt(data['collected_at'])
            if data.get('source_url')!='https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json':continue
            if at>now or now-at>timedelta(hours=min(24,data.get('max_age_hours',24))):continue
            if digest(data['games'])!=data['body_sha256']:continue
            games=[g for g in data['games'] if dt(g['gameTimeUTC']).astimezone(ZoneInfo('Asia/Seoul')).date()==target]
            if games:candidates.append((at,games,data))
        except (ValueError,KeyError,TypeError):continue
    if not candidates:return [],None
    _,games,data=max(candidates,key=lambda c:c[0])
    store.append('data_quality_logs',str(uuid4()),{'state':'OFFICIAL_CACHED_SNAPSHOT','source_url':data['source_url'],'collected_at':data['collected_at'],
        'snapshot_hash':data['body_sha256'],'at':utcnow().isoformat(),'live_official_verified':False})
    return games,{'source_url':data['source_url'],'collected_at':data['collected_at'],'body_sha256':data['body_sha256']}
