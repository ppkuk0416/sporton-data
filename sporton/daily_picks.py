"""Immutable user-facing TOP choices, exposure-aware settlement, separate score ledgers."""
from datetime import timedelta
import math
from zoneinfo import ZoneInfo
from .contracts import utcnow
from .storage import digest
from .nba_quality import dt,final_result,settlement,efficiency

TYPES={'OFFICIAL_PRICE':'공식 가격픽','OBSERVATION':'관찰 픽','CONDITIONAL':'조건부 픽'}
NAMES=dict(zip(
 ['Atlanta Hawks','Boston Celtics','Brooklyn Nets','Charlotte Hornets','Chicago Bulls','Cleveland Cavaliers','Dallas Mavericks','Denver Nuggets','Detroit Pistons','Golden State Warriors','Houston Rockets','Indiana Pacers','LA Clippers','Los Angeles Clippers','Los Angeles Lakers','Memphis Grizzlies','Miami Heat','Milwaukee Bucks','Minnesota Timberwolves','New Orleans Pelicans','New York Knicks','Oklahoma City Thunder','Orlando Magic','Philadelphia 76ers','Phoenix Suns','Portland Trail Blazers','Sacramento Kings','San Antonio Spurs','Toronto Raptors','Utah Jazz','Washington Wizards'],
 ['애틀랜타','보스턴','브루클린','샬럿','시카고','클리블랜드','댈러스','덴버','디트로이트','골든스테이트','휴스턴','인디애나','LA 클리퍼스','LA 클리퍼스','LA 레이커스','멤피스','마이애미','밀워키','미네소타','뉴올리언스','뉴욕','오클라호마시티','올랜도','필라델피아','피닉스','포틀랜드','새크라멘토','샌안토니오','토론토','유타','워싱턴']))


def name(team):return NAMES.get(team,team or '팀 미확인')
def finite(v):return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)


def pick_label(p):
    if p['market_type']=='moneyline':return name(p['team'])+' 승리'
    if p['market_type']=='total':return f"총점 {p['line']:g} "+('오버' if p['selection']=='over' else '언더')
    if p['pick_type']=='CONDITIONAL':return f"{name(p['team'])} {p['condition']['threshold']:+g} 이상일 때 검토"
    return f"{name(p['team'])} {p['line']:+g}"


def reasons_and_risks(row):
    a,h=row['baseline_scores_away_home'];reasons=[];risks=[]
    if h!=a:reasons.append(f"예상은 {name(row['away'])} {a:g}–{h:g} {name(row['home'])}, {name(row['home'] if h>a else row['away'])} {abs(h-a):g}점 우세")
    for side in ('away','home'):
        f=row.get(side+'_form');ef=row.get('efficiency',{}).get(side)
        if f:reasons.append(f"{name(row[side])} 최근 {f['n']}경기 {f['wins']}승 {f['n']-f['wins']}패, 평균 득점 {f['for']:.1f}·실점 {f['against']:.1f}")
        if ef and ef.get('metrics',{}).get('net_rtg') is not None:
            reasons.append(f"{name(row[side])} 최근 {ef['n']}경기 파생 NetRtg {ef['metrics']['net_rtg']:+.1f}")
        if f and (dt(row['starts_at'])-dt(f['last_start'])).total_seconds()<36*3600:risks.append(name(row[side])+'의 연속 일정과 출전시간 부담')
    af=row.get('away_form');hf=row.get('home_form')
    if af and hf:
        away_net=af['for']-af['against'];home_net=hf['for']-hf['against']
        stronger='home' if home_net>away_net else 'away' if away_net>home_net else None
        if stronger:reasons.insert(1,f"최근 득실점 차는 {name(row[stronger])}가 {abs(home_net-away_net):.1f}점 더 좋아 전력 방향을 뒷받침하지만 상대 난도 차이는 남아 있음")
        for side,f in [('away',af),('home',hf)]:
            hours=(dt(row['starts_at'])-dt(f['last_start'])).total_seconds()/3600
            reasons.append(f"{name(row[side])} 직전 경기 시작과 {hours:.1f}시간 간격"+('으로 연속 일정 부담' if hours<36 else '으로 일정 간격 확보'))
    uncertain=[x['player'] for x in row.get('injuries',[]) if x.get('status') not in ('Out','Active')]
    out=[x['player'] for x in row.get('injuries',[]) if x.get('status')=='Out']
    if uncertain:risks.insert(0,'출전 불확실: '+', '.join(uncertain[:3]))
    elif out:risks.insert(0,'결장 표기 '+', '.join(out[:3])+'의 대체 역할·출전시간 영향 미확정')
    if not row.get('trained_model_used'):risks.append('검증된 실전 예측모델이 아닌 기준선 시나리오')
    if row.get('review',{}).get('counter_evidence'):risks.append('독립 검사의 반대 근거와 최종 명단을 재확인해야 함')
    return reasons,risks or ['최종 선발과 시장 가격 변동']


