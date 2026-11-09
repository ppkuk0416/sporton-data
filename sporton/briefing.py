"""Twice-daily KST research briefings: immutable source references, not chat notifications."""
from datetime import datetime,timedelta,time,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from uuid import uuid4
from contextlib import contextmanager
import fcntl
import json
import os
import math
from .contracts import aware,utcnow
from .storage import home,digest
from .sports import LEAGUES,sport_for
from .engine import forecast,audit,drift
from .models import champion

KST=ZoneInfo('Asia/Seoul')
SLOTS=(time(6,30),time(17,0))
POLICY={
    'timezone':'Asia/Seoul','times':['06:30','17:00'],
    'implemented_sports':['basketball','football','baseball'],
    'pending_sports':['KBL','volleyball','LoL','VALORANT'],
    'priorities':['과거 예측 원본과 실제 결과 비교, 실패와 불확실성 공개',
        '앞으로 24시간 경기와 다음 브리핑 전 경기, 종목별 독립 분석·심사',
        '실제 데이터만 축적하고 출처·공개 시각·피처·모델 버전 보존',
        '모델 성능 저하·수집 실패·미구현 기능 및 이전 브리핑 후 변화 추적',
        '검증된 후보만 승격, 자동 교체·임의 배당·허위 성능 금지'],
    'delivery':'local_dashboard_and_markdown','chat_notifications_connected':False,
    'data_pipeline':'direct licensed archives + normalized inbox CSV; KBL official discovery blocked; archives are not verified live data',
}


def slots_near(now):
    local=aware(now).astimezone(KST)
    return sorted(datetime.combine(local.date()+timedelta(days=d),t,KST).astimezone(timezone.utc)
                  for d in (-1,0,1) for t in SLOTS)


def latest_slot(now):return max(t for t in slots_near(now) if t<=now)
def next_slot(now):return min(t for t in slots_near(now) if t>now)
def slot_key(at):return aware(at).astimezone(KST).strftime('%Y-%m-%d-%H%M')
def iso(at):return aware(at).isoformat()
def local(at):return datetime.fromisoformat(at).astimezone(KST).strftime('%m-%d %H:%M') if isinstance(at,str) else at.astimezone(KST).strftime('%m-%d %H:%M')


def add_note(store,text):
    text=text.strip()
    if not text:raise ValueError('empty briefing note')
    note={'text':text,'at':iso(utcnow()),'status':'user_recorded_topic','not_a_result_label':True}
    return store.append('briefing_notes',str(uuid4()),note)


def policy_record(store):
    store.append('briefing_notes','policy:'+digest(POLICY),{'policy':POLICY,'kind':'user_briefing_requirements'},idempotent=True)


def row_as_of(row,now):
    return datetime.fromisoformat(row['created_at'])<=now


def visible_reports(store,now,test_mode):
    mode='TEST/FIXTURE' if test_mode else 'real';out={}
    for row in store.rows('model_predictions'):
        p=row['payload']
        if p['snapshot']['data_mode']==mode and datetime.fromisoformat(p['created_at'])<=now:out[row['id']]=p
    return out


def reconcile_markdown(record):
    directory=home()/'briefings';directory.mkdir(exist_ok=True)
    path=directory/(record['id']+'.md')
    content=record['markdown']
    if path.exists() and path.read_text()!=content:raise ValueError('briefing export integrity conflict')
    if not path.exists():
        temp=path.with_suffix('.tmp');temp.write_text(content);temp.replace(path)
    return str(path)


