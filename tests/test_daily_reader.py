"""TEST/FIXTURE only: frozen TOP selections, exposure, grading and reader report."""
import copy,json
from datetime import timedelta
import pytest
from sporton.contracts import utcnow
from sporton.storage import digest
from sporton.governance import register_report
from sporton.daily_picks import select_picks,save_picks,audit_top_picks,record_condition_quote,recover_legacy_picks
from sporton.nba_report import render_daily,to_html,telegram_summary
from sporton import dispatch


def row(now,gid='TEST-GAME',scores=(100.,110.),line=-5.5,total=210.):
    f={'n':5,'wins':3,'for':110.,'against':100.,'last_start':(now-timedelta(days=2)).isoformat(),
       'last10':{'n':10,'wins':6,'for':108.,'against':102.},'season':{'n':15,'wins':9,'for':109.,'against':103.},
       'home':{'n':7,'wins':5,'for':111.,'against':100.},'away':{'n':8,'wins':4,'for':107.,'against':105.}}
    return {'id':gid,'away':'TEST AWAY','home':'TEST HOME','starts_at':(now+timedelta(hours=2)).isoformat(),'already_started':False,
        'away_form':copy.deepcopy(f),'home_form':copy.deepcopy(f),'baseline_scores_away_home':list(scores),'predicted_margin':scores[1]-scores[0],
        'predicted_total':sum(scores),'review':{'verdict':'PASS','reasons':['TEST/FIXTURE'],'counter_evidence':[]},'schedule_status':'VERIFIED',
        'market':{'home_line':line,'line':'TEST HOME '+str(line),'total':total,'bookmaker':'TEST BOOK','observed_at':now.isoformat(),'published_at':None,
            'prices':{'pointSpread_home':{'decimal':1.9},'pointSpread_away':{'decimal':1.9}}},'efficiency':{},'injuries':[],
        'source_url':'https://fixture.example.test/summary','analysis_level':'PARTIAL_ANALYSIS','recommendation_status':'WATCH','model_version':'TEST-BASELINE','prediction_id':'TEST-PRED'}


def freeze(store,now,rows,picks=None,bid='TEST-BRIEF'):
    picks=select_picks(rows,now,store) if picks is None else picks
    rid=register_report(store,'RESEARCH',content={'date_kst':str(now.date()),'games':rows,'top_picks':picks},data_mode='TEST/FIXTURE')
    save_picks(store,picks,rid,bid,utcnow().isoformat())
    store.append('briefing_events','telegram:sent:'+bid,{'at':utcnow().isoformat(),'message_id':1})
    return rid,picks


def result_fetch(away=100,home=105,completed=True):
    def fetch(*args):return {'header':{'competitions':[{'status':{'type':{'completed':completed}},'competitors':[
        {'id':'A','homeAway':'away','score':str(away)},{'id':'H','homeAway':'home','score':str(home)}]}]}}
    return fetch


def test_selects_one_per_game_three_at_most_and_never_upgrades_public_price(store):
    now=utcnow();rs=[row(now,'TEST-'+str(i)) for i in range(5)]
    before=copy.deepcopy(rs);p=select_picks(rs,now,store)
    assert len(p)==3 and len({x['game_id'] for x in p})==3
    assert all(x['pick_type']=='OBSERVATION' and not x['publication_verified'] and x['label'].endswith('+5.5') is False for x in p)
    assert rs==before and p[0]['label']=='TEST HOME -5.5'
    assert all(x['confidence']=='낮음' for x in p)


def test_missing_market_is_explicit_condition_never_invented_price():
    now=utcnow();r=row(now,line=None,total=None);r['market']['prices']={}
    p=select_picks([r],now)[0]
    assert p['pick_type']=='CONDITIONAL' and p['line'] is None and p['odds'] is None
    assert p['condition']['threshold']==-8.0 and '자체' in p['condition']['basis']


def test_no_picks_after_start_or_without_minimum_evidence():
    now=utcnow();r=row(now);r['starts_at']=now.isoformat()
    assert select_picks([r],now)==[]
    r=row(now);r['away_form']=None
    assert select_picks([r],now)==[]
    r=row(now);r['review']['verdict']='NO'
    assert select_picks([r],now)==[]