def _official(row,store,now):
    """Only recover an existing published official decision; never upgrade a public quote."""
    if store is None:return None
    fixture=row.get('nba_fixture',{}).get('game_id') or row['id']
    d=store.get('official_recommendations',fixture)
    if not d or d.get('state')!='RECOMMEND' or d.get('review')!='PASS' or d.get('data_mode')!='real':return None
    p=store.get('model_predictions',d.get('prediction_id'));m=store.get('market_snapshots',d.get('market_id'));review=store.get('critical_reviews',d.get('review_id'))
    if not p or not m or not review or not row.get('trained_model_used') or row.get('model_prediction_id')!=d.get('prediction_id') or review.get('verdict')!='PASS' or review.get('report_hash')!=digest(p):return None
    if m.get('quality')!='verified' or not finite(m.get('decimal_odds')) or m['decimal_odds']<=1:return None
    if m.get('game_id')!=fixture or m.get('scope')!='including_overtime':return None
    if dt(p['created_at'])>=dt(row['starts_at']) or not m.get('published_at'):return None
    if not dt(m['published_at'])<=dt(m['collected_at'])<=now or now-dt(m['collected_at'])>timedelta(minutes=15):return None
    side=m['selection'];kind=m['market_type']
    if kind not in ('spread','total','moneyline'):return None
    if kind!='moneyline' and not finite(m.get('line')):return None
    return {'pick_type':'OFFICIAL_PRICE','market_type':kind,'selection':side,'line':m.get('line'),
        'team':row.get(side) if side in ('away','home') else None,'odds':m['decimal_odds'],'bookmaker':m['bookmaker'],
        'observed_at':m['collected_at'],'published_at':m['published_at'],'publication_verified':True,'condition':None,
        'scenario_gap':abs(row['baseline_scores_away_home'][1]-row['baseline_scores_away_home'][0]+m.get('line',0)) if kind=='spread' else None,'official_decision_id':fixture,'official_prediction_id':d['prediction_id'],'market_snapshot_id':d['market_id'],'priority':1000.}