def build_snapshot(store,now,scheduled_for,window_start,previous,test_mode=False,refresh=True,inbox=None):
    mode='TEST/FIXTURE' if test_mode else 'real';issues=[];maintenance=None
    if refresh:
        try:
            from .maintenance import tick
            maintenance=tick(store,inbox or home()/'inbox',test_mode,False)
            issues.extend({'kind':'ingest_failure',**f} for f in maintenance['failures'])
        except Exception as exc:
            issues.append({'kind':'maintenance_failed','error':str(exc)})
    quality='TEST/FIXTURE' if test_mode else 'verified'
    historical=[r for r in store.rows('historical_game_results') if r['payload']['quality']==quality
                and datetime.fromisoformat(r['payload']['published_at'])<=now]
    fixtures=[r['payload'] for r in store.rows('fixtures')]
    # Game IDs alone are not a mode tag. Historical fixtures inherit quality; future fixtures need no fabricated sample data.
    test_ids={r['payload']['game_id'] for r in store.rows('historical_game_results') if r['payload']['quality']=='TEST/FIXTURE'}
    real_ids={r['payload']['game_id'] for r in historical}
    if not test_mode:fixtures=[f for f in fixtures if f['game_id'] not in test_ids]
    prior_reports=visible_reports(store,now,test_mode)
    upcoming=sorted([f for f in fixtures if now<datetime.fromisoformat(f['starts_at'])<=now+timedelta(hours=24)],key=lambda f:f['starts_at'])
    if refresh:
        for f in upcoming:
            if store.get('official_recommendations',f['game_id']):continue
            current=champion(store,f['league'])
            if not current:
                issues.append({'kind':'no_champion','game_id':f['game_id'],'league':f['league']});continue
            try:forecast(store,f,current,test_mode=test_mode)
            except Exception as exc:issues.append({'kind':'forecast_unavailable','game_id':f['game_id'],'league':f['league'],'error':str(exc)})
        try:audit(store)
        except Exception as exc:issues.append({'kind':'audit_failed','error':str(exc)})
    reports=visible_reports(store,now,test_mode)
    # refresh may create predictions milliseconds after the first cutoff. Caller uses a fresh final cutoff after work.
    decisions={r['payload']['fixture_id']:r['payload'] for r in store.rows('official_recommendations')
        if r['payload']['data_mode']==mode and datetime.fromisoformat(r['payload']['at'])<=now}
    from .analytics import resolved_evaluations
    evaluations={r['payload']['prediction_id']:r['payload'] for r in resolved_evaluations(store)
        if r['payload']['data_mode']==mode and datetime.fromisoformat(r['payload']['at'])<=now}
    past=[]
    for f in sorted(fixtures,key=lambda f:f['starts_at']):
        from .governance import effective_get
        result=effective_get(store,'actual_results',f['game_id'],now);decision=decisions.get(f['game_id']);prediction_id=decision['prediction_id'] if decision else None
        new_result=result and result['quality']==quality and window_start<datetime.fromisoformat(result['published_at'])<=now
        pending=prediction_id and datetime.fromisoformat(f['starts_at'])<=now and prediction_id not in evaluations
        new_eval=prediction_id in evaluations and window_start<datetime.fromisoformat(evaluations[prediction_id]['at'])<=now
        recent_started=window_start<datetime.fromisoformat(f['starts_at'])<=now
        if new_result or new_eval or pending or recent_started:
            available_result=result if result and result['quality']==quality and datetime.fromisoformat(result['published_at'])<=now else None
            past.append({'fixture':f,'status':'final' if available_result else 'started_or_awaiting_verified_result',
                'actual_result':available_result,'original_result':store.get('actual_results',f['game_id']),'prediction':reports.get(prediction_id),
                'decision':decision,'evaluation':evaluations.get(prediction_id),
                'no_prior_prediction':prediction_id is None})
    future=[]
    boundary=next_slot(now)
    for f in upcoming:
        d=decisions.get(f['game_id']);pid=d['prediction_id'] if d else None
        drafts=[p for p in reports.values() if p['fixture']['game_id']==f['game_id']]
        report=reports.get(pid) or (max(drafts,key=lambda p:p['created_at']) if drafts else None)
        reviews=[r['payload'] for r in store.rows('critical_reviews') if report and r['payload']['prediction_id']==report['id'] and datetime.fromisoformat(r['payload']['at'])<=now]
        new_info=[]
        if report:
            for table in ('injuries','team_statistics','market_snapshots'):
                for row in store.rows(table):
                    item=row['payload']
                    if item.get('game_id')==f['game_id'] and item.get('quality')==quality and item.get('collected_at') and datetime.fromisoformat(report['created_at'])<datetime.fromisoformat(item['collected_at']) and datetime.fromisoformat(item['collected_at'])<=now:
                        new_info.append({'table':table,'id':row['id'],'payload':item})
        future.append({'new_information_since_prediction':new_info,'requires_reassessment':bool(new_info),'fixture':f,'before_next_briefing':datetime.fromisoformat(f['starts_at'])<boundary,
            'prediction':report,'decision':d,'reviews':reviews,'analysis_status':'published' if d else 'reviewed_or_rejected' if report else 'unavailable'})
    models=[]
    for league in LEAGUES:
        current=champion(store,league)
        versions=[r['payload'] for r in store.rows('model_versions') if r['payload']['league']==league and r['payload']['data_mode']==mode and datetime.fromisoformat(r['payload']['created_at'])<=now]
        observations=[p for p in evaluations.values() if p.get('league',reports.get(p['prediction_id'],{}).get('fixture',{}).get('league'))==league]
        models.append({'league':league,'sport':sport_for(league),'verified_games':sum(r['payload']['league']==league for r in historical),
            'champion':current,'latest_model':versions[-1] if versions else None,
            'drift':drift(store,current) if current else {'warning':False,'status':'insufficient_data','n':0},
            'live_n':len(observations),'live_accuracy':sum(p['correct'] for p in observations)/len(observations) if observations else None})
    previous_data=previous.get('snapshot',{}) if previous else {}
    old_ids={r['fixture']['game_id'] for r in previous_data.get('upcoming',[])}
    failures=[r for r in store.rows('source_failures') if window_start<datetime.fromisoformat(r['created_at'])<=now]
    promotions=[r for r in store.rows('model_promotions') if window_start<datetime.fromisoformat(r['created_at'])<=now]
    notes=[r for r in store.rows('briefing_notes') if row_as_of(r,now)]
    from .collection import status as collection_status
    research=[r['payload'] for r in store.rows('training_runs') if r['payload'].get('purpose')=='date_only_retrospective_research' and 'test' in r['payload']]
    return {'data_mode':mode,'as_of_timestamp':iso(now),'scheduled_for':iso(scheduled_for),'window_start':iso(window_start),
        'next_briefing_at':iso(boundary),'upcoming_horizon_hours':24,'past':past,'upcoming':future,'models':models,
        'changes_since_previous':{'previous_briefing_id':previous['id'] if previous else None,
            'previously_announced_now_finished':[p['fixture']['game_id'] for p in past if p['fixture']['game_id'] in old_ids and p['actual_result']],
            'new_upcoming_ids':[f['fixture']['game_id'] for f in future if f['fixture']['game_id'] not in old_ids],
            'new_evaluation_ids':[pid for pid,p in evaluations.items() if window_start<datetime.fromisoformat(p['at'])<=now],
            'model_events':promotions,'record_revisions':[r for r in store.rows('record_revisions') if window_start<datetime.fromisoformat(r['payload']['recorded_at'])<=now]},
        'data_management':{'total_verified_games':len(historical),'new_results':sum(window_start<datetime.fromisoformat(r['payload']['published_at'])<=now for r in historical),
            'new_ingested_game_count':sum(window_start<datetime.fromisoformat(r['created_at'])<=now for r in historical),
            'independent_sources':collection_status(store),'archive_research':research[-1] if research else None,
            'source_failures':failures,'maintenance':maintenance,'issues':issues,'immutable_prediction_count':len(reports)},
        'tracked_topics':notes,'policy':POLICY,'limitations':['일정 공급자 미연결: 등록된 일정 밖의 경기는 알 수 없습니다.',
            '실제 데이터·모델이 없으면 분석 불가로 기록하며 수치·경기·배당을 만들지 않습니다.',
            '일반 채팅 내용은 자동 수집하지 않습니다. 등록된 주제·과거 브리핑·예측·심사·결과만 추적합니다.',
            '정시 실행은 호스트와 예약 프로세스가 실행 중일 때만 가능합니다. 이 채팅으로 자동 전송되지 않습니다.']}