@pytest.mark.parametrize('away,home,expected',[(100,106,'W'),(100,105,'L'),(100,105.5,'Push')])
def test_observation_frozen_line_grading_and_no_roi(store,away,home,expected):
    now=utcnow();r=row(now);rid,picks=freeze(store,now,[r]);original=store.get('professional_reports',rid)
    audit=audit_top_picks(store,result_fetch(away,home),now+timedelta(days=1),(now+timedelta(days=1)).date())
    e=audit['records'][0]
    assert e['status']==expected and e['pick']['line']==-5.5
    assert audit['groups']['OBSERVATION']['settled']==1 and audit['groups']['OFFICIAL_PRICE']['count']==0
    assert e['unit_profit'] is None and audit['groups']['OBSERVATION']['roi'] is None
    assert store.get('professional_reports',rid)==original
    assert store.rows('learning_feedback')[-1]['payload']['weights_changed'] is False


def test_conditional_not_counted_without_before_game_activation(store):
    now=utcnow();r=row(now,line=None,total=None);r['market']['prices']={};_,p=freeze(store,now,[r])
    a=audit_top_picks(store,result_fetch(),now+timedelta(days=1),(now+timedelta(days=1)).date())
    assert a['records'][0]['status']=='CONDITION_UNCONFIRMED'
    assert a['groups']['CONDITIONAL']['settled']==0 and a['groups']['CONDITIONAL']['condition_unconfirmed']==1
    with pytest.raises(ValueError,match='before start'):record_condition_quote(store,p[0]['pick_id'],{
        'game_id':r['id'],'market_type':'spread','selection':'home','line':-7,'observed_at':now.isoformat(),'source_url':'https://fixture.example.test','bookmaker':'TEST'},now+timedelta(days=1))


def test_conditional_uses_first_confirmed_actual_line_not_hypothetical_threshold(store):
    now=utcnow();r=row(now,line=None,total=None);r['market']['prices']={};_,p=freeze(store,now,[r])
    record_condition_quote(store,p[0]['pick_id'],{'game_id':r['id'],'market_type':'spread','selection':'home','line':-7,
        'observed_at':(now+timedelta(minutes=1)).isoformat(),'source_url':'https://fixture.example.test','bookmaker':'TEST'},now+timedelta(minutes=1))
    a=audit_top_picks(store,result_fetch(100,107),now+timedelta(days=1),(now+timedelta(days=1)).date())
    assert a['records'][0]['status']=='Push' and a['groups']['CONDITIONAL']['pushes']==1
    assert a['records'][0]['pick']['line'] is None  # original recommendation was not rewritten


def test_missing_original_or_late_delivery_excludes_performance(store):
    now=utcnow();r=row(now);p=select_picks([r],now)[0]
    save_picks(store,[p],'TEST-missing','TEST-missing-brief',utcnow().isoformat())
    a=audit_top_picks(store,result_fetch(),now+timedelta(days=1),(now+timedelta(days=1)).date())
    assert not a['records'] and a['excluded']


def test_old_top_watch_restored_only_from_immutable_delivered_original(store):
    now=utcnow();r=row(now);candidate={'game_id':r['id'],'selection':'home spread','home_line':-5.5,'observed_at':now.isoformat()}
    rid=register_report(store,'RESEARCH',content={'games':[r],'top_watch':[candidate]},data_mode='TEST/FIXTURE')
    before=store.get('professional_reports',rid)
    store.append('briefings','TEST-legacy',{'research_report_id':rid})
    store.append('briefing_events','telegram:sent:TEST-legacy',{'at':utcnow().isoformat()})
    assert recover_legacy_picks(store)==1 and recover_legacy_picks(store)==0
    a=audit_top_picks(store,result_fetch(100,106),now+timedelta(days=1),(now+timedelta(days=1)).date())
    assert a['records'][0]['status']=='W' and store.get('professional_reports',rid)==before


def test_new_report_empty_top_does_not_restore_nonpublished_legacy_candidates(store):
    now=utcnow();r=row(now);rid=register_report(store,'RESEARCH',content={'games':[r],'top_picks':[],'top_watch':[{'game_id':r['id'],'selection':'home spread','home_line':-5.5}]},data_mode='TEST/FIXTURE')
    store.append('briefings','TEST-empty',{'research_report_id':rid});store.append('briefing_events','telegram:sent:TEST-empty',{'at':now.isoformat()})
    assert recover_legacy_picks(store)==0