def select_picks(rows,now,store=None):
    """Distinct games, explicit prices or explicit self-derived conditions; no EV claim."""
    choices=[]
    for row in rows:
        scores=row.get('baseline_scores_away_home')
        if row.get('already_started') or dt(row['starts_at'])<=now or row.get('review',{}).get('verdict')!='PASS':continue
        if row.get('schedule_status') in ('INVALID_EVENT','SOURCE_CONFLICT'):continue
        if not scores or len(scores)!=2 or not all(finite(x) and x>=0 for x in scores):continue
        forms=[row.get(s+'_form') for s in ('away','home')]
        if not all(f and f.get('n',0)>=3 for f in forms):continue
        a,h=scores;margin=h-a;total=a+h;m=row.get('market',{});observed=m.get('observed_at')
        fresh=bool(observed and timedelta(0)<=now-dt(observed)<=timedelta(minutes=15))
        reasons,risks=reasons_and_risks(row)
        support=sum(min((f.get('last10') or f).get('n',f['n']),10) for f in forms)/20
        support+=sum(bool(row.get('efficiency',{}).get(s)) for s in ('away','home'))*.25
        penalty=min(3,len([i for i in row.get('injuries',[]) if i.get('status') not in ('Active',)]))*.12
        candidates=[];official=_official(row,store,now)
        if official:candidates.append(official)
        def candidate(kind,side,line,gap,price_key,scale):
            if not fresh:return
            price=m.get('prices',{}).get(price_key,{}).get('decimal')
            candidates.append({'pick_type':'OBSERVATION','market_type':kind,'selection':side,'line':line,'team':row.get(side),
                'odds':price if finite(price) and price>1 else None,'bookmaker':m.get('bookmaker'),'observed_at':observed,
                'published_at':m.get('published_at'),'publication_verified':False,'condition':None,
                'market_snapshot_id':row.get('market_snapshot_id'),'scenario_gap':gap,'priority':support-penalty+min(gap/scale,1.)})
        line=m.get('home_line')
        if finite(line) and abs(margin+line)>2:
            side='home' if margin+line>0 else 'away';candidate('spread',side,line if side=='home' else -line,abs(margin+line),'pointSpread_'+side,12.)
        if finite(m.get('total')) and abs(total-m['total'])>2:
            side='over' if total>m['total'] else 'under';candidate('total',side,m['total'],abs(total-m['total']),'total_'+side,18.)
        if margin!=0:
            side='home' if margin>0 else 'away';price=m.get('prices',{}).get('moneyline_'+side,{}).get('decimal')
            if finite(price) and price>1:candidate('moneyline',side,None,abs(margin),'moneyline_'+side,15.)
        if not candidates and margin!=0:
            side='home' if margin>0 else 'away';threshold=math.ceil((-abs(margin)+2)*2)/2
            candidates.append({'pick_type':'CONDITIONAL','market_type':'spread','selection':side,'team':row[side],'line':None,
                'odds':None,'bookmaker':None,'observed_at':None,'published_at':None,'publication_verified':False,
                'condition':{'operator':'>=','threshold':threshold,'basis':'자체 검토 기준. 실제로 조회한 시장선이 아님. 경기 전 조건 충족 가격 확인이 필요함.'},
                'priority':support-penalty-.25,'scenario_gap':None})
        if not candidates:continue
        p=max(candidates,key=lambda p:p['priority'])
        p.update(game_id=row['id'],starts_at=row['starts_at'],away=row['away'],home=row['home'],created_at=now.isoformat(),
            expected_scores=list(scores),expected_margin=margin,expected_total=total,reasons=reasons,risks=risks,
            confidence='높음' if p['pick_type']=='OFFICIAL_PRICE' and row.get('analysis_level')=='FULL_ANALYSIS' else '보통' if row.get('trained_model_used') else '낮음',
            reference_market=dict(m),source_prediction_id=row.get('prediction_id'),source_url=row.get('source_url'),model_version=row.get('model_version'))
        if p['market_type']=='spread' and p.get('line') is not None:p['reasons'].insert(0,f"선택한 핸디 {p['line']:+g}와 예상 득점 차를 비교하면 여유 {p['scenario_gap']:.1f}점 — 검증된 수익 우위는 아님")
        elif p['market_type']=='total':p['reasons'].insert(0,f"예상 총점 {total:g}과 조회 기준 {p['line']:g}의 차이 {abs(total-p['line']):.1f}점")
        choices.append(p)
    ranked=sorted(choices,key=lambda p:(p['pick_type']=='OFFICIAL_PRICE',p['priority']),reverse=True)[:3]
    for rank,p in enumerate(ranked,1):
        p['rank']=rank;p['pick_id']='reader-pick:'+digest(p);p['label']=pick_label(p)
    return ranked


def save_picks(store,picks,report_id,briefing_id,recorded_at,conn=None):
    for pick in picks:
        if dt(pick['created_at'])>=dt(pick['starts_at']) or not dt(pick['created_at'])<=dt(recorded_at)<dt(pick['starts_at']):raise ValueError('TOP pick cannot be frozen after start')
        payload={**pick,'kind':'daily_top_pick','report_id':report_id,'briefing_id':briefing_id,'recorded_at':recorded_at,
            'original_pick_hash':digest(pick),'exposure':'verify Telegram receipt before performance attribution'}
        store.append('market_selections',pick['pick_id'],payload,idempotent=True,conn=conn)