def render_markdown(key,snapshot,generated_at,late):
    s=snapshot
    lines=[f'# 스포츠 데일리 브리핑 · {local(s["scheduled_for"])} KST',
        f'실제 생성: {local(generated_at)} KST · 데이터 기준: {local(s["as_of_timestamp"])} KST · 모드: {s["data_mode"]}',
        f'예정 시각 대비 지연 {int(late)}초. 사전 분석은 실제 생성 시각 기준이며 소급 생성하지 않습니다.',
        '', '## 지난 경기와 이전 분석 검증']
    if not s['past']:lines.append('- 새로 확인된 종료 경기·감사 기록이 없습니다. 전체 스포츠에 경기가 없었다는 뜻은 아닙니다.')
    for item in s['past']:
        f=item['fixture'];lines.append(f"- [{f['league']}] {f['home_team']} vs {f['away_team']} · {local(f['starts_at'])} · {item['status']}")
        if item['actual_result']:
            r=item['actual_result'];lines.append(f"  - 실제 결과: {r['home_score']}–{r['away_score']} · 출처 {r['source']} · 공개 {local(r['published_at'])}")
        if item['prediction']:
            p=item['prediction'];lines.append(f"  - 사전 예측 {p['id']} / 모델 {p['model_id']}: 득점 {p['scores'][0]:.2f}–{p['scores'][1]:.2f}")
        elif item['no_prior_prediction']:lines.append('  - 보존된 사전 예측 없음: 사후에 적중 기록을 만들지 않았습니다.')
        if item['evaluation']:
            e=item['evaluation'];lines.append(f"  - 감사: {'적중' if e['correct'] else '실패'} · Brier {e['brier']:.4f} · 마진 오차 {e['margin_error']:.2f} · 총점 오차 {e['total_error']:.2f}")
    lines+=['','## 앞으로 24시간 경기 · 경기 전 분석']
    if not s['upcoming']:lines.append('- 등록된 앞으로 24시간 일정이 없습니다. 공식 일정 전체를 확보한 상태가 아닙니다.')
    for item in s['upcoming']:
        f=item['fixture'];lines.append(f"- [{f['league']}] {f['home_team']} vs {f['away_team']} · {local(f['starts_at'])} · {'다음 브리핑 전' if item['before_next_briefing'] else '이후'} · {item['analysis_status']}")
        p=item['prediction'];d=item['decision']
        if p:
            lines.append(f"  - 분석가 {p['analyst']} · 모델 {p['model_id']} · 범위 {p['scope']} · 득점 {p['scores'][0]:.2f}–{p['scores'][1]:.2f}")
            probs=p.get('outcome_probabilities',{'home':p['home_win_probability'],'away':1-p['home_win_probability']})
            lines.append('  - 교정된 연구 확률 (실전 검증과 별개): '+', '.join(f'{k} {v:.1%}' for k,v in probs.items()))
            lines.extend('  - 불확실성: '+t for t in p.get('limitations',[]))
        else:lines.append('  - 사전 분석 불가: 검증된 데이터 또는 리그별 Champion이 없습니다.')
        if item['requires_reassessment']:lines.append(f"  - 원본 예측 이후 새 정보 {len(item['new_information_since_prediction'])}건: 기존 예측을 유지하고 재심사 필요로 표시합니다. 기존 추천을 현재 유효한 추천으로 재확인하지 않습니다.")
        if d:lines.append(f"  - 원본 게시 심사 {d['review']} · 시장 선택 {d['state']} · 실제 시장 {d.get('market_id') or '미확인'}")
        for review in item['reviews']:lines.append(f"  - 독립 심사 {review['verdict']}: "+'; '.join(review['reasons']+review.get('counter_evidence',[])))
    lines+=['','## 데이터 축적·모델 관리']
    management=s['data_management'];lines.append(f"- 누적 검증 경기 {management['total_verified_games']}건 · 신규 입력 {management['new_ingested_game_count']}건 · 이번 구간 공개 결과 {management['new_results']}건 · 보존 예측 {management['immutable_prediction_count']}건")
    for m in s['models']:
        lines.append(f"- {m['league']}: 데이터 {m['verified_games']} · Champion {m['champion'] or '미선정'} · 실전 감사 {m['live_n']} · 적중률 {format(m['live_accuracy'],'.1%') if m['live_accuracy'] is not None else '미측정'} · 드리프트 {m['drift'].get('status','경고' if m['drift']['warning'] else '정상')}")
    for source in management['independent_sources']:
        archive=source['latest_archive'];attempt=source['last_attempt']
        lines.append(f"- 직접 수집 {source['source_key']}: {attempt['status'] if attempt else '미실행'} · 원본 경기 {archive['payload']['metadata']['games'] if archive else 0}건 · 등급 {source['data_grade']}. 아카이브를 실전 검증 자료로 간주하지 않습니다.")
    research=management['archive_research']
    if research:
        lines.append('- 공개 아카이브 시간순 연구 실험 (실전 승격 불가):')
        for kind,result in research['test'].items():
            lines.append(f"  - {kind}: 테스트 n={result['n']}, Brier={result['brier']:.4f}, Accuracy={result['accuracy']:.1%}")
        dl=research['test']['pytorch_mlp']['brier'];baseline=min(v['brier'] for k,v in research['test'].items() if k!='pytorch_mlp')
        if dl>=baseline:lines.append('  - 딥러닝의 최종 테스트 개선이 확인되지 않았습니다. 운영 모델로 승격하지 않았습니다.')
        lines.append('  - 실제 공개 시각이 없는 회고 자료로, 정밀한 사전 시점 검증이나 실전 수익의 증거가 아닙니다.')
    for m in s['models']:
        model=m['latest_model']
        if model:
            val=model['validation'];lines.append(f"- {m['league']} 최근 학습 {local(model['trained_at'])} · 학습 기간 {' ~ '.join(map(local,model['training_period']))} · 검증 n={val['n']}, Brier={val['brier']:.4f}. 검증 성능과 실전 성능은 별개입니다.")
    lines.append(f"- 수집 실패 {len(management['source_failures'])}건 · 작업 문제 {len(management['issues'])}건. 학습 데이터가 늘었다고 모델을 자동 교체하지 않습니다.")
    for r in management['source_failures']:lines.append('- 수집 실패: '+r['payload'].get('error','원인 미확인'))
    for i in management['issues']:lines.append('- 작업 상태: '+json.dumps(i,ensure_ascii=False))
    lines+=['','## 이전 브리핑·논의 주제 추적']
    c=s['changes_since_previous'];lines.append('- 이전 브리핑: '+(c['previous_briefing_id'] or '없음'))
    for r in c.get('record_revisions',[]):lines.append('- 정정 기록: '+r['payload']['target_table']+':'+r['payload']['target_id']+' · '+r['payload']['reason']+' · 원본 보존')
    lines.append('- 이전에 안내한 경기 중 결과 확인: '+(', '.join(c['previously_announced_now_finished']) or '없음'))
    lines.extend('- 관리 원칙: '+t for t in s['policy']['priorities'])
    for r in s['tracked_topics']:
        if 'text' in r['payload']:lines.append('- 등록 주제: '+r['payload']['text'])
    lines+=['- 미구현 추적: KBL·배구·LoL·VALORANT 전용 모델, 실제 데이터 공급자, 생성형 LLM 전문가 연결.','','## 확인 범위와 제한']
    lines.extend('- '+t for t in s['limitations'])
    return '\n'.join(lines)+'\n'


