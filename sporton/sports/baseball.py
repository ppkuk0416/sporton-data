"""Pitcher/lineup/bullpen features and run heads, independently trained per MLB/KBO."""
from datetime import datetime
import numpy as np
from sklearn.ensemble import RandomForestClassifier,RandomForestRegressor
from sklearn.linear_model import LogisticRegression,PoissonRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from ..storage import digest
from .contracts import BaseballContext
from .common import histories,team_scores,make_snapshot,calibrated

CONTEXT_FIELDS=['starter_fip','starter_k_bb','starter_last_pitch_count','starter_rest_days','lineup_wrc_plus','bullpen_pitches_3d','park_factor','temperature_c']


def context_as_of(store,fixture,cutoff,test_mode=False):
    quality='TEST/FIXTURE' if test_mode else 'verified';latest={}
    for r in store.rows('team_statistics'):
        p=r['payload']
        if p.get('kind')!='baseball_pregame' or p.get('game_id')!=fixture.game_id or p.get('league')!=fixture.league:continue
        c=BaseballContext.model_validate({k:v for k,v in p.items() if k!='kind'})
        if c.quality!=quality or c.published_at>cutoff or c.collected_at>cutoff:continue
        if c.team_id not in latest or c.published_at>latest[c.team_id].published_at:latest[c.team_id]=c
    return latest


class BaseballModel:
    def __init__(self,kind):self.kind=kind
    def fit(self,fit,cal,features,outcomes):
        X=np.array([r['x'] for r in fit]);y=[r['y'] for r in fit];scores=np.array([r['scores'] for r in fit])
        if self.kind=='starter_logistic':
            self.classifier=make_pipeline(StandardScaler(),LogisticRegression(C=.3,max_iter=1000,random_state=47)).fit(X,y)
            self.run_heads=[make_pipeline(StandardScaler(),PoissonRegressor(alpha=2.,max_iter=1000)).fit(X,scores[:,i]) for i in range(2)]
        else:
            self.classifier=RandomForestClassifier(n_estimators=80,max_depth=5,min_samples_leaf=8,n_jobs=2,random_state=47).fit(X,y)
            self.run_heads=[RandomForestRegressor(n_estimators=80,max_depth=5,min_samples_leaf=8,n_jobs=2,random_state=47+i).fit(X,scores[:,i]) for i in range(2)]
        CX=np.array([r['x'] for r in cal])
        self.calibrator=LogisticRegression(C=1.,max_iter=1000,random_state=47).fit(np.log(np.clip(self.classifier.predict_proba(CX),1e-9,1)),[r['y'] for r in cal])
        self.radius=np.quantile(np.abs(np.array([r['scores'] for r in cal])-self.scores(CX)),min(1,np.ceil((len(cal)+1)*.9)/len(cal)),axis=0,method='higher')
        return self
    def probabilities(self,X):return calibrated(self.classifier.predict_proba(X),self.calibrator)
    def scores(self,X):return np.maximum(0,np.column_stack([h.predict(X) for h in self.run_heads]))


class BaseballAnalyst:
    name='baseball-starter-lineup-bullpen-analyst-v1'
    def explain(self,snap):
        v=snap['values'];out=[f"최근 득점/실점: 홈 {v['home_runs_for']:.2f}/{v['home_runs_against']:.2f}, 원정 {v['away_runs_for']:.2f}/{v['away_runs_against']:.2f}."]
        for side,label in [('home','홈'),('away','원정')]:
            out.append(f"{label} 선발 FIP: {v[side+'_starter_fip']:.2f}." if v[side+'_starter_fip_known'] else f'{label} 선발 FIP 미확인: 임의 투수 수치로 대체하지 않았습니다.')
        out.append('MLB와 KBO는 모델을 별도로 학습하며 KBO 무승부를 별도 결과로 취급합니다.')
        return out