def record_condition_quote(store,pick_id,quote,now=None):
    now=now or utcnow();p=store.get('market_selections',pick_id)
    if not p or p.get('pick_type')!='CONDITIONAL' or not _original(store,p):raise ValueError('conditional pick required')
    observed=dt(quote['observed_at'])
    if not dt(p['created_at'])<=observed<=now<dt(p['starts_at']):raise ValueError('condition evidence must be recorded before start')
    if quote.get('game_id')!=p['game_id'] or quote.get('market_type')!=p['market_type'] or quote.get('selection')!=p['selection']:raise ValueError('condition quote identity mismatch')
    if not str(quote.get('source_url','')).startswith('https://') or not quote.get('bookmaker') or not finite(quote.get('line')):raise ValueError('evidenced quote required')
    if quote['line']<p['condition']['threshold']:raise ValueError('condition not met')
    payload={'kind':'daily_pick_condition_evidence','pick_id':pick_id,'recorded_at':now.isoformat(),'quote':quote}
    store.append('market_selections','condition:'+digest(payload),payload,idempotent=True)


def _original(store,p):
    original=store.get('professional_reports',p['report_id'])
    if not original or not original.get('created_at') or dt(original['created_at'])>=dt(p['starts_at']):return None
    if p.get('original_schema')=='legacy_top_watch':
        candidates=original.get('content',{}).get('top_watch',[]);rank=p.get('legacy_rank',0)
        if not 0<rank<=len(candidates):return None
        item=_legacy_item({**original,'id':p['report_id']},candidates[rank-1],rank)
    else:item=next((x for x in original.get('content',{}).get('top_picks',[]) if x.get('pick_id')==p['pick_id']),None)
    if original.get('original_hash')!=digest(original.get('content',{})):return None
    if item is None or digest(item)!=p['original_pick_hash']:return None
    if dt(p['created_at'])>=dt(p['starts_at']):return None
    if p.get('original_schema')!='legacy_top_watch' and not dt(p['created_at'])<=dt(p['recorded_at'])<dt(p['starts_at']):return None
    return item


def _feedback(p,result,summary,source_row):
    a,h=p['expected_scores'];ap,hp=result['away_score'],result['home_score'];facts=[]
    for side,expected,actual in [('away',a,ap),('home',h,hp)]:
        delta=actual-expected
        facts.append(f"{name(p[side])} 실제 득점이 예상보다 {abs(delta):.1f}점 "+('많았음' if delta>0 else '적었음' if delta<0 else '같았음'))
    if summary:
        cs=summary.get('header',{}).get('competitions',[{}])[0].get('competitors',[])
        for team in cs:
            side=team.get('homeAway');m=efficiency(summary,team.get('id'))
            if side in ('home','away') and m:
                facts.append(f"{name(p[side])} 실제 eFG {m['efg_pct']*100:.1f}%, TOV {m['tov_pct']*100:.1f}% (포제션 추정 기반)")
    total_error=(ap+hp)-(a+h);margin_error=(hp-ap)-(h-a)
    next_check='총점 오차가 커서 포제션·3점 시도와 성공·자유투를 먼저 대조' if abs(total_error)>abs(margin_error) else '득점 차 오차가 커서 양 팀 대체 선수 출전시간·턴오버·리바운드를 먼저 대조'
    return {'facts':facts,'judgement':'예측 오차는 확인됐으나 부상·로테이션 등의 인과 원인은 아직 미확인',
        'hypothesis':next_check+'할 필요가 있다는 가설','next_check':next_check+'. 한 경기만으로 가중치를 변경하지 않음.',
        'expected_scores':[a,h],'actual_scores':[ap,hp],'margin_error':margin_error,'total_error':total_error,
        'process_warning':'적중 여부와 별개로 예상 과정 점검 필요' if max(abs(total_error),abs(margin_error))>10 else '적중했어도 한 경기로 분석 방법의 우수성을 확정하지 않음'}