def create_briefing(store,scheduled_for=None,test_mode=False,refresh=True,inbox=None):
    started=utcnow();scheduled=aware(scheduled_for or started)
    if scheduled>started:raise ValueError('cannot create a future scheduled briefing')
    key=slot_key(scheduled) if scheduled_for is not None else 'manual-'+str(uuid4())
    existing=store.get('briefings',key)
    if existing:
        if existing['data_mode']!=('TEST/FIXTURE' if test_mode else 'real'):raise ValueError('briefing data mode mismatch')
        reconcile_markdown(existing)
        return existing
    policy_record(store)
    previous_rows=[r['payload'] for r in store.rows('briefings') if r['payload']['data_mode']==('TEST/FIXTURE' if test_mode else 'real')]
    previous=max(previous_rows,key=lambda p:p['generated_at']) if previous_rows else None
    before=[t for t in slots_near(scheduled) if t<scheduled]
    window_start=datetime.fromisoformat(previous['generated_at']) if previous else max(before)
    # Work with the actual clock; the scheduled timestamp is only a label, never a pre-game cutoff.
    first=build_snapshot(store,started,scheduled,window_start,previous,test_mode,refresh,inbox)
    cutoff=utcnow()
    snapshot=build_snapshot(store,cutoff,scheduled,window_start,previous,test_mode,False,inbox)
    snapshot['data_management']['maintenance']=first['data_management']['maintenance']
    snapshot['data_management']['issues']=first['data_management']['issues']
    generated=utcnow();late=max(0,(generated-scheduled).total_seconds())
    record={'id':key,'scheduled_for':iso(scheduled),'generated_at':iso(generated),'data_mode':snapshot['data_mode'],
        'timezone':'Asia/Seoul','late_by_seconds':late,'late':scheduled_for is not None and late>900,
        'snapshot':snapshot,'delivery':{'channel':'local_dashboard_and_markdown','external_sent':False},
        'markdown':render_markdown(key,snapshot,iso(generated),late)}
    store.append('briefings',key,record)
    reconcile_markdown(record)
    store.append('briefing_events','created:'+key,{'briefing_id':key,'at':iso(generated),'action':'created','scheduled_for':iso(scheduled)})
    return record