def test_reader_order_no_dictionary_or_internal_state_before_diagnostics():
    now=utcnow();r=row(now);p=select_picks([r],now);before=copy.deepcopy(r)
    text=render_daily(now.date(),now,[r],['SOURCE_UNAVAILABLE TEST'],{'top_pick_audit':{}},'COMPLETED_PARTIAL','workflow_dispatch',p)
    headings=['① 오늘의 핵심 요약','② 전일 추천 픽 결과 및 피드백','③ 전 경기 분석','④ 핵심 데이터 상세표','⑤ 연구 모델 및 검증 현황','⑥ 기술 진단 및 출처']
    positions=[text.index(h) for h in headings];assert positions==sorted(positions)
    body=text[:positions[-1]]
    for bad in ('None',"{'",'SOURCE_UNAVAILABLE','COMPLETED_PARTIAL','workflow_dispatch'):assert bad not in body
    assert 'TEST HOME -5.5' in body and '관찰 픽' in body and '구간별 점수 산출 근거 부족' in body
    assert '최근 10경기' in body and '시즌' in body and r==before
    assert len(telegram_summary(now.date(),now,[r],p,{}).encode('utf-16-le'))//2<=1024


def test_html_escapes_source_text_and_uses_mobile_viewport():
    text=to_html('# TEST\n<script>alert(1)</script>\n\n| 지표 | 값 |\n| --- | --- |\n| Pace | 101 |')
    assert '<script>' not in text and '&lt;script&gt;' in text
    assert 'name="viewport"' in text and '<table>' in text and 'overflow-x:auto' in text


def test_storage_rollback_preserves_no_partial_pick_ledger(store,monkeypatch):
    now=utcnow();r=row(now);r['feature_snapshot_id']='TEST-feature';p=select_picks([r],now)
    append=store.append
    def fail(table,*a,**k):
        if table=='market_selections':raise RuntimeError('TEST ledger unavailable')
        return append(table,*a,**k)
    monkeypatch.setattr(store,'append',fail)
    with pytest.raises(RuntimeError):
        with store.transaction() as conn:
            rid=register_report(store,'RESEARCH',content={'games':[r],'top_picks':p},data_mode='TEST/FIXTURE',conn=conn)
            save_picks(store,p,rid,'TEST-tx',utcnow().isoformat(),conn)
    assert store.rows('professional_reports')==[] and store.rows('market_selections')==[]


def test_half_metrics_only_when_original_half_prediction_exists():
    from sporton.nba_quality import grade
    now=utcnow();p={'created_at':now.isoformat(),'starts_at':(now+timedelta(hours=1)).isoformat(),'game_id':'TEST',
        'scores':[100,110],'half_scores':[49,55],'quarter_scores':[[24,27],[25,28],[25,28],[26,27]]}
    r={'away_score':102,'home_score':108,'away_periods':[25,27,25,25],'home_periods':[26,28,27,27],'source_grade':'TEST/FIXTURE'}
    e=grade(p,r)
    assert e['half_metrics']['actual_scores']==[52,54] and e['half_metrics']['team_mae']==2
    assert len(e['segment_metrics'])==4
    del p['half_scores'];del p['quarter_scores']
    assert grade(p,r)['half_metrics'] is None and grade(p,r)['segment_metrics'] is None


def test_separate_official_and_observation_ledgers(store):
    now=utcnow();rs=[row(now,'TEST-OFFICIAL'),row(now,'TEST-WATCH')];ps=select_picks(rs,now)
    # TEST-only frozen official fixture exercises accounting, not price qualification.
    ps[0]['pick_type']='OFFICIAL_PRICE';ps[0]['publication_verified']=True;ps[0]['odds']=1.9
    freeze(store,now,rs,ps)
    a=audit_top_picks(store,result_fetch(100,106),now+timedelta(days=1),(now+timedelta(days=1)).date())
    assert a['groups']['OFFICIAL_PRICE']['wins']==1 and a['groups']['OFFICIAL_PRICE']['roi']==pytest.approx(.9)
    assert a['groups']['OBSERVATION']['wins']==1 and a['groups']['OBSERVATION']['roi'] is None


def test_after_start_delivery_never_counts_as_pregame(store):
    now=utcnow();r=row(now);ps=select_picks([r],now)
    rid=register_report(store,'RESEARCH',content={'games':[r],'top_picks':ps},data_mode='TEST/FIXTURE')
    save_picks(store,ps,rid,'TEST-LATE',utcnow().isoformat())
    store.append('briefing_events','telegram:sent:TEST-LATE',{'at':(now+timedelta(hours=3)).isoformat()})
    a=audit_top_picks(store,result_fetch(),now+timedelta(days=1),(now+timedelta(days=1)).date())
    assert not a['records'] and a['excluded'][0]['reason']=='사용자 전달 성공 기록 미확인'