def audit_top_picks(store,fetch,now,target):
    restored=recover_legacy_picks(store)
    saved=[r['payload'] for r in store.rows('market_selections') if r['payload'].get('kind')=='daily_top_pick']
    evaluated=[];excluded=[];summaries={};conditions=[r['payload'] for r in store.rows('market_selections') if r['payload'].get('kind')=='daily_pick_condition_evidence']
    for p in saved:
        if not _original(store,p):excluded.append({'pick_id':p['pick_id'],'reason':'사전 원본·해시·시각 확인 불가'});continue
        exposure=store.get('briefing_events','telegram:sent:'+p['briefing_id'])
        freeze=p['created_at'] if p.get('original_schema')=='legacy_top_watch' else p['recorded_at']
        if not exposure or not exposure.get('at') or not dt(freeze)<=dt(exposure['at'])<dt(p['starts_at']):excluded.append({'pick_id':p['pick_id'],'reason':'사용자 전달 성공 기록 미확인'});continue
        entry={'pick':p,'status':'PENDING','result':None,'feedback':None,'unit_profit':None}
        if dt(p['starts_at'])>now:evaluated.append(entry);continue
        quote=p;condition_id=None
        if p['pick_type']=='CONDITIONAL':
            evidence=[x for x in conditions if x['pick_id']==p['pick_id'] and dt(x['recorded_at'])<dt(p['starts_at'])
                and dt(p['created_at'])<=dt(x['quote']['observed_at'])<=dt(x['recorded_at']) and x['quote']['line']>=p['condition']['threshold']]
            if not evidence:entry['status']='CONDITION_UNCONFIRMED'
            else:
                found=min(evidence,key=lambda x:x['quote']['observed_at']);quote={**p,**found['quote']};condition_id=digest(found)
        try:
            gid=p['game_id']
            if gid not in summaries:
                source=store.latest_source('https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event='+gid)
                if source and now-timedelta(minutes=15)<=dt(source['payload']['collected_at'])<=now:summary=source['payload']['body']
                else:summary=fetch(store,'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event='+gid)
                summaries[gid]=summary
            summary=summaries[gid];comp=summary.get('header',{}).get('competitions',[])
            if not comp:evaluated.append(entry);continue
            result=final_result({'id':gid,'date':p['starts_at'],'competitions':comp})
            if not result:evaluated.append(entry);continue
            entry['result']=result
            if entry['status']!='CONDITION_UNCONFIRMED':
                margin=result['home_score']-result['away_score'];kind=p['market_type'];side=p['selection']
                if kind=='spread':value=margin*(1 if side=='home' else -1)+quote['line']
                elif kind=='total':value=(result['home_score']+result['away_score']-quote['line'])*(1 if side=='over' else -1)
                else:value=margin*(1 if side=='home' else -1)
                entry['status']=settlement(value)
                if p['pick_type']=='OFFICIAL_PRICE' and finite(p.get('odds')):entry['unit_profit']=p['odds']-1 if value>0 else -1 if value<0 else 0
            entry['feedback']=_feedback(p,result,summary,None)
            entry['condition_evidence_hash']=condition_id
            key='top-pick-audit:'+digest({'pick_id':p['pick_id'],'result':result,'condition':condition_id})
            existing=store.get('settlement_events',key)
            if existing:entry=existing['evaluation']
            else:
                with store.transaction() as c:
                    store.append('settlement_events',key,{'kind':'daily_top_pick_audit','at':now.isoformat(),'evaluation':entry},conn=c)
                    store.append('learning_feedback',key,{'kind':'daily_top_pick_feedback','pick_id':p['pick_id'],'feedback':entry['feedback'],'verified_cause':False,'weights_changed':False},conn=c)
        except (ValueError,TypeError,KeyError):entry['result_collection_issue']=True
        evaluated.append(entry)
    groups={}
    for kind in TYPES:
        rows=[r for r in evaluated if r['pick']['pick_type']==kind];w=sum(r['status']=='W' for r in rows);l=sum(r['status']=='L' for r in rows);push=sum(r['status']=='Push' for r in rows)
        priced=[r for r in rows if r['unit_profit'] is not None]
        groups[kind]={'wins':w,'losses':l,'pushes':push,'settled':w+l+push,'count':len(rows),'accuracy':w/(w+l) if w+l else None,
            'roi':sum(r['unit_profit'] for r in priced)/len(priced) if kind=='OFFICIAL_PRICE' and priced else None,
            'condition_unconfirmed':sum(r['status']=='CONDITION_UNCONFIRMED' for r in rows)}
    prior=[r for r in evaluated if dt(r['pick']['created_at']).astimezone(ZoneInfo('Asia/Seoul')).date()<target]
    previous_id=max(prior,key=lambda r:r['pick']['created_at'])['pick']['report_id'] if prior else None
    delivered_reports=[]
    for b in store.rows('briefings'):
        payload=b['payload'];rid=payload.get('research_report_id');report=store.get('professional_reports',rid) if rid else None
        if not report or dt(report['created_at']).astimezone(ZoneInfo('Asia/Seoul')).date()>=target:continue
        receipt=store.get('briefing_events','telegram:sent:'+b['id'])
        if receipt and receipt.get('at'):delivered_reports.append((report['created_at'],rid))
    if delivered_reports:previous_id=max(delivered_reports)[1]
    previous=sorted([r for r in prior if r['pick']['report_id']==previous_id],key=lambda r:r['pick']['rank'])
    return {'previous':previous,'previous_report_id':previous_id,'groups':groups,'excluded':excluded,
        'all_win':bool(previous) and all(r['status']=='W' for r in previous),'previous_count':len(previous),
        'records':evaluated,'legacy_recovered':restored,'legacy_note':'이전 TOP은 불변 원본·선택·시각·전달 기록이 확인될 때만 복구하며 확인 불가 성적은 합산하지 않음.'}