def ensure_backup(store,record):
    key='backup:'+record['id']
    if store.get('briefing_events',key):return
    if os.getenv('SPORTON_PERSISTENCE_CONFIG'):
        store.append('briefing_events',key,{'action':'backup_delegated','briefing_id':record['id'],'at':iso(utcnow()),'worker':'persistence-worker','verified_restore':False})
        return
    from .maintenance import backup
    destination=home()/'backups'/record['id']
    if destination.exists():destination=destination.with_name(destination.name+'-retry-'+str(uuid4()))
    try:
        manifest=backup(store,destination)
        store.append('briefing_events',key,{'action':'backup_completed','briefing_id':record['id'],'at':iso(utcnow()),'path':str(destination),'manifest':manifest})
    except Exception as exc:
        store.append('briefing_events',str(uuid4()),{'action':'backup_failed','briefing_id':record['id'],'at':iso(utcnow()),'error':str(exc)})


def run_due(store,test_mode=False,inbox=None):
    now=utcnow();due=latest_slot(now);key=slot_key(due)
    existing=store.get('briefings',key)
    if existing:
        if existing['data_mode']!=('TEST/FIXTURE' if test_mode else 'real'):raise ValueError('briefing data mode mismatch')
        ensure_backup(store,existing)
        return None
    previous=[r['payload'] for r in store.rows('briefings') if not r['id'].startswith('manual-')]
    missed=[]
    if previous:
        last=max(datetime.fromisoformat(r['scheduled_for']) for r in previous)
        cursor=next_slot(last)
        while cursor<due:
            missed.append(iso(cursor));cursor=next_slot(cursor)
    result=create_briefing(store,due,test_mode,True,inbox)
    ensure_backup(store,result)
    if missed:store.append('briefing_events','missed:'+key,{'at':iso(utcnow()),'action':'missed_slots','slots':missed,'catch_up_briefing_id':key})
    return result


