"""Daily NBA collection, reproducible research, append-only reports and Telegram delivery.

Runs without an LLM. Baseline scores are explicitly unvalidated; no invented model edge.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime,timedelta,time,timezone,date
import fcntl
import json
import os
import math
from pathlib import Path
import signal
import time as clock
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from .contracts import utcnow
from .storage import Store,home,digest
from .telegram import deliver,settings,api,TelegramError

KST=ZoneInfo('Asia/Seoul')
ESPN='https://site.api.espn.com/apis/site/v2/sports/basketball/nba/'
STOP=False


@contextmanager
def lock():
    with (home()/'nba-dispatch.lock').open('a') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('NBA dispatch already running') from None
        try:yield
        finally:fcntl.flock(handle,fcntl.LOCK_UN)


def fetch(store,url):
    """Retry schema/transport failures; use only a bounded, disclosed validated cache."""
    def validate(body):
        if not isinstance(body,dict):raise ValueError('JSON object expected')
        if 'site.api.espn.com' in url:
            if ('scoreboard?' in url or '/schedule?' in url) and not isinstance(body.get('events'),list):raise ValueError('events schema changed')
            if 'summary?' in url and not isinstance(body.get('header'),dict):raise ValueError('summary header schema changed')
        else:
            from .nba_quality import official_games
            official_games(body)
        return body
    error='unknown'
    for attempt in range(3):
        try:
            with httpx.Client(timeout=8,follow_redirects=False) as client:
                headers={'User-Agent':'SPORTON research/0.4'} if 'site.api.espn.com' in url else {'User-Agent':'Mozilla/5.0','Referer':'https://www.nba.com/','Origin':'https://www.nba.com'}
                response=client.get(url,headers=headers)
                response.raise_for_status();body=validate(response.json())
        except Exception as exc:
            error=type(exc).__name__
            if attempt<2:clock.sleep(2**attempt)
            continue
        payload={'source_url':url,'collected_at':utcnow().isoformat(),'body':body,'schema_validated':True}
        store.append('raw_data',digest(payload),payload)
        return body
    store.append('source_failures',str(uuid4()),{'source_url':url,'at':utcnow().isoformat(),'error':error,'status':'SOURCE_UNAVAILABLE','attempts':3})
    age=timedelta(hours=24 if '/schedule?' in url else 1)
    cached=store.latest_source(url)
    for r in ([cached] if cached else []):
        p=r['payload']
        if not p.get('collected_at') or parse_time(p['collected_at'])>utcnow() or utcnow()-parse_time(p['collected_at'])>age:continue
        try:body=validate(p['body'])
        except (ValueError,KeyError,TypeError):continue
        store.append('data_quality_logs',str(uuid4()),{'source_url':url,'state':'CACHED_FALLBACK','snapshot_id':r['id'],'collected_at':p['collected_at'],'at':utcnow().isoformat()})
        return body
    raise ValueError('source unavailable; no fresh schema-valid cache')


def parse_time(value):return datetime.fromisoformat(value.replace('Z','+00:00'))


def baseline(team,start):
    """Observed final scores strictly before this game and before the collection clock."""
    points=[]
    for e in team.get('events',[]):
        at=parse_time(e['gameDate'])
        if at>=start or at>=utcnow() or e.get('gameResult') not in ('W','L'):continue
        h,a=float(e['homeTeamScore']),float(e['awayTeamScore'])
        if not all(math.isfinite(x) and x>=0 for x in (h,a)):continue
        if str(e['homeTeamId'])==str(team['team']['id']):pf,pa=h,a
        elif str(e['awayTeamId'])==str(team['team']['id']):pf,pa=a,h
        else:continue
        points.append((at,pf,pa,e['gameResult']))
    points=sorted(points)[-5:]
    if len(points)<3:return None
    return {'n':len(points),'for':sum(p[1] for p in points)/len(points),'against':sum(p[2] for p in points)/len(points),
        'wins':sum(p[3]=='W' for p in points),'last_start':points[-1][0].isoformat(),
        'source_event_ids':[e['id'] for e in team.get('events',[]) if e.get('id') and parse_time(e['gameDate']) in {p[0] for p in points}]}


def describe(e,summary,now):
    start=parse_time(e['date']);competitors=e['competitions'][0]['competitors']
    h=next(t for t in competitors if t['homeAway']=='home');a=next(t for t in competitors if t['homeAway']=='away')
    forms={str(t['team']['id']):baseline(t,start) for t in summary.get('lastFiveGames',[])}
    hf,af=forms.get(str(h['id'])),forms.get(str(a['id']))
    scores=None
    if hf and af:scores=[round((af['for']+hf['against'])/2,1),round((hf['for']+af['against'])/2,1)]
    from .nba_quality import quoted_prices
    markets=summary.get('pickcenter') or []
    m=next((m for m in markets if m.get('details')),None)
    if not m:m=next(iter(e['competitions'][0].get('odds',[])),{})
    injuries=[]
    for t in summary.get('injuries',[]):
        for x in t.get('injuries',[]):
            updated=x.get('date')
            if updated and parse_time(updated)>now:continue
            injuries.append({'team':t['team']['abbreviation'],'player':x['athlete']['displayName'],'status':x.get('status','UNKNOWN'),'updated':updated})
    status=(e.get('status') or e['competitions'][0].get('status',{})).get('type',{})
    live=bool(status.get('completed')) or start<=now or status.get('state') in ('in','post')
    return {'id':e['id'],'starts_at':start.isoformat(),'away':a['team']['displayName'],'home':h['team']['displayName'],
        'away_form':af,'home_form':hf,'baseline_scores_away_home':None if live else scores,'already_started':live,
        'market':{'line':m.get('details'),'total':m.get('overUnder'),'bookmaker':m.get('provider',{}).get('name'),'published_at':None,'observed_at':utcnow().isoformat(),'home_line':None,'prices':quoted_prices(m),'scope_verified':False},
        'injuries':injuries,'source_url':ESPN+'summary?event='+e['id'],'official_pick':'PASS',
        'baseline_version':'last5-opponent-average-v1','probability':None}


def collect(store,target,now):
    from .nba_quality import official_games,match_schedule,regular,enrich,teams,code
    events={};issues=[];official=[];available=False
    for url in ('https://stats.nba.com/stats/scoreboardv3?GameDate='+str(target-timedelta(days=1))+'&LeagueID=00',
                'https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json'):
        try:
            body=fetch(store,url);all_games=official_games(body)
            official=[g for g in all_games if parse_time(g['gameTimeUTC']).astimezone(KST).date()==target]
            if 'cdn.nba.com' in url and all_games:
                meta=store.latest_source(url)
                if meta:
                    projection=[{k:g[k] for k in ('gameId','gameTimeUTC','homeTeam','awayTeam')} for g in all_games]
                    cache={'source_url':url,'collected_at':meta['payload']['collected_at'],'body_sha256':digest(projection),'games':projection,'max_age_hours':24}
                    temp=home()/'official-schedule-cache.tmp';temp.write_text(json.dumps(cache));temp.replace(home()/'official-schedule-cache.json')
            available=True
            if official:break
        except (ValueError,KeyError,TypeError):issues.append('SOURCE_UNAVAILABLE: '+url.split('/')[2])
    cached_official=None
    if not official:
        from .nba_quality import official_cache
        official,cached_official=official_cache(store,target,now)
        if official:available=True
    for d in (target-timedelta(days=1),target):
        try:
            board=fetch(store,ESPN+'scoreboard?dates='+d.strftime('%Y%m%d')+'&limit=100')
            for e in board.get('events',[]):
                try:
                    if regular(e) and parse_time(e['date']).astimezone(KST).date()==target:
                        teams(e);events[e['id']]=e
                except (ValueError,KeyError,TypeError,StopIteration):issues.append('INVALID_EVENT: '+str(e.get('id')))
        except ValueError:issues.append('ESPN schedule SOURCE_UNAVAILABLE: '+str(d))
    if not events and official:raise ValueError('Official games exist but fallback schedule returned none; schedule conflict')
    if not events and any('ESPN schedule' in i for i in issues):raise ValueError('NBA schedule could not be verified')
    def detail(e):
        issue=None
        try:summary=fetch(store,ESPN+'summary?event='+e['id'])
        except ValueError:summary={};issue='경기 상세 수집 실패: '+e['name']
        r=describe(e,summary,now);state,g=match_schedule(e,official,available)
        if cached_official and state=='VERIFIED':state='PARTIALLY_VERIFIED'
        r['official_cached_evidence']=cached_official if g and cached_official else None
        meta=store.latest_source(ESPN+'summary?event='+e['id'])
        if meta:
            r['source_snapshot_id']=meta['id'];r['source_collected_at']=meta['payload'].get('collected_at')
            r['market']['observed_at']=r['source_collected_at']
        r.update(schedule_status=state,schedule_crosschecked=state=='VERIFIED',venue=e['competitions'][0].get('venue',{}).get('fullName'),schedule_snapshot=digest(e))
        h,a=teams(e)
        if g:
            from .governance import register_alias
            register_alias(store,'fixture','NBA','NBA',str(g['gameId']),e['id'],['NBA schedule and ESPN canonical team/time comparison'])
            r['nba_fixture']={'game_id':g['gameId'],'league':'NBA','season':str(e.get('season',{}).get('year','unknown')),'starts_at':e['date'],
                'home_team':str(g['homeTeam']['teamId']),'away_team':str(g['awayTeam']['teamId'])}
        import re
        match=re.match(r'([A-Za-z]+)\s+([+-]?\d+(?:\.\d+)?)',r['market']['line'] or '')
        if match:
            team,line=code(match[1]),float(match[2])
            if team==code(h['team'].get('abbreviation','')):r['market']['home_line']=line
            elif team==code(a['team'].get('abbreviation','')):r['market']['home_line']=-line
        r=enrich(store,e,r,summary,fetch,now)
        return r,issue
    rows=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for r,issue in pool.map(detail,sorted(events.values(),key=lambda e:e['date'])):
            if issue:issues.append(issue)
            if r['schedule_status']!='VERIFIED':issues.append(r['schedule_status']+': '+r['away']+' @ '+r['home'])
            rows.append(r)
    return rows,issues


def prior_audit(store,now):
    from .nba_quality import audit_predictions
    reports=store.rows('professional_reports');briefings=store.rows('briefings')
    prior=[r for r in reports if r['payload'].get('content',{}).get('date_kst','9999')<str(now.astimezone(KST).date())]
    from .engine import audit as audit_verified_predictions
    from .analytics import audit_result_corrections
    verified_added=audit_verified_predictions(store);audit_result_corrections(store)
    audit=audit_predictions(store,fetch,now)
    audit['new_verified_audits']=len(verified_added)
    from .nba_quality import learning_cycle
    audit['learning_gate']=learning_cycle(store,now)
    research=[r['payload'] for r in store.rows('training_runs') if r['payload'].get('kind')=='NBA_RETROSPECTIVE_COMPARISON']
    reference=Path(__file__).resolve().parents[1]/'docs/validation/nba-three-season-research.json'
    comparison=research[-1] if research else json.loads(reference.read_text()) if reference.exists() else None
    audit['research_comparison']=comparison
    return {**audit,'stored_reports':len(prior),'all_report_count':len(reports),'all_briefing_count':len(briefings),
        'data_home':str(home().resolve()),'database_engine':store.engine.dialect.name,
        'note':'사전 시각·원본 해시·경기 ID가 있는 연구 예측만 감사. 관찰 성적과 공식 가격픽을 분리함. 미등록 과거 채팅은 성적에 합산하지 않음.'}


def top_watch(rows):
    eligible=[]
    for r in rows:
        if r.get('review',{}).get('verdict')!='PASS' or not r.get('baseline_scores_away_home'):continue
        margin=r.get('predicted_margin');line=r.get('market',{}).get('home_line')
        if margin is None or line is None or abs(margin+line)<=2:continue
        if r.get('analysis_level')=='INSUFFICIENT_DATA':continue
        observed=r.get('market',{}).get('observed_at')
        if not observed or (utcnow()-parse_time(observed)).total_seconds()>900:continue
        eligible.append({'game_id':r['id'],'match':r['away']+' @ '+r['home'],'selection':'home spread' if margin+line>0 else 'away spread',
            'home_line':line,'scenario_gap':abs(margin+line),'class':'WATCH','meaning':'unvalidated scenario discrepancy, not verified EV','observed_at':r['market'].get('observed_at')})
    return sorted(eligible,key=lambda r:r['scenario_gap'],reverse=True)[:3]


def render(target,now,rows,issues,audit,run_state=None,origin=None):
    scheduled=datetime.combine(target,time(6,30),KST)
    pre=[r for r in rows if not r['already_started']];valid=[r for r in pre if r.get('baseline_scores_away_home')]
    lines=[f'# SPORTON NBA 분석 · {target} ({["월","화","수","목","금","토","일"][target.weekday()]}요일)',
        '## 1. 운영 상태',f'예정: {scheduled.isoformat()} / 실제 생성: {now.astimezone(KST).isoformat()}',
        f'실행 구분: {origin or os.getenv("GITHUB_EVENT_NAME","manual")} / 전체 {len(rows)}경기, 시작 전 {len(pre)}, 사전 수치 생성 {len(valid)}/{len(pre)}',
        f'최근 전력 이력 {sum(bool(r.get(s+"_form")) for r in rows for s in ("home","away"))}/{2*len(rows)}팀; 효율 기록 {sum(bool(r.get("efficiency",{}).get(s)) for r in rows for s in ("home","away"))}/{2*len(rows)}팀; 공식 일정 일치 {sum(r.get("schedule_status")=="VERIFIED" for r in rows)}/{len(rows)}경기',
        f'분석 상태: {run_state or "COMPLETED_PARTIAL"} · 연구 기준선과 검증 모델을 구분함','',
        '## 2. 전일 감사',f'현재 DB: 보고서 {audit.get("all_report_count",audit.get("stored_reports",0))}개 / 브리핑 {audit.get("all_briefing_count",0)}개 / 이전 날짜 보고서 {audit.get("stored_reports",0)}개',
        audit.get('note',''),'관찰 감사: '+str({k:audit.get(k) for k in ('n','direction_wins','direction_n','accuracy','team_mae','margin_mae','total_mae')}),
        f'공식 가격픽 {audit.get("official_priced_n",0)}개; ROI {audit.get("roi")}; 확률 교정·구간 성적은 원본이 없으면 미산출.',
        '큰 마진 오차 3건: '+str(audit.get('worst_three',[])),'',
        '## 3. 오류와 절차 개선',
        '종료 후 사라지는 summary 최근 5경기를 팀 시즌 일정으로 복구. 최근 5/10/시즌 및 홈·원정을 분리. 공급원 장애와 일정 충돌을 구분.',
        '관측 기록의 공개 시각이 없어 자동 학습 승격하지 않음. 학습 가중치 임의 변경 없음. 개선 효과는 외부표본 검증 전까지 미입증.','',
        '## 4. 전 경기 분석']
    for r in rows:
        lines.extend([f'### {parse_time(r["starts_at"]).astimezone(KST):%H:%M} {r["away"]} @ {r["home"]}',
            f'장소: {r.get("venue") or "미확인"} / {"사후 감사 대상" if r["already_started"] else "시작 전"} / 일정: {r.get("schedule_status","PARTIALLY_VERIFIED")}',
            f'분석: {r.get("analysis_level","PARTIAL_ANALYSIS")} / 시장 판단: {r.get("recommendation_status","NO_BET")} / 모델: {r.get("model_version","baseline")}',
            f'시장: {r["market"]["line"] or "미확인"}, U/O {r["market"]["total"]} · {r["market"]["bookmaker"] or "업체 미확인"} · 조회 {r["market"].get("observed_at")} (원 공개 시각 미확인)'])
        cache=r.get('official_cached_evidence')
        if cache:lines.append('공식 일정 캐시·실시간 ESPN의 팀/시각 일치. 공식 캐시 수집: '+cache['collected_at']+'; 공식 실시간 확인은 아님.')
        prices=r.get('market',{}).get('prices',{})
        if prices:lines.append('조회 배당(십진): '+', '.join(k+' '+str(v['decimal']) for k,v in prices.items() if v.get('decimal') is not None))
        scores=r.get('baseline_scores_away_home')
        if scores:
            a,h=scores;lean=r['home'] if h>a else r['away'] if a>h else '동률'
            lines.append(f'예상 시나리오(원정–홈): {a:.1f}–{h:.1f}; 홈 마진 {h-a:+.1f}; 총점 {h+a:.1f}; 방향 {lean}')
            line=r['market'].get('home_line');total=r['market'].get('total')
            if line is not None:lines.append(f'핸디 비교: 홈 {line:+.1f}, 시나리오 잔차 {h-a+line:+.1f} → '+('홈 방향' if h-a+line>0 else '원정 방향' if h-a+line<0 else '중립'))
            if total is not None:lines.append('총점 비교: '+('OVER' if h+a>total else 'UNDER' if h+a<total else '중립')+f' (차이 {h+a-total:+.1f})')
        else:lines.append('사전 수치 미생성: '+('이미 시작/종료' if r['already_started'] else '최소 근거 부족'))
        for side,label in [('away','원정'),('home','홈')]:
            f=r.get(side+'_form')
            if not f:continue
            windows=[]
            for k,title in [('last10','최근10'),('season','시즌'),('home','홈'),('away','원정')]:
                w=f.get(k)
                if w:windows.append(f'{title} {w["n"]}경기 득 {w["for"]:.1f}/실 {w["against"]:.1f}')
            lines.append(f'{label} 최근 {f["n"]}경기 {f["wins"]}승 / 득 {f["for"]:.1f}, 실 {f["against"]:.1f}; '+ '; '.join(windows))
            ef=r.get('efficiency',{}).get(side)
            if ef:
                m=ef['metrics'];lines.append(f'파생 효율({ef["n"]}경기): ORtg {m["ortg"]:.1f}, DRtg {m["drtg"]:.1f}, Net {m["net_rtg"]:+.1f}, Pace {m["pace"]:.1f}, eFG {m["efg_pct"]:.3f}, TS {m["ts_pct"]:.3f}, 3PAr {m["three_par"]:.3f}, FTr {m["ftr"]:.3f}, TOV {m["tov_pct"]:.3f}; 공식 포제션·추적 수치 아님')
            gap=(parse_time(r['starts_at'])-parse_time(f['last_start'])).total_seconds()/3600
            lines.append(f'{label} 직전 시작 간격 {gap:.1f}시간'+(' (연속 경기)' if gap<36 else ''))
        injuries=r.get('injuries',[])
        if injuries:lines.append('선수 변수(ESPN 표기): '+'; '.join(f'{i["team"]} {i["player"]} {i["status"]}' for i in injuries))
        review=r.get('review',{})
        lines.extend(['주요 반대 근거: '+' / '.join(review.get('counter_evidence',[])[:4]),r['source_url'],''])
    comparison=audit.get('research_comparison')
    if comparison:
        lines.extend(['### 회고 연구 모델 비교',f'3시즌 {comparison["source_games"]}경기, {comparison["usable_samples"]}샘플. 시간순 학습·교정·검증 분리, 실전 검증/자동 승격 아님.'])
        fold=comparison['folds'][-1]
        for name,m in fold['metrics'].items():lines.append(f'{name}: 외부 구간 {m["n"]}경기, 팀 득점 MAE {m["score_mae"]:.2f}, 마진 MAE {m["margin_mae"]:.2f}, 총점 MAE {m["total_mae"]:.2f}, Brier {m["brier"]:.3f}')
        lines.append('공개 아카이브의 최초 정보 공개 시각 미확보. 연구 개선을 실전 성능 개선으로 인정하지 않음. 현재 학습 게이트: '+str(audit.get('learning_gate',{}).get('state','미확인')))
    lines.extend(['## 5. 독립 심사', '코드 기반 독립 원자료/시각/수치 검사를 실행. 전문가 LLM 심사는 미연결이며 AI 검토 완료로 표시하지 않음.'])
    for r in rows:
        v=r.get('review',{});lines.append(f'{r["away"]} @ {r["home"]}: {v.get("verdict","NO")} — '+ '; '.join(v.get('reasons',['심사 기록 없음'])))
    lines.extend(['','## 6. TOP 관찰 후보', '공식 가격픽은 별도 자격 미충족으로 0개. 아래는 검증되지 않은 시나리오와 시장선 차이가 2점을 초과한 WATCH이며 수익 우위가 아니다.'])
    top=top_watch(rows)
    lines.extend([f'{i+1}. {t["match"]}: {t["selection"]}, 홈 기준 {t["home_line"]:+.1f}, 시나리오 차이 {t["scenario_gap"]:.1f}점 / WATCH / 조회 {t["observed_at"]}' for i,t in enumerate(top)] or ['해당 기준을 충족한 관찰 후보 없음. 강제 선정하지 않음.'])
    lines.extend(['','## 7. DB·다음 감사','원본 저장/예측 ID/심사 기록은 보고서 저장 트랜잭션과 연결된다. 시작 후 기록은 사전 성적에서 제외.',
        '공식 포제션·Usage·On/Off·확정 선발·선수별 이동/출전시간 및 구간 모델이 미확보이므로 FULL_ANALYSIS로 표시하지 않음.',
        '점수는 포제션 기반 파생 효율 Baseline(양 팀 3경기 이상) 또는 최근 득실점 비교 Baseline을 명시한다. 공식 추적 데이터와 구분한다. 모든 점수 시나리오는 비교용 Baseline이다. 검증된 Champion이 있을 때만 기존 정량 엔진을 사용한다.','수집 이슈: '+('; '.join(issues) or '없음')])
    return '\n'.join(lines)


def alert(store,message):
    config=settings()
    if not config:return
    try:
        sent=api(config[0],'sendMessage',data={'chat_id':config[1],'text':message})
        store.append('briefing_events',str(uuid4()),{'action':'nba_dispatch_alert','at':utcnow().isoformat(),'message_id':sent['message_id']})
    except TelegramError:
        store.append('briefing_events',str(uuid4()),{'action':'nba_dispatch_alert_failed','at':utcnow().isoformat()})


def job_event(store,job_id,state,phase,**extra):
    payload={'job_id':job_id,'state':state,'phase':phase,'at':utcnow().isoformat(),**extra}
    store.append('job_executions',str(uuid4()),payload)


def _run(store,target,now,dry_run,job_id,origin):
    from .nba_quality import VERSION,save_predictions
    key=VERSION+'-'+str(target)
    if origin!='schedule':key+='-manual-'+os.getenv('GITHUB_RUN_ID','local')
    with lock():
        if not dry_run and store.get('briefing_events','telegram:sent:'+key):return {'status':'already_sent','date_kst':str(target)}
        if not dry_run and not settings():raise ValueError('Telegram not configured')
        existing=store.get('briefings',key)
        if existing:
            content=existing['snapshot'];record={'id':key,'data_mode':'real','markdown':content['markdown']}
        else:
            job_event(store,job_id,'RUNNING','collection_started')
            rows,issues=collect(store,target,now)
            job_event(store,job_id,'RUNNING','collection_completed',games=len(rows),issues=len(issues))
            audit=prior_audit(store,utcnow())
            # Collection may cross tipoff. Recheck the actual save clock, not the requested clock.
            generated=utcnow()
            for r in rows:
                if parse_time(r['starts_at'])<=generated:
                    r['already_started']=True;r['baseline_scores_away_home']=None
                    r['recommendation_status']='NO_BET'
                    if r.get('review'):r['review']={**r['review'],'verdict':'NO','reasons':['Already started at analysis completion; audit only.']}
            pre=[r for r in rows if not r['already_started']]
            valid=[r for r in pre if r.get('baseline_scores_away_home') and r.get('review',{}).get('verdict','PASS')=='PASS']
            state='COMPLETED' if valid and len(valid)==len(pre) and not issues and all(r.get('analysis_level')=='FULL_ANALYSIS' for r in valid) else 'COMPLETED_PARTIAL'
            if pre and not valid:state='FAILED'
            if not rows and not dry_run:
                job_event(store,job_id,'COMPLETED','no_games_no_notification')
                return {'status':'no_games','date_kst':str(target)}
            markdown=render(target,generated,rows,issues,audit,run_state=state,origin=origin)
            markdown+='\n\n보고서 실행 ID: '+job_id+' / 원본 ID: '+key+' / 저장 시 검증할 SHA256: '+digest({'games':rows,'as_of':generated.isoformat()})
            content={'dispatcher_version':VERSION,'date_kst':str(target),'as_of':generated.isoformat(),'origin':origin,'job_id':job_id,
                'run_state':state,'scheduled_for':datetime.combine(target,time(6,30),KST).isoformat(),
                'games':rows,'issues':issues,'audit':audit,'top_watch':top_watch(rows),'markdown':markdown}
            job_event(store,job_id,'RUNNING','analysis_completed',result_state=state,pregame_count=len(pre),analysis_count=len(valid))
            from .governance import register_report
            if not dry_run:
                with store.transaction() as conn:
                    rid=register_report(store,'RESEARCH',content=content,conn=conn)
                    store.append('briefings',key,{'id':key,'generated_at':generated.isoformat(),'scheduled_for':content['scheduled_for'],
                        'data_mode':'real','snapshot':content,'markdown':markdown,'research_report_id':rid},conn=conn)
                    save_predictions(store,rows,rid,generated.isoformat(),conn,origin=origin)
                job_event(store,job_id,'RUNNING','report_stored',report_id=rid,content_hash=digest(content))
            record={'id':key,'data_mode':'real','markdown':markdown}
        directory=home()/'daily-reports';directory.mkdir(exist_ok=True)
        path=directory/(str(target)+('-preview' if dry_run else '')+'.md');path.write_text(record['markdown'])
        if dry_run:return {'status':'preview','report':str(path),'run_state':content['run_state']}
        if content['run_state']=='FAILED':raise ValueError('All pregame analyses lack minimum evidence; partial failure recorded, no normal report sent')
        result=deliver(store,record)
        if result['status'] not in ('sent','already_sent'):raise ValueError('Telegram delivery incomplete: '+result['status'])
        job_event(store,job_id,content['run_state'],'delivery_completed',report_id=key)
        late=utcnow()>datetime.combine(target,time(6,30),KST)
        store.append('briefing_events',str(uuid4()),{'action':'nba_dispatch_complete','at':utcnow().isoformat(),'date_kst':str(target),'late':late,'origin':origin,'report_id':key})
        return {'status':result['status'],'date_kst':str(target),'late':late,'origin':origin,'run_state':content['run_state'],'report':str(path)}


def run(store,target=None,now=None,dry_run=False,origin=None):
    now=now or utcnow();target=target or now.astimezone(KST).date();origin=origin or os.getenv('GITHUB_EVENT_NAME','manual')
    job_id=str(uuid4());scheduled=datetime.combine(target,time(6,30),KST)
    job_event(store,job_id,'SCHEDULED','created',scheduled_for=scheduled.isoformat(),origin=origin,dry_run=dry_run)
    receipt=store.get('briefing_events','telegram:sent:nba-daily-v2-'+str(target))
    previous_jobs=store.rows('job_executions')
    timely_ids={r['payload']['job_id'] for r in previous_jobs if r['payload'].get('phase')=='created'
        and r['payload'].get('origin')=='schedule' and r['payload'].get('scheduled_for')==scheduled.isoformat()
        and parse_time(r['payload']['at'])<=scheduled+timedelta(minutes=15)}
    timely_started=any(r['payload'].get('job_id') in timely_ids and r['payload'].get('phase')=='execution_started' for r in previous_jobs)
    if origin=='schedule' and now>scheduled+timedelta(minutes=15) and not receipt and not timely_started:
        job_event(store,job_id,'MISSED_SCHEDULE','delayed_scheduled_invocation',scheduled_for=scheduled.isoformat(),actual=now.isoformat(),
            note='Observed delayed invocation; not proof that an earlier independent run failed.')
    job_event(store,job_id,'RUNNING','execution_started',origin=origin)
    try:
        result=_run(store,target,now,dry_run,job_id,origin)
        job_event(store,job_id,result.get('run_state','COMPLETED'),'execution_finished',outcome=result['status'])
        return result
    except Exception as exc:
        job_event(store,job_id,'FAILED','execution_failed',error_type=type(exc).__name__)
        raise


def next_run(now):
    local=now.astimezone(KST);scheduled=datetime.combine(local.date(),time(6,30),KST)
    return scheduled if local<scheduled else scheduled+timedelta(days=1)


def daemon(store,start_date=None):
    global STOP
    def stop(*args):
        global STOP
        STOP=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    with (home()/'nba-daemon.lock').open('a') as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('NBA daemon already running') from None
        while not STOP:
            now=utcnow();local=now.astimezone(KST);due=local.time()>=time(6,30) and (start_date is None or local.date()>=start_date)
            key='nba-daily-v2-'+str(local.date())
            if due and not store.get('briefing_events','telegram:sent:'+key):
                attempts=[e for e in store.rows('briefing_events') if e['payload'].get('action')=='nba_dispatch_failed' and e['payload'].get('date_kst')==str(local.date())]
                if not attempts or (now-parse_time(attempts[-1]['created_at'])).total_seconds()>=300:
                    try:print(json.dumps(run(store,origin='schedule'),ensure_ascii=False),flush=True)
                    except Exception:
                        store.append('briefing_events',str(uuid4()),{'action':'nba_dispatch_failed','date_kst':str(local.date()),'at':utcnow().isoformat()})
                        if not attempts:alert(store,'SPORTON: 오늘 NBA 자동 보고서 생성/전송이 실패해 5분 간격으로 재시도합니다. 실행 로그를 확인하세요.')
            state={'heartbeat':utcnow().isoformat(),'pid':os.getpid(),'next_run_kst':next_run(utcnow()).isoformat(),'scope':'current_cloud_environment_only'}
            temp=home()/'nba-daemon.tmp';temp.write_text(json.dumps(state));temp.replace(home()/'nba-daemon.json')
            clock.sleep(20)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--daemon',action='store_true');parser.add_argument('--dry-run',action='store_true');parser.add_argument('--date',type=date.fromisoformat)
    args=parser.parse_args();s=Store()
    try:
        if args.daemon:daemon(s,args.date)
        else:print(json.dumps(run(s,args.date,dry_run=args.dry_run),ensure_ascii=False))
    except Exception:
        if not args.dry_run:alert(s,'SPORTON: NBA 보고서 자동 실행에 실패했습니다. 저장된 기록으로 재시도하며, 분석 완료로 표시하지 않습니다.')
        raise SystemExit('NBA dispatch failed; see redacted source_failures/briefing_events') from None

if __name__=='__main__':main()