def _legacy_item(original,candidate,rank):
    content=original.get('content',{});row=next((r for r in content.get('games',[]) if r.get('id')==candidate.get('game_id')),None)
    if not row or not row.get('baseline_scores_away_home') or candidate.get('selection') not in ('home spread','away spread'):return None
    at=original.get('created_at');observed=row.get('market',{}).get('observed_at') or candidate.get('observed_at')
    if not at or dt(at)>=dt(row['starts_at']) or not observed or dt(observed)>dt(at):return None
    home_line=candidate.get('home_line')
    if not finite(home_line) or home_line!=row.get('market',{}).get('home_line'):return None
    side='home' if candidate['selection']=='home spread' else 'away';scores=row['baseline_scores_away_home'];reason,risk=reasons_and_risks(row)
    p={'rank':rank,'game_id':row['id'],'away':row['away'],'home':row['home'],'starts_at':row['starts_at'],'created_at':at,
        'pick_type':'OBSERVATION','market_type':'spread','selection':side,'team':row[side],'line':home_line if side=='home' else -home_line,
        'odds':row['market'].get('prices',{}).get('pointSpread_'+side,{}).get('decimal'),'bookmaker':row['market'].get('bookmaker'),
        'observed_at':observed,'published_at':row['market'].get('published_at'),'publication_verified':False,'condition':None,
        'expected_scores':scores,'expected_margin':scores[1]-scores[0],'expected_total':sum(scores),'reasons':[candidate.get('reason') or candidate.get('meaning') or '이전 보고서에는 선택 근거 문구를 별도로 저장하지 않아 당시 예상·기준점만 확인합니다.'],'risks':risk,
        'confidence':'낮음','source_prediction_id':row.get('prediction_id'),'source_url':row.get('source_url'),'model_version':row.get('model_version'),
        'original_schema':'legacy_top_watch','legacy_rank':rank}
    p['pick_id']='legacy-reader-pick:'+digest({'report_id':original['id'],'rank':rank,'candidate':candidate})
    p['label']=pick_label(p)
    return p


def recover_legacy_picks(store):
    briefings={r['payload'].get('research_report_id'):r['id'] for r in store.rows('briefings') if r['payload'].get('research_report_id')}
    recovered=0
    for record in store.rows('professional_reports'):
        r=record['payload'];r={**r,'id':record['id']};content=r.get('content',{})
        if 'top_picks' in content or not content.get('top_watch'):continue
        bid=briefings.get(record['id'])
        if not bid or not store.get('briefing_events','telegram:sent:'+bid):continue
        for rank,c in enumerate(content['top_watch'],1):
            p=_legacy_item(r,c,rank)
            if not p or store.get('market_selections',p['pick_id']):continue
            payload={**p,'kind':'daily_top_pick','report_id':record['id'],'briefing_id':bid,'recorded_at':utcnow().isoformat(),
                'original_pick_hash':digest(p),'restored_from_immutable_report':True}
            store.append('market_selections',p['pick_id'],payload,idempotent=True);recovered+=1
    return recovered
