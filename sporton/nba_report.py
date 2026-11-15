"""Reader-first NBA report. Presentation only: source dictionaries remain untouched."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import html
import json
import re
from .daily_picks import TYPES,name,pick_label,select_picks,reasons_and_risks,finite
from .nba_quality import dt

KST=ZoneInfo('Asia/Seoul')
STATUS={'W':'적중','L':'미적중','Push':'무승부','PENDING':'결과 확인 중','CONDITION_UNCONFIRMED':'조건 충족 미확인 · 성적 제외'}
MODELS={'recent_average':'최근 경기 평균','elo':'Elo·선형 회귀','logistic':'로지스틱·선형 회귀','lightgbm':'LightGBM','pytorch_mlp':'PyTorch MLP'}
METRICS={'ortg':'공격 효율 ORtg','drtg':'수비 효율 DRtg','net_rtg':'공수 차이 NetRtg','pace':'경기 속도 Pace',
    'efg_pct':'유효 야투 eFG%','ts_pct':'슈팅 효율 TS%','three_par':'3점 시도 비중 3PAr','ftr':'자유투 비중 FTr','oreb_pct':'공격 리바운드 OREB%',
    'dreb_pct':'수비 리바운드 DREB%','tov_pct':'턴오버 비중 TOV%'}


def num(v,digits=1):return f'{v:.{digits}f}' if finite(v) else '미확인'
def signed(v):return f'{v:+g}' if finite(v) else '미확인'
def odds(v):return f'{v:.2f}배' if finite(v) else '배당 미확인'
def pct(v):return f'{v*100:.1f}%' if finite(v) else '미계산'
def local(v,full=False):return dt(v).astimezone(KST).strftime('%m/%d %H:%M' if full else '%H:%M') if v else '미확인'
def cell(v):return str(v if v is not None else '미확인').replace('|','／').replace('\n',' ')
def table(headers,rows):return ['| '+' | '.join(map(cell,headers))+' |','| '+' | '.join('---' for _ in headers)+' |']+['| '+' | '.join(map(cell,r))+' |' for r in rows]
def match(row):return name(row['away'])+' @ '+name(row['home'])


def topic(team):
    word=name(team);last=ord(word[-1]);closed=0xAC00<=last<=0xD7A3 and (last-0xAC00)%28!=0
    return word+('은' if closed else '는')


def plain(text):
    return str(text).replace('예측 오차는 확인됐으나 부상·로테이션 등의 인과 원인은 아직 미확인','예상과 실제 결과의 차이는 확인했습니다. 다만 부상이나 출전시간 때문에 생긴 차이인지까지는 아직 확인하지 못했습니다').replace('실제 eFG','실제 슛 효율(eFG)').replace('TOV','공을 잃은 공격 비율(TOV)').replace('검증된 실전 예측모델이 아닌 기준선 시나리오','최근 경기 자료를 바탕으로 계산한 예상이어서 실제 경기에서는 오차가 클 수 있습니다.').replace('독립 검사의 반대 근거와 최종 명단을 재확인해야 함','최종 출전 명단에 따라 판단이 바뀔 수 있습니다.').replace('unvalidated scenario discrepancy, not verified EV','당시 예상 점수와 기준점의 차이를 근거로 골랐습니다. 실제 수익성이 검증된 선택은 아닙니다.')


def pick_reason(p):
    a,h=p['expected_scores'];margin=h-a
    winner=name(p['home'] if margin>0 else p['away'])
    forecast=f'{winner}의 {abs(margin):g}점 차 승리를 예상합니다.' if margin else '예상 점수는 동점으로, 승패는 박빙으로 봅니다.'
    line=p.get('line');kind=p['market_type']
    if p['pick_type']=='CONDITIONAL':
        return forecast+' 실제 기준점은 확인하지 못했습니다. 적어 둔 조건이 경기 전에 확인될 때만 검토하는 선택입니다.'
    if kind=='spread' and finite(line):
        team=name(p['team'])
        rule=(f'선택한 팀이 지더라도 점수 차가 {line:g}점보다 작으면 적중합니다.' if line>0 else
              f'선택한 팀이 {abs(line):g}점보다 큰 차이로 이겨야 적중합니다.' if line<0 else '선택한 팀이 이겨야 적중합니다.')
        selected=margin if p['selection']=='home' else -margin
        return forecast+' '+rule+f' 예상대로라면 이 기준보다 {selected+line:g}점 여유가 있습니다.'
    if kind=='total':
        direction='높아' if p['selection']=='over' else '낮아'
        return f"두 팀 합계는 {a+h:g}점으로 예상합니다. 조회한 기준 {line:g}점보다 {abs(a+h-line):g}점 {direction} {'오버' if p['selection']=='over' else '언더'}를 골랐습니다."
    return forecast+' 점수 차보다 승리팀 방향에 초점을 맞춘 선택입니다.'


def top_score(top):
    previous=top.get('previous',[])
    if not previous:return '확인할 수 있는 직전 추천 원본이 없어 성적을 합산하지 않았습니다.'
    wins=sum(e['status']=='W' for e in previous);losses=sum(e['status']=='L' for e in previous);push=sum(e['status']=='Push' for e in previous)
    pending=len(previous)-wins-losses-push
    return f"{len(previous)}개 중 {wins}개 적중 · {losses}개 미적중 · {push}개 무승부"+(f' · {pending}개 판정 대기' if pending else '')


def _pick_cards(picks):
    lines=[]
    for p in picks:
        lines.extend([f"### {p['rank']}. {p['label']}",f"{match(p)} · {local(p['starts_at'])} 한국시간",''])
        lines.append('**이 픽을 고른 이유:** '+pick_reason(p))
        a,h=p['expected_scores'];lines.append(f"**예상 점수:** {name(p['away'])} {a:g}–{h:g} {name(p['home'])} · 합계 {a+h:g}점")
        if p['pick_type']=='CONDITIONAL':lines.append('**선택 조건:** '+p['label']+' — 실제 시장선이 아닌 자체 검토 기준입니다.')
        else:lines.append(f"**확인한 가격:** {p['label']} / {odds(p.get('odds'))} · {p.get('bookmaker') or '업체 미확인'} · 조회 {local(p.get('observed_at'),True)} 한국시간")
        lines.extend(['**틀릴 수 있는 가장 큰 이유:** '+plain(p['risks'][0]),f"**신뢰도:** {p['confidence']} · **분류:** {TYPES[p['pick_type']]}",''])
    return lines


def _previous(top):
    previous=top.get('previous',[]);lines=['**직전 추천 성적: '+top_score(top)+'**','']
    if not previous:return lines+['원본을 확인하지 못한 이전 채팅의 선택은 채점에 넣지 않습니다.','']
    lines.append(f"대상 보고서: {local(previous[0]['pick']['created_at'],True)} 한국시간. 당시 선택과 기준점은 바꾸지 않았습니다.")
    for e in previous:
        p=e['pick'];r=e.get('result');f=e.get('feedback')
        lines.extend(['',f"### {p['rank']}. {p['label']} — {STATUS[e['status']]}",
            f"**당시 선택:** {p['label']} / {odds(p.get('odds'))} · {TYPES[p['pick_type']]}",
            '**왜 골랐나:** '+pick_reason(p)])
        if r:
            actual=f"{name(p['away'])} {num(r['away_score'],0)}–{num(r['home_score'],0)} {name(p['home'])}"
            lines.append('**실제 결과:** '+actual+' · '+STATUS[e['status']])
            a,h=p['expected_scores'];ap,hp=r['away_score'],r['home_score']
            lines.append(f"**예상과 달랐던 점:** {topic(p['away'])} 예상 {a:g}점보다 {abs(ap-a):g}점 "+('더 넣었고' if ap>=a else '덜 넣었고')+f", {topic(p['home'])} 예상 {h:g}점보다 {abs(hp-h):g}점 "+('더 넣었습니다.' if hp>=h else '덜 넣었습니다.'))
        else:lines.append('**실제 결과:** 최종 점수를 아직 확인하지 못해 판정을 기다리고 있습니다.')
        if f:
            lines.append('**결과를 어떻게 볼까:** '+plain(f['judgement'])+'. '+plain(f['process_warning'])+'.')
            if f.get('facts'):lines.append('확인한 내용: '+' / '.join(plain(x) for x in f['facts']))
            lines.append('**다음 분석에서 확인할 점:** '+plain(f['next_check']))
        else:lines.append('**다음 분석에서 확인할 점:** 최종 점수와 선수 출전 기록을 확인한 뒤 복기하겠습니다. 아직 원인을 단정하지 않겠습니다.')
        if e['status']=='CONDITION_UNCONFIRMED':lines.append('경기 전 조건 충족 여부를 확인하지 못했으므로, 결과가 있어도 승패 집계에서 제외합니다.')
    if top.get('all_win'):lines+=['','추천한 '+str(len(previous))+'개가 모두 적중했습니다. 다만 적중만으로 분석 과정까지 정확했다고 보지는 않습니다.']
    return lines+['']


def _pick_records(top):
    lines=['### 누적 추천 성적 — 유형별로 따로 집계','']
    for kind,label in TYPES.items():
        g=top.get('groups',{}).get(kind,{})
        lines.append(f"- {label}: {g.get('wins',0)}승 {g.get('losses',0)}패 {g.get('pushes',0)}무 · 판정 {g.get('settled',0)}개 · 적중률 {pct(g.get('accuracy'))}")
        if kind=='CONDITIONAL':lines.append(f"조건 충족 미확인 {g.get('condition_unconfirmed',0)}개는 승패에서 제외했습니다.")
        if kind=='OFFICIAL_PRICE':lines.append(f"동일 1단위 기준 참고 수익률 {pct(g.get('roi'))}. 실제 투자 손익은 아닙니다." if g.get('roi') is not None else '집계할 수 있는 공식 가격픽이 없어 수익률은 아직 계산하지 않았습니다.')
    return lines+['관찰 픽과 조건부 픽의 성적을 실제 체결 수익률로 표시하지 않습니다.','']


def _all_game_feedback(audit,reference):
    rows=audit.get('all_evaluations') or audit.get('evaluations') or []
    rows=[e for e in rows if e.get('report_id')==reference] if reference else []
    # Within one original report each game appears once; manual/scheduled/model records are not pooled.
    distinct={e['game_id']:e for e in rows};rows=list(distinct.values())
    lines=['','### 전 경기 예상 오차 — TOP 픽과 별도 평가']
    if rows:
        lines.append(f"사전 원본이 있는 {len(rows)}경기 · 팀 득점 MAE {sum(e['team_mae'] for e in rows)/len(rows):.2f}점 · 마진 MAE {sum(e['margin_mae'] for e in rows)/len(rows):.2f}점 · 총점 MAE {sum(e['total_mae'] for e in rows)/len(rows):.2f}점")
        for e in sorted(rows,key=lambda e:e['margin_mae'],reverse=True):
            game=name(e.get('away_name'))+' @ '+name(e.get('home_name')) if e.get('away_name') and e.get('home_name') else '경기 '+e['game_id']
            p=e.get('predicted_scores');r=e.get('actual_scores')
            lines.extend([f'**{game}**',f"예상 {num(p[0])}–{num(p[1])} → 실제 {num(r[0],0)}–{num(r[1],0)} · 승리 방향 "+('적중' if e.get('direction_correct') is True else '미적중' if e.get('direction_correct') is False else '동률 예상으로 미집계')+
                f" · 마진 오차 {num(e['margin_mae'])}점 / 총점 오차 {num(e['total_mae'])}점"])
            lines.append(f'홈 마진 예상 {p[1]-p[0]:+g} → 실제 {r[1]-r[0]:+g} / 총점 예상 {sum(p):g} → 실제 {sum(r):g}')
            if e.get('half_metrics'):
                half=e['half_metrics'];lines.append(f"전반 예상 {num(half['predicted_scores'][0])}–{num(half['predicted_scores'][1])} → 실제 {num(half['actual_scores'][0],0)}–{num(half['actual_scores'][1],0)} · 팀 득점 MAE {num(half['team_mae'])} / 마진 MAE {num(half['margin_mae'])} / 총점 MAE {num(half['total_mae'])}")
            if e.get('segment_metrics'):lines.append('구간 오차: '+', '.join(f"{s['period']}Q "+(f"예상 {num(s['predicted_scores'][0])}–{num(s['predicted_scores'][1])} → 실제 {num(s['actual_scores'][0],0)}–{num(s['actual_scores'][1],0)} · " if s.get('predicted_scores') else '')+f"팀 득점 MAE {num(s['team_mae'])} / 마진 {num(s['margin_mae'])} / 총점 {num(s['total_mae'])}" for s in e['segment_metrics']))
        by_team={}
        history={e['game_id']:e for e in audit.get('evaluations',rows)}
        for e in history.values():
            for side in ('away','home'):
                if finite(e.get(side+'_score_error')):by_team.setdefault(e.get(side+'_name') or e['game_id']+side,[]).append(e[side+'_score_error'])
        repeated=[(t,v) for t,v in by_team.items() if len(v)>=3 and (all(x>0 for x in v) or all(x<0 for x in v))]
        if repeated:lines.append('누적 사전 예측의 반복 점검 대상: '+', '.join(f'{name(t)} {len(v)}경기 연속 '+('과대 예상' if v[0]>0 else '과소 예상') for t,v in repeated)+'. 표본이 작아 확정 패턴으로 보지는 않습니다.')
        else:lines.append('같은 팀의 반복 과대·과소 예상을 확정할 표본은 아직 부족합니다.')
    else:lines.append('이 보고서와 연결해 확인할 전 경기 사전 원본·최종 결과가 아직 없습니다. 점수 오차를 임의로 채우지 않았습니다.')
    lines.append('쿼터·전반 사전 예측이 없었던 경기는 해당 구간 MAE를 산출하지 않습니다. 단일 경기로 모델 가중치를 변경하지 않습니다.')
    return lines


def _game(row):
    lines=[f"### {match(row)} · {local(row['starts_at'])} 한국시간"]
    scores=row.get('baseline_scores_away_home');m=row.get('market',{})
    if row.get('already_started'):return lines+['이미 시작한 경기이므로 새 사전 예측을 만들지 않았습니다.','']
    if not scores:return lines+['경기나 전력 자료가 부족해 승리팀과 점수를 판단하기 어렵습니다.','']
    a,h=scores;margin=h-a;total=a+h;winner=name(row['home'] if h>a else row['away'])
    lines.extend([f"**예상 점수: {name(row['away'])} {a:g}–{h:g} {name(row['home'])}**",'',
        f'승리팀은 **{winner}**, 점수 차는 **{abs(margin):g}점**으로 봅니다.' if margin else '예상 점수는 동점입니다. 어느 팀이 이길지 우열을 가리기 어렵습니다.'])
    af=row.get('away_form');hf=row.get('home_form')
    if af and hf:
        lines.append(f"{topic(row['away'])} 최근 {af['n']}경기에서 {af['wins']}승, {topic(row['home'])} 최근 {hf['n']}경기에서 {hf['wins']}승을 기록했습니다. 같은 기간 평균 득점은 각각 {af['for']:.1f}점과 {hf['for']:.1f}점입니다.")
        an=af['for']-af['against'];hn=hf['for']-hf['against']
        if an!=hn:lines.append(f"최근 경기에서 넣은 점수와 내준 점수의 차이는 {name(row['away'] if an>hn else row['home'])} 쪽이 더 좋았습니다. 다만 상대했던 팀의 수준까지 같지는 않아 이 숫자만으로 승패를 정하지는 않습니다.")
    if finite(m.get('home_line')):
        line=m['home_line'];edge=margin+line
        side='home' if edge>0 else 'away';selected_line=line if side=='home' else -line
        lines.append(f"**핸디캡:** {name(row[side])} {selected_line:+g} 쪽을 봅니다. 예상 점수대로라면 기준점을 적용했을 때 {abs(edge):g}점 여유가 있습니다." if edge else '**핸디캡:** 예상 점수와 기준점이 맞닿아 있어 어느 쪽도 뚜렷하게 유리하지 않습니다.')
    else:lines.append('**핸디캡:** 실제 기준점을 확인하지 못했습니다. 점수 차 예상과 실제 가격을 비교한 뒤 판단해야 합니다.')
    if finite(m.get('total')):
        line=m['total'];direction='오버' if total>line else '언더'
        lines.append(f"**총점:** 두 팀 합계는 {total:g}점으로 예상합니다. 확인한 기준 {line:g}점보다 {'높아' if total>line else '낮아'} **{direction}** 쪽을 봅니다." if total!=line else f'**총점:** 예상 합계와 기준점이 모두 {total:g}점으로, 어느 쪽도 뚜렷하게 유리하지 않습니다.')
    else:lines.append(f'**총점:** 합계 {total:g}점으로 봅니다. 실제 시장 기준점은 확인하지 못했습니다.')
    _,risks=reasons_and_risks(row)
    lines.extend(['','**예상이 틀어질 수 있는 변수:** '+plain(risks[0])])
    if len(risks)>1:lines.append(plain(risks[1]))
    if row.get('schedule_status')=='SOURCE_UNAVAILABLE':lines.append('공식 일정 자료를 불러오지 못해 다른 공개 일정 자료로 확인했습니다.')
    q=row.get('quarter_scores');half=row.get('half_scores')
    if q:lines.extend(['','**쿼터 예상(원정–홈)**',*table(['구간','예상 점수'],[[str(i+1)+'쿼터',f'{num(x[0])}–{num(x[1])}'] for i,x in enumerate(q)])])
    if half:lines.append(f'전반 예상: {num(half[0])}–{num(half[1])}')
    if not q and not half:lines.append('전반·쿼터: **구간별 점수 산출 근거 부족**. 최종 점수를 나눠 구간 점수를 만들지는 않았습니다.')
    if row.get('lineup_segment_notes'):lines.extend(['구간 참고: '+plain(x) for x in row['lineup_segment_notes']])
    return lines+['']


def math_ceil_half(n):
    import math
    return math.ceil(n*2)/2


def _data(row):
    lines=[f'### {match(row)}']
    for side,label in [('away','원정'),('home','홈')]:
        f=row.get(side+'_form')
        if not f:lines.append(f'{name(row[side])}: 최근 전력 자료 미확인');continue
        lines.append(f"**{name(row[side])} · {label}**")
        windows=[('최근 5경기',f),('최근 10경기',f.get('last10')),('시즌',f.get('season')),('홈',f.get('home')),('원정',f.get('away'))]
        lines+=table(['구간','경기·성적','평균 득점 / 실점'],[[title,f"{w['n']}경기 · {w['wins']}승 {w['n']-w['wins']}패",f"{num(w['for'])} / {num(w['against'])}"] for title,w in windows if w]);lines.append('')
        gap=(dt(row['starts_at'])-dt(f['last_start'])).total_seconds()/3600
        lines.append(f'직전 경기 **시작**과 {gap:.1f}시간 간격'+(' · 연속 일정 주의' if gap<36 else '')+'. 정확한 경기 종료 후 휴식시간과는 다릅니다.')
    ef=[row.get('efficiency',{}).get(s) for s in ('away','home')]
    if any(ef):
        lines.extend(['','**박스스코어 기반 파생 지표**',f"표본: {name(row['away'])} {ef[0]['n'] if ef[0] else 0}경기 / {name(row['home'])} {ef[1]['n'] if ef[1] else 0}경기"])
        data=[]
        for key,label in METRICS.items():
            values=[e.get('metrics',{}).get(key) if e else None for e in ef]
            data.append([label,*[pct(v) if key in ('efg_pct','ts_pct','three_par','ftr','oreb_pct','dreb_pct','tov_pct') else num(v) for v in values]])
        lines+=table(['지표',name(row['away']),name(row['home'])],data);lines.append('공식 포제션·선수 추적 지표가 아닌 추정 포제션 기반 평균입니다.')
    injuries=row.get('injuries',[])
    if injuries:
        lines+=['',*table(['선수','조회 상태'],[[i['player']+(' · '+i.get('team','')),{'Out':'결장 표기','Day-To-Day':'출전 불확실','Active':'출전 가능 표기'}.get(i.get('status'),'상태 확인 필요')] for i in injuries])]
    else:lines.append('조회된 선수 상태 자료가 없습니다. 전원 정상 출전을 뜻하지 않습니다.')
    m=row.get('market',{});price_labels={'moneyline_away':'원정 승','moneyline_home':'홈 승','pointSpread_away':'원정 핸디','pointSpread_home':'홈 핸디','total_over':'오버','total_under':'언더'}
    lines.extend(['',f"시장 출처: {m.get('bookmaker') or '업체 미확인'} · 조회 {local(m.get('observed_at'),True)} KST · 원 공개 시각 "+(local(m['published_at'],True)+' KST' if m.get('published_at') else '미확인')])
    prices=m.get('prices',{})
    if prices:lines+=table(['시장','조회 배당(십진)'],[[price_labels.get(k,k),num(v.get('decimal'),2)] for k,v in prices.items()])
    scores=row.get('baseline_scores_away_home')
    if scores:
        a,h=scores;line=m.get('home_line');total=m.get('total')
        lines+=table(['비교','값'],[['예상 점수(원정–홈)',f'{a:g}–{h:g}'],['예상 홈 마진 / 총점',f'{h-a:+g} / {h+a:g}'],['시장 홈 핸디 / 총점',f"{signed(line)} / {num(total)}"],
            ['홈 핸디 시나리오 차이',num(h-a+line) if finite(line) else '기준점 미확인'],['총점 시나리오 차이',num(h+a-total) if finite(total) else '기준점 미확인']])
    lines.append('모델: '+('검증 자격을 갖춘 기존 운영 모델' if row.get('trained_model_used') else '최근 득실점 또는 파생 효율 기반 탐색 시나리오')+' · 확정 선발·선수별 역할 영향·공식 포제션 등은 추가 확인이 필요합니다.')
    return lines+['']


def _research(audit):
    lines=['## ⑤ 연구 모델 및 검증 현황'];r=audit.get('research_comparison')
    if not r:return lines+['현재 보고서에서 확인 가능한 모델 비교 원본이 없습니다. 성능을 추정하지 않았습니다.','']
    f=r['folds'][-1];ms=f['metrics']
    lines.extend([f"회고 연구 {r['source_games']:,}경기, 사용 표본 {r['usable_samples']:,}개. 아래는 같은 시간순 외부 구간의 비교입니다.",''])
    lines+=table(['모델','검증 경기','팀 득점 MAE'],[[MODELS.get(k,k),v['n'],num(v['score_mae'],2)] for k,v in ms.items()]);lines.append('')
    lines+=table(['모델','마진 MAE','총점 MAE','Brier'],[[MODELS.get(k,k),num(v['margin_mae'],2),num(v['total_mae'],2),num(v['brier'],3)] for k,v in ms.items()])
    margin=min(ms,key=lambda k:ms[k]['margin_mae']);total=min(ms,key=lambda k:ms[k]['total_mae'])
    lines+=['',f"이 구간에서 마진 오차는 **{MODELS.get(margin,margin)}**, 총점 오차는 **{MODELS.get(total,total)}**가 상대적으로 작았습니다. 한 지표의 우위가 다른 지표나 수익성을 보장하지는 않습니다.",
        '역사 자료의 최초 공개 시각을 증명하지 못한 회고 연구입니다. 실제 운영 모델의 실전 성능이나 투자 수익 향상이 검증된 결과와 구분합니다.']
    gate=audit.get('learning_gate',{})
    if gate.get('state')=='BLOCKED':lines.append('자동 학습·승격 보류: 경기 전에 이용 가능했음을 확인한 학습 자료가 아직 부족합니다. 단일 결과에 맞춰 가중치를 바꾸지 않습니다.')
    else:lines.append('자동 승격은 충분한 검증 자료와 기존 모델 대비 시간순 외부 검증을 통과해야 가능합니다.')
    return lines+['']


def render_daily(target,now,rows,issues,audit,run_state=None,origin=None,picks=None,diagnostics=None):
    picks=select_picks(rows,now) if picks is None else picks;top=audit.get('top_pick_audit',{});previous=top.get('previous',[])
    weekday=['월','화','수','목','금','토','일'][target.weekday()]
    lines=[f'# SPORTON NBA · {target} ({weekday}요일)',f"작성 {now.astimezone(KST):%m/%d %H:%M} KST · 분석 대상 {len(rows)}경기",'',
        '## ① 오늘의 핵심 요약']
    pre=sum(not r.get('already_started') and dt(r['starts_at'])>now for r in rows)
    lines.extend([f'오늘은 {len(rows)}경기를 살펴봤습니다. 이 가운데 아직 시작하지 않은 경기는 {pre}경기입니다.',
        '**직전 추천 성적:** '+top_score(top),
        '먼저 지난 추천을 채점하고, 오늘 경기를 하나씩 설명하겠습니다. 최종 선택은 보고서 맨 마지막에 모았습니다.','',
        '## ② 전일 추천 픽 결과 및 피드백',*_previous(top),'## ③ 전 경기 분석'])
    for row in rows:lines+=_game(row)
    lines+=['## ④ 핵심 데이터 상세표','본문 판단에 사용한 수치를 보존했습니다. 실제 확인하지 못한 항목은 채우지 않았습니다.','']
    for row in rows:lines+=_data(row)
    lines+=_pick_records(top)
    lines+=_all_game_feedback(audit,top.get('previous_report_id') or audit.get('game_reference_report_id'))
    lines+=_research(audit)
    lines+=['## ⑥ 기술 진단 및 출처','코드 기반 검사는 수행했지만, 전문가 또는 별도 생성형 AI의 독립 심사가 완료됐다고 표현하지 않습니다.','',
        f'실행 구분: {origin or "manual"} · 처리 상태: {run_state or "COMPLETED_PARTIAL"}',f'예정 분석: {target} 06:30 KST · 실제 생성: {now.astimezone(KST).isoformat()}',
        f'사전 원본 제외 기록: {len(top.get("excluded",[]))}건 · 이전 TOP 원본 복구: {top.get("legacy_recovered",0)}건',
        '실제 가격 자격을 확인하지 못한 관찰 픽을 공식 가격픽으로 승격하지 않았습니다. 연구·관찰 성적은 실제 체결 손익이 아닙니다.','']
    for row in rows:
        m=row.get('market',{});v=row.get('review',{})
        lines+=['**'+match(row)+'**',f"경기 ID: {row['id']} · 모델: {row.get('model_version') or '미확인'} · 분석 범위: {row.get('analysis_level') or '미확인'}",
            f"출처: {row.get('source_url') or '미확인'} · 조회 {m.get('observed_at') or '미확인'} · 원 공개 시각 {m.get('published_at') or '미확인'}",
            f"일정: {row.get('schedule_status') or '미확인'} · 심사: {v.get('verdict','NO')}",'심사 사유: '+'; '.join(v.get('reasons',[])),
            '반대 근거 원문: '+'; '.join(v.get('counter_evidence',[])),
            '스냅샷/예측: '+str(row.get('feature_snapshot_id') or '미확인')+' / '+str(row.get('prediction_id') or '미확인'),'']
    lines+=['수집 진단:',*['- '+str(i) for i in issues]] if issues else ['수집 진단: 추가 오류 없음']
    lines+=['',*(diagnostics or []),'','## ⑦ 최종 픽 3개','']
    if len(picks)<3:lines.append(f'현재 자료로 추천할 수 있는 선택은 {len(picks)}개입니다. 시작 전 경기나 근거가 부족한 자리를 가상의 픽으로 채우지는 않았습니다.')
    lines+=['신뢰도는 판단의 강도를 뜻하며 검증된 적중 확률은 아닙니다. 다음 보고서에서 아래 선택을 같은 기준점으로 각각 채점하겠습니다.','']
    lines+=_pick_cards(picks)
    return '\n'.join(lines).rstrip()+'\n'


def to_html(markdown,title='SPORTON NBA 보고서'):
    """Small escaped Markdown subset for a portable, offline mobile preview."""
    output=[];lines=markdown.splitlines();i=0;in_code=False;details=False
    def inline(text):
        escaped=html.escape(text)
        escaped=re.sub(r'\*\*(.+?)\*\*',r'<strong>\1</strong>',escaped)
        return re.sub(r'https://[^\s<]+',lambda m:'<a href="'+m[0]+'" rel="noopener noreferrer">'+m[0]+'</a>',escaped)
    while i<len(lines):
        line=lines[i]
        if line.startswith('## ④ ') and not details:
            output.append('<details class="supporting"><summary>상세 데이터·검증·출처 펼쳐 보기</summary>');details=True
        if line.startswith('## ⑦ ') and details:
            output.append('</details>');details=False
        if line.startswith('```'):
            output.append('</pre>' if in_code else '<pre>');in_code=not in_code;i+=1;continue
        if in_code:output.append(html.escape(line)+'\n');i+=1;continue
        if line.startswith('|') and i+1<len(lines) and re.match(r'^\|[ :|\-]+\|$',lines[i+1]):
            headers=[x.strip() for x in line.strip('|').split('|')];output.append('<div class="table-wrap"><table><thead><tr>'+''.join('<th>'+inline(h)+'</th>' for h in headers)+'</tr></thead><tbody>');i+=2
            while i<len(lines) and lines[i].startswith('|'):
                output.append('<tr>'+''.join('<td>'+inline(x.strip())+'</td>' for x in lines[i].strip('|').split('|'))+'</tr>');i+=1
            output.append('</tbody></table></div>');continue
        heading=re.match(r'^(#{1,4}) (.*)',line)
        if heading:level=len(heading[1]);output.append(f'<h{level}>'+inline(heading[2])+f'</h{level}>')
        elif line.startswith('- '):output.append('<p class="bullet">• '+inline(line[2:])+'</p>')
        elif line.strip():output.append('<p>'+inline(line)+'</p>')
        i+=1
    if details:output.append('</details>')
    css='''*{box-sizing:border-box}body{margin:0;background:#f3f5f4;color:#20312d;font-family:Arial,"Noto Sans CJK KR",sans-serif;font-size:15px;line-height:1.7}.sheet{max-width:780px;margin:auto;background:white;padding:28px}summary{cursor:pointer;font-weight:bold;padding:16px 0;color:#17654e}details{border-top:1px solid #dce8e3;margin-top:24px}h1{word-break:keep-all;font-size:27px;line-height:1.3;letter-spacing:-.04em}h2{font-size:21px;border-top:2px solid #dce8e3;padding-top:24px;margin-top:38px}h3{font-size:18px;line-height:1.5;color:#17654e;margin-top:28px}p{margin:8px 0;overflow-wrap:anywhere}.bullet{padding-left:10px}.table-wrap{width:100%;overflow-x:auto;margin:14px 0}table{width:100%;border-collapse:collapse;font-size:13px;table-layout:fixed}th,td{padding:9px 7px;border-bottom:1px solid #e4ebe7;text-align:left;overflow-wrap:anywhere}th{background:#edf5f0;color:#295b48}strong{color:#123f31}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}@media(max-width:480px){.sheet{padding:18px 16px}h1{font-size:24px}h2{font-size:19px}table{font-size:12px}th,td{padding:8px 5px}}'''
    return '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title><style>'+css+'</style></head><body><main class="sheet">'+''.join(output)+'</main></body></html>'


def telegram_summary(target,now,rows,picks,top):
    lines=[f'SPORTON NBA · {target}',f'작성 {now.astimezone(KST):%H:%M} 한국시간 · {len(rows)}경기','',
        '직전 추천: '+top_score(top),'',
        '지난 추천 채점과 오늘 경기 설명은 첨부 보고서에 담았습니다.','',f'오늘의 최종 픽 {len(picks)}개']
    if not picks:lines.append('시작 전 경기나 분석 근거가 부족해 추천을 만들지 않았습니다.')
    for p in picks:
        lines.extend([f"{p['rank']}. {p['label']} · {TYPES[p['pick_type']]}",
            pick_reason(p)[:115],'위험: '+plain(p['risks'][0])[:65]])
    text='\n'.join(lines)
    while len(text.encode('utf-16-le'))//2>1000:text=text[:-1]
    return text
