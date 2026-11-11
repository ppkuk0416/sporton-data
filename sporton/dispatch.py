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
    for attempt in range(3):
        try:
            with httpx.Client(timeout=20,follow_redirects=False) as client:
                response=client.get(url,headers={'User-Agent':'SPORTON research/0.3'})
                response.raise_for_status();body=response.json()
            payload={'source_url':url,'collected_at':utcnow().isoformat(),'body':body}
            store.append('raw_data',digest(payload),payload)
            return body
        except Exception:
            if attempt<2:clock.sleep(attempt+1)
    store.append('source_failures',str(uuid4()),{'source_url':url,'at':utcnow().isoformat(),'error':'source unavailable after 3 attempts'})
    raise ValueError('NBA source unavailable after retries')


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
    markets=summary.get('pickcenter') or []
    m=next((m for m in markets if m.get('details')),None)
    if not m:m=next(iter(e['competitions'][0].get('odds',[])),{})
    injuries=[]
    for t in summary.get('injuries',[]):
        for x in t.get('injuries',[]):
            updated=x.get('date')
            if updated and parse_time(updated)>now:continue
            injuries.append({'team':t['team']['abbreviation'],'player':x['athlete']['displayName'],'status':x.get('status','UNKNOWN'),'updated':updated})
    status=e.get('status',{}).get('type',{})
    live=bool(status.get('completed')) or start<=now or status.get('state') in ('in','post')
    return {'id':e['id'],'starts_at':start.isoformat(),'away':a['team']['displayName'],'home':h['team']['displayName'],
        'away_form':af,'home_form':hf,'baseline_scores_away_home':None if live else scores,'already_started':live,
        'market':{'line':m.get('details'),'total':m.get('overUnder'),'bookmaker':m.get('provider',{}).get('name'),'published_at':None},
        'injuries':injuries,'source_url':ESPN+'summary?event='+e['id'],'official_pick':'PASS',
        'baseline_version':'last5-opponent-average-v1','probability':None}


def collect(store,target,now):
    events={};issues=[]
    for d in (target-timedelta(days=1),target):
        try:
            board=fetch(store,ESPN+'scoreboard?dates='+d.strftime('%Y%m%d')+'&limit=100')
            for e in board.get('events',[]):
                if parse_time(e['date']).astimezone(KST).date()==target:events[e['id']]=e
        except ValueError:issues.append('ESPN 일정 조회 실패: '+str(d))
    if not events and issues:raise ValueError('NBA schedule could not be verified')
    official=None
    try:
        official=fetch(store,'https://stats.nba.com/stats/scoreboardv3?GameDate='+str(target-timedelta(days=1))+'&LeagueID=00')
    except ValueError:issues.append('NBA 공식 일정 교차 확인 실패')
    official_games=(official or {}).get('scoreboard',{}).get('games',[])
    def detail(e):
        issue=None
        try:summary=fetch(store,ESPN+'summary?event='+e['id'])
        except ValueError:summary={};issue='경기 상세 수집 실패: '+e['name']
        r=describe(e,summary,now)
        matching=[g for g in official_games if parse_time(g['gameTimeUTC'])==parse_time(e['date'])
            and g['homeTeam']['teamCity']+' '+g['homeTeam']['teamName']==r['home']
            and g['awayTeam']['teamCity']+' '+g['awayTeam']['teamName']==r['away']]
        r['schedule_crosschecked']=len(matching)==1
        return r,issue
    rows=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for r,issue in pool.map(detail,sorted(events.values(),key=lambda e:e['date'])):
            if issue:issues.append(issue)
            if not r['schedule_crosschecked']:issues.append('공식 일정 일치 미확인: '+r['away']+' @ '+r['home'])
            rows.append(r)
    return rows,issues


def prior_audit(store,now):
    """No predictions are invented for the days before automation was installed."""
    prior=[r for r in store.rows('professional_reports') if r['payload'].get('content',{}).get('dispatcher_version')=='nba-daily-v1'
        and r['payload']['content'].get('date_kst')<str(now.astimezone(KST).date())]
    return {'stored_reports':len(prior),'verified_accuracy':None,'note':'사전 원본과 확정 결과를 연결한 채점은 아직 미완료. 적중률·MAE·ROI를 만들어 표시하지 않음.'}