@contextmanager
def scheduler_lock():
    with (home()/'briefing.scheduler.lock').open('a') as lock:
        try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('briefing scheduler already running') from None
        try:yield
        finally:fcntl.flock(lock.fileno(),fcntl.LOCK_UN)


def scheduler_status():
    path=home()/'briefing.scheduler.json';state=json.loads(path.read_text()) if path.exists() else {}
    locked=False
    try:
        with scheduler_lock():pass
    except ValueError:locked=True
    heartbeat=datetime.fromisoformat(state['heartbeat']) if state.get('heartbeat') else None
    return {'policy':POLICY,'process_lock_held':locked,'heartbeat':state.get('heartbeat'),
        'running_observed':bool(locked and heartbeat and (utcnow()-heartbeat).total_seconds()<120),
        'next_scheduled_at':iso(next_slot(utcnow())),'chat_notifications_connected':False}


def daemon(store,inbox=None,test_mode=False,once=False,retrain=False):
    import time as clock
    with scheduler_lock():
        while True:
            now=utcnow()
            # Hourly data accumulation/audit is distinct from the twice-daily briefing schedule.
            hour_key=now.strftime('%Y-%m-%dT%H')
            if not store.get('briefing_events','collection:'+hour_key):
                from .collection import collect_due
                collected=collect_due(store) if not test_mode else [{'status':'TEST/FIXTURE real collection disabled'}]
                research_result=None
                if retrain:
                    import importlib.util
                    if importlib.util.find_spec('torch') and all(any(r['payload']['source_key']=='epl-'+season for r in store.rows('archive_datasets')) for season in ('2023-24','2024-25','2025-26')):
                        try:
                            from .archive_research import run_epl_research
                            result=run_epl_research(store)
                            research_result={'status':'complete','data_hash':result['data_hash'],'production_eligible':False}
                        except Exception as exc:research_result={'status':'blocked','error':str(exc)}
                store.append('briefing_events','collection:'+hour_key,{'at':iso(utcnow()),'action':'direct_collection','results':collected,'archive_research':research_result})
            if not store.get('briefing_events','maintenance:'+hour_key):
                from .maintenance import tick
                from .engine import daily
                outcomes={}
                try:outcomes['inbox']=tick(store,inbox or home()/'inbox',test_mode,False)
                except Exception as exc:outcomes['inbox_error']=str(exc)
                if retrain:
                    for league in LEAGUES:
                        count=sum(r['payload']['league']==league and r['payload']['quality']==('TEST/FIXTURE' if test_mode else 'verified') for r in store.rows('historical_game_results'))
                        if count<100:continue
                        try:outcomes[league]=daily(store,test_mode,True,league,deep_learning=bool(__import__('importlib.util').util.find_spec('torch')))
                        except Exception as exc:outcomes[league]={'blocked':str(exc)}
                store.append('briefing_events','maintenance:'+hour_key,{'at':iso(utcnow()),'action':'hourly_maintenance','outcomes':outcomes,'retrain_enabled':retrain})
            state={'pid':os.getpid(),'heartbeat':iso(now),'next_scheduled_at':iso(next_slot(now)),'retrain_enabled':retrain}
            path=home()/'briefing.scheduler.json';temp=path.with_suffix('.tmp');temp.write_text(json.dumps(state));temp.replace(path)
            try:
                result=run_due(store,test_mode,inbox)
                if result:print(json.dumps({'briefing_id':result['id'],'generated_at':result['generated_at'],'late':result['late']},ensure_ascii=False),flush=True)
            except Exception as exc:
                store.append('briefing_events',str(uuid4()),{'at':iso(utcnow()),'action':'failed','error':str(exc)})
                if once:raise
                print(json.dumps({'error':str(exc)},ensure_ascii=False),flush=True)
            if once:return
            clock.sleep(min(30,max(1,(next_slot(utcnow())-utcnow()).total_seconds())))
