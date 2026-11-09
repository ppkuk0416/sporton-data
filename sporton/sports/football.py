"""90-minute goal model and 3-way analyst. No basketball pace or NBA coefficients."""
import numpy as np
from scipy.stats import skellam
from sklearn.linear_model import PoissonRegressor,LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from .common import histories,team_scores,make_snapshot,calibrated


class FootballModel:
    def __init__(self,kind):self.kind=kind
    def fit(self,fit,cal,features,outcomes):
        X=np.array([r['x'] for r in fit]);scores=np.array([r['scores'] for r in fit])
        self.goal_heads=[make_pipeline(StandardScaler(),PoissonRegressor(alpha=1.,max_iter=1000)).fit(X,scores[:,i]) for i in range(2)]
        if self.kind=='form_logistic':
            self.classifier=make_pipeline(StandardScaler(),LogisticRegression(C=.5,max_iter=1000,random_state=23)).fit(X,[r['y'] for r in fit])
        CX=np.array([r['x'] for r in cal])
        self.calibrator=LogisticRegression(C=1.,max_iter=1000,random_state=23).fit(np.log(np.clip(self.base(CX),1e-9,1)),[r['y'] for r in cal])
        self.radius=np.quantile(np.abs(np.array([r['scores'] for r in cal])-self.scores(CX)),min(1,np.ceil((len(cal)+1)*.9)/len(cal)),axis=0,method='higher')
        return self
    def scores(self,X):return np.maximum(.01,np.column_stack([h.predict(X) for h in self.goal_heads]))
    def base(self,X):
        if self.kind=='form_logistic':return self.classifier.predict_proba(X)
        goals=self.scores(X);h,a=goals[:,0],goals[:,1]
        return np.column_stack([skellam.cdf(-1,h,a),skellam.pmf(0,h,a),1-skellam.cdf(0,h,a)])
    def probabilities(self,X):return calibrated(self.base(X),self.calibrator)


class FootballAnalyst:
    name='football-goal-tactics-analyst-v1'
    def explain(self,snap):
        v=snap['values']
        return [f"90분 기준 최근 득점/실점: 홈 {v['home_goals_for']:.2f}/{v['home_goals_against']:.2f}, 원정 {v['away_goals_for']:.2f}/{v['away_goals_against']:.2f}.",
                '무승부를 독립 결과로 모델링하며 연장전·승부차기는 예측 범위에서 제외합니다.',
                'xG/PPDA가 누락되면 결측으로 처리하고 실제 전술 우위가 확인된 것처럼 설명하지 않습니다.']


class FootballPlugin:
    sport='football';version='football-form-xg-v1';scope='regulation'
    outcomes=['away','draw','home'];kinds=('goal_poisson','form_logistic')
    analyst=FootballAnalyst()
    features=[f'{side}_{name}' for side in ('home','away') for name in ('goals_for','goals_against','draw_rate','rest','xg_for','xg_against','ppda','advanced_known')]
    def __init__(self,league):self.league=league
    def snapshot(self,store,fixture,cutoff,test_mode=False):
        history=histories(store,fixture,cutoff,test_mode,8);values={}
        for side,team in [('home',fixture.home_team),('away',fixture.away_team)]:
            h=history[side];scores=np.array([team_scores(g,team) for g in h]);xg=[];xga=[];ppda=[]
            for g in h:
                home=g.home_team==team
                xf,xa=(g.home_xg,g.away_xg) if home else (g.away_xg,g.home_xg)
                pressure=g.home_ppda if home else g.away_ppda
                if xf is not None:xg.append(xf)
                if xa is not None:xga.append(xa)
                if pressure is not None:ppda.append(pressure)
            values.update({side+'_goals_for':float(scores[:,0].mean()),side+'_goals_against':float(scores[:,1].mean()),
                side+'_draw_rate':float(np.mean(scores[:,0]==scores[:,1])),side+'_rest':min(21.,(fixture.starts_at-h[-1].ends_at).total_seconds()/86400),
                side+'_xg_for':float(np.mean(xg)) if xg else 0.,side+'_xg_against':float(np.mean(xga)) if xga else 0.,
                side+'_ppda':float(np.mean(ppda)) if ppda else 0.,side+'_advanced_known':float(len(xg)==len(h) and len(xga)==len(h) and len(ppda)==len(h))})
        return make_snapshot(self,fixture,cutoff,history,values,test_mode=test_mode)
    def fit(self,kind,fit,cal):return FootballModel(kind).fit(fit,cal,self.features,self.outcomes)
    def review_evidence(self,history,fixture):
        out={}
        for side,team in [('home',fixture.home_team),('away',fixture.away_team)]:
            h=[g for g in history if team in (g.home_team,g.away_team)][-5:]
            out[side]={'n':len(h),'median_goal_difference':float(np.median([a-b for a,b in [team_scores(g,team) for g in h]])) if h else None,
                       'clean_sheets':sum(team_scores(g,team)[1]==0 for g in h),'games':[g.game_id for g in h]}
        return out
    def review_checks(self,report,evidence):
        reasons=[];counter=[]
        if report['scope']!='regulation':reasons.append('football requires 90-minute scope')
        home=evidence['home']['median_goal_difference'];away=evidence['away']['median_goal_difference']
        if home is not None and away is not None and (home-away)*report['margin']<0:
            counter.append('Recent independent goal-difference form favors the opposite side.')
        if max(report['scores'])>10:reasons.append('goal expectation outside supported research range')
        return reasons,counter
    limitations=['생성형 LLM 및 포메이션·선발·부상·세트피스 공급자 미연결.',
                 '독립 Poisson 가정은 득점 상관·전술 변화에 취약하며 실데이터 성능 미검증.',
                 '득점/확률은 90분 기준. 연장전·승부차기 승자와 시장선 확률은 미지원.']