def render(target,now,rows,issues,audit):
    lines=[f'# SPORTON NBA 일일 분석 · {target} (한국시간)',f'생성: {now.astimezone(KST).isoformat()}',
        f'경기 {len(rows)}개 / 시작 전 {sum(not r["already_started"] for r in rows)}개','',
        '## 전일 감사',f'보관된 이전 자동 보고서: {audit["stored_reports"]}개. {audit["note"]}','',
        '## 판단 방법과 한계',
        '점수 시나리오 = (자팀 최근 최대 5경기 득점 평균 + 상대 최근 최대 5경기 실점 평균) / 2.',
        '최소 3경기가 필요하다. 현재 경기 이후 기록은 제외한다. 이는 시계열 외부 검증을 통과한 모델이 아니며 예상 확률·가격 우위를 의미하지 않는다.',
        '결장 영향·휴식·상대 전력·포제션을 수치 보정하지 않았다. 승패 방향과 점수는 탐색적 기준선이며 공식 추천은 PASS다.',
        'ESPN 부상 표기를 사용하며 공식 최종 부상보고서·선발 5인을 확인한 것으로 표시하지 않는다. 구간별 점수를 임의로 나누지 않는다.','']
    for r in rows:
        lines.extend([f'## {parse_time(r["starts_at"]).astimezone(KST):%H:%M} {r["away"]} @ {r["home"]}',
            '일정 교차 확인: '+('NBA 공식·ESPN 일치' if r['schedule_crosschecked'] else '미확인'),
            f'조회 시장: {r["market"]["line"] or "MISSING"} / U/O {r["market"]["total"] if r["market"]["total"] is not None else "MISSING"} · {r["market"]["bookmaker"] or "출처 미확인"} · 원 공개 시각 미확인'])
        scores=r['baseline_scores_away_home']
        if scores:
            away,home_score=scores;lean=r['home'] if home_score>away else r['away'] if away>home_score else '동률'
            lines.append(f'탐색 점수(원정–홈): {away:.1f}–{home_score:.1f}; 홈 마진 {home_score-away:+.1f}; 총점 {away+home_score:.1f}; 기준선 방향: {lean}')
        else:lines.append('점수/승패 판단: '+('이미 시작/종료 → 사전 예측 금지' if r['already_started'] else '최근 결과 표본 부족 → 산출 불가'))
        for label,f in [('원정',r['away_form']),('홈',r['home_form'])]:
            if not f:lines.append(label+' 최근 결과: MISSING');continue
            gap=(parse_time(r['starts_at'])-parse_time(f['last_start'])).total_seconds()/3600
            lines.append(f'{label} 최근 {f["n"]}경기: {f["wins"]}승 {f["n"]-f["wins"]}패, 득점 {f["for"]:.1f}/실점 {f["against"]:.1f}, 직전 경기 시작 간격 {gap:.1f}시간'+(' → 연속 경기 일정 주의' if gap<36 else ''))
        lines.append('주요 명단 표기: '+('; '.join(f'{x["team"]} {x["player"]} {x["status"]}' for x in r['injuries']) or '조회된 명단 없음. 전원 정상 출전을 뜻하지 않음.'))
        lines.extend(['반대 시나리오: 최근 3~5경기 점수는 상대 강도·외곽 성공률에 민감하다. 대체 선수 역할과 선발 확정이 없어 결장 효과를 과대·과소평가할 수 있다.',
            '승패/핸디/UO 공식 추천: PASS. 시장과 기준선 차이만으로 TOP 픽을 생성하지 않음.',r['source_url'],''])
    lines.extend(['## 수집 오류',*(issues or ['없음']), '','## 다음 검증',
        '불변 보고서와 실제 결과를 연결한 감사, 공식 명단·포제션 피처, 후보 모델 외부표본 검증이 후속 과제다. 보고서 생성·자동 전송 성공을 예측 성능 개선으로 간주하지 않는다.'])
    return '\n'.join(lines)


def alert(store,message):
    config=settings()
    if not config:return
    try:
        sent=api(config[0],'sendMessage',data={'chat_id':config[1],'text':message})
        store.append('briefing_events',str(uuid4()),{'action':'nba_dispatch_alert','at':utcnow().isoformat(),'message_id':sent['message_id']})
    except TelegramError:
        store.append('briefing_events',str(uuid4()),{'action':'nba_dispatch_alert_failed','at':utcnow().isoformat()})


def run(store,target=None,now=None,dry_run=False):
    now=now or utcnow();target=target or now.astimezone(KST).date();key='nba-daily-'+str(target)
    with lock():
        if store.get('briefing_events','telegram:sent:'+key):return {'status':'already_sent','date_kst':str(target)}
        if not dry_run and not settings():raise ValueError('Telegram not configured')
        existing=store.get('briefings',key)
        if existing:
            content=existing['snapshot'];record={'id':key,'data_mode':'real','markdown':content['markdown']}
        else:
            rows,issues=collect(store,target,now);audit=prior_audit(store,now)
            markdown=render(target,utcnow(),rows,issues,audit)
            content={'dispatcher_version':'nba-daily-v1','date_kst':str(target),'as_of':utcnow().isoformat(),'games':rows,'issues':issues,'audit':audit,'markdown':markdown}
            # Stable daily ID: retries cannot rewrite the original analysis.
            from .governance import register_report
            if not dry_run:
                rid=register_report(store,'RESEARCH',content=content)
                store.append('briefings',key,{'id':key,'generated_at':utcnow().isoformat(),'scheduled_for':datetime.combine(target,time(6,30),KST).isoformat(),'data_mode':'real','snapshot':content,'markdown':markdown,'research_report_id':rid})
            record={'id':key,'data_mode':'real','markdown':markdown}
        directory=home()/'daily-reports';directory.mkdir(exist_ok=True)
        path=directory/(str(target)+('-preview' if dry_run else '')+'.md');path.write_text(record['markdown'])
        if dry_run:return {'status':'preview','report':str(path)}
        result=deliver(store,record)
        if result['status'] not in ('sent','already_sent'):raise ValueError('Telegram delivery incomplete: '+result['status'])
        late=utcnow().astimezone(KST).date()==target and utcnow().astimezone(KST).time()>time(6,30)
        store.append('briefing_events',str(uuid4()),{'action':'nba_dispatch_complete','at':utcnow().isoformat(),'date_kst':str(target),'late':late,'report_id':key})
        return {'status':result['status'],'date_kst':str(target),'late':late,'report':str(path)}


def next_run(now):
    local=now.astimezone(KST);scheduled=datetime.combine(local.date(),time(6,15),KST)
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
            now=utcnow();local=now.astimezone(KST);due=local.time()>=time(6,15) and (start_date is None or local.date()>=start_date)
            key='nba-daily-'+str(local.date())
            if due and not store.get('briefing_events','telegram:sent:'+key):
                attempts=[e for e in store.rows('briefing_events') if e['payload'].get('action')=='nba_dispatch_failed' and e['payload'].get('date_kst')==str(local.date())]
                if not attempts or (now-parse_time(attempts[-1]['created_at'])).total_seconds()>=300:
                    try:print(json.dumps(run(store),ensure_ascii=False),flush=True)
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