class BaseballPlugin:
    sport='baseball';version='baseball-starter-bullpen-v1';scope='including_extra_innings'
    kinds=('starter_logistic','run_forest');analyst=BaseballAnalyst()
    features=[f'{side}_{name}' for side in ('home','away') for name in ['runs_for','runs_against','rest','bullpen_proxy_3d','lineup_confirmed']+sum(([k,k+'_known'] for k in CONTEXT_FIELDS),[])]
    def __init__(self,league):
        self.league=league;self.outcomes=['away','home'] if league=='MLB' else ['away','draw','home']
    def snapshot(self,store,fixture,cutoff,test_mode=False):
        history=histories(store,fixture,cutoff,test_mode,10);values={};context=context_as_of(store,fixture,cutoff,test_mode)
        for side,team in [('home',fixture.home_team),('away',fixture.away_team)]:
            h=history[side];scores=np.array([team_scores(g,team) for g in h]);c=context.get(team)
            values.update({side+'_runs_for':float(scores[:,0].mean()),side+'_runs_against':float(scores[:,1].mean()),
                side+'_rest':min(14.,(fixture.starts_at-h[-1].ends_at).total_seconds()/86400),
                # Schedule density is a proxy, never represented as actual bullpen pitches.
                side+'_bullpen_proxy_3d':float(sum(0<(fixture.starts_at-g.ends_at).total_seconds()<=3*86400 for g in h)),
                side+'_lineup_confirmed':float(c.lineup_confirmed) if c else 0.})
            for k in CONTEXT_FIELDS:
                value=getattr(c,k,None) if c else None
                values[side+'_'+k]=float(value) if value is not None else 0.
                values[side+'_'+k+'_known']=float(value is not None)
        lineage=[{'sha256':digest(c.model_dump(mode='json')),'context':c.model_dump(mode='json')} for c in context.values()]
        return make_snapshot(self,fixture,cutoff,history,values,lineage,test_mode)
    def fit(self,kind,fit,cal):return BaseballModel(kind).fit(fit,cal,self.features,self.outcomes)
    def review_evidence(self,history,fixture):
        out={}
        for side,team in [('home',fixture.home_team),('away',fixture.away_team)]:
            h=[g for g in history if team in (g.home_team,g.away_team)][-5:]
            out[side]={'n':len(h),'median_runs_allowed':float(np.median([team_scores(g,team)[1] for g in h])) if h else None,
                       'extra_inning_games':sum(len(g.home_periods)>g.regulation_innings for g in h),'games':[g.game_id for g in h]}
        return out
    def review_checks(self,report,evidence):
        reasons=[];counter=[]
        if report['scope']!='including_extra_innings':reasons.append('baseball full-game scope required')
        if report['fixture']['league']=='MLB' and 'draw' in report['outcome_probabilities']:reasons.append('MLB final cannot have draw class')
        for side in ('home','away'):
            context=evidence.get('pregame_context',{}).get(report['fixture'][side+'_team'])
            known=report['snapshot']['values'][side+'_starter_fip_known']
            if known and (not context or context.get('starter_fip')!=report['snapshot']['values'][side+'_starter_fip']):
                reasons.append('starter FIP inconsistent with independent source context')
        if not all(report['snapshot']['values'][s+'_starter_fip_known'] for s in ('home','away')):
            counter.append('Starting-pitcher FIP unverified; report is team-form research, not a verified pitcher matchup.')
        if not all(report['snapshot']['values'][s+'_lineup_confirmed'] for s in ('home','away')):
            counter.append('Lineups not confirmed; starter/lineup uncertainty remains.')
        if max(report['scores'])>25:reasons.append('run expectation outside supported research range')
        return reasons,counter
    limitations=['실제 선발/타선/불펜 API 미연결. 사전 context가 없으면 결측 플래그로 표시.',
                 '5이닝·이닝별 분포·구종·구속 모델 미구현. 일정 밀도는 실제 불펜 사용량이 아닙니다.',
                 'KBO 시즌별 규정의 공식 검증은 별도 필요. 연구 모델이며 실전 성능 미검증.']