def test_dispatch_freezes_exact_rendered_top_and_next_audit_follows_it(store,monkeypatch):
    now=utcnow();rs=[row(now,'TEST-RUN-'+str(i)) for i in range(3)]
    for i,r in enumerate(rs):r['prediction_id']='TEST-RUN-PRED-'+str(i)
    monkeypatch.setattr(dispatch,'settings',lambda:('TEST','TEST'))
    monkeypatch.setattr(dispatch,'collect',lambda *a:(rs,[]))
    def send(s,r):
        saved=s.get('briefings',r['id']);ps=saved['snapshot']['top_picks']
        assert len(ps)==3 and all(p['label'] in r['markdown'] and p['label'] in r['telegram_caption'] for p in ps)
        assert r['html_document'].startswith('<!doctype html>')
        assert len([x for x in s.rows('market_selections') if x['payload'].get('kind')=='daily_top_pick'])==3
        s.append('briefing_events','telegram:sent:'+r['id'],{'at':utcnow().isoformat(),'message_id':1})
        return {'status':'sent'}
    monkeypatch.setattr(dispatch,'deliver',send)
    assert dispatch.run(store)['status']=='sent'
    a=audit_top_picks(store,result_fetch(100,106),now+timedelta(days=1),now.astimezone(dispatch.KST).date()+timedelta(days=1))
    assert len(a['previous'])==3 and a['all_win'] and a['groups']['OBSERVATION']['wins']==3


def test_final_three_are_last_and_previous_three_each_get_graded(store):
    now=utcnow();rs=[row(now,'TEST-FINAL-'+str(i)) for i in range(3)]
    rid,ps=freeze(store,now,rs)
    top=audit_top_picks(store,result_fetch(100,106),now+timedelta(days=1),now.astimezone(dispatch.KST).date()+timedelta(days=1))
    text=render_daily(now.date(),now,rs,[],{'top_pick_audit':top},picks=ps,diagnostics=['TEST RUN ID'])
    final=text.split('## ⑦ 최종 픽 3개')[1]
    assert final.count('### ')==3 and all(p['label'] in final for p in ps)
    assert 'TEST RUN ID' not in final and text.index('TEST RUN ID')<text.index('## ⑦')
    feedback=text.split('## ②')[1].split('## ③')[0]
    assert feedback.count('— 적중')==3 and '3개 중 3개 적중' in feedback
    assert '왜 골랐나' in feedback and '실제 결과' in feedback and '다음 분석에서 확인할 점' in feedback
    assert '홈 마진' not in final and '이 픽을 고른 이유' in final
    html=to_html(text)
    assert '<details class="supporting">' in html and html.index('</details>')<html.index('⑦ 최종 픽 3개')


def test_telegram_summary_keeps_all_three_labels_and_previous_score():
    now=utcnow();ps=select_picks([row(now,'TEST-CAP-'+str(i)) for i in range(3)],now)
    top={'previous':[{'pick':p,'status':s} for p,s in zip(ps,['W','L','Push'])]}
    caption=telegram_summary(now.date(),now,[],ps,top)
    assert '3개 중 1개 적중' in caption and '1개 미적중' in caption and '1개 무승부' in caption
    assert all(p['label'] in caption for p in ps) and len(caption.encode('utf-16-le'))//2<=1024


@pytest.mark.parametrize('key',['value','displayValue'])
def test_summary_final_with_both_period_schemas_settles_frozen_picks(store,key):
    from sporton.nba_quality import final_result
    now=utcnow();_,ps=freeze(store,now,[row(now)])
    body=result_fetch(100,106)()
    competitors=body['header']['competitions'][0]['competitors']
    competitors[0]['linescores']=[{key:str(v)} for v in [25,25,25,25]]
    competitors[1]['linescores']=[{key:str(v)} for v in [26,27,26,27]]
    result=final_result({'id':'TEST-GAME','date':ps[0]['starts_at'],'competitions':body['header']['competitions']})
    assert result['away_periods']==[25,25,25,25] and result['home_periods']==[26,27,26,27]
    a=audit_top_picks(store,lambda *args:body,now+timedelta(days=1),now.astimezone(dispatch.KST).date()+timedelta(days=1))
    assert a['records'][0]['status']=='W' and a['groups']['OBSERVATION']['wins']==1
