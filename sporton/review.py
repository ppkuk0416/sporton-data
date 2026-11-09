"""Independent deterministic reviewer; no LLM or artificial role-name independence."""
from datetime import datetime
from uuid import uuid4
import numpy as np
from .contracts import Game, Fixture, utcnow
from .storage import digest


class NBACriticalAnalyst:
    def blind_assessment(self, store, fixture, cutoff, test_mode=False):
        # Independent raw-record checks and medians, not analyst feature vector or recommendation.
        from .governance import games_as_of
        games=games_as_of(store,'NBA',cutoff)
        quality='TEST/FIXTURE' if test_mode else 'verified'
        allowed=[g for g in games if g.published_at<=cutoff and g.ends_at<cutoff and g.quality==quality and g.game_id!=fixture.game_id]
        evidence={}
        for side,team in [('home',fixture.home_team),('away',fixture.away_team)]:
            history=sorted([g for g in allowed if team in (g.home_team,g.away_team)],key=lambda g:g.starts_at)[-5:]
            margins=[(g.home_score-g.away_score)*(1 if g.home_team==team else -1) for g in history]
            evidence[side]={'n':len(history),'median_margin':float(np.median(margins)) if margins else None,'games':[g.game_id for g in history]}
        from .data import injuries_as_of
        evidence['known_injuries']=injuries_as_of(store,fixture.game_id,cutoff,test_mode)
        evidence['independent_margin']=None if not evidence['home']['n'] or not evidence['away']['n'] else evidence['home']['median_margin']-evidence['away']['median_margin']
        evidence.update({'fixture_id':fixture.game_id,'cutoff':cutoff.isoformat(),'created_at':utcnow().isoformat(),
                         'limitations':['No verified injury/lineup feed; not asserted complete.','Median margin is an independent heuristic, not a second probability model.']})
        key=str(uuid4()); store.append('review_evidence',key,evidence)
        return key,evidence

    def review(self,store,report,evidence_id,meta,market=None):
        # Blind evidence must have already been saved before report creation.
        evidence=store.get('review_evidence',evidence_id)
        reasons=[]; counter=[]
        if not evidence or evidence['fixture_id']!=report['fixture']['game_id']:
            reasons.append('independent evidence unavailable')
        elif evidence['created_at']>report['created_at']:
            reasons.append('blind assessment was not completed first')
        now=utcnow(); starts=datetime.fromisoformat(report['fixture']['starts_at'])
        cutoff=datetime.fromisoformat(report['snapshot']['feature_cutoff_timestamp'])
        if now>=starts or cutoff>now:
            reasons.append('analysis must precede tipoff and cutoff must not be future')
        if report['snapshot']['minimum_team_games']<3:
            reasons.append('insufficient sample')
        if datetime.fromisoformat(meta['selection_end'])>cutoff or datetime.fromisoformat(meta['trained_at'])>cutoff:
            reasons.append('model or selection evidence was unavailable at cutoff')
        if not 0<report['home_win_probability']<1:
            reasons.append('invalid probability')
        if any(x<0 for x in report['scores']):
            reasons.append('negative predicted score')
        if abs(report['margin']-(report['scores'][0]-report['scores'][1]))>1e-8:
            reasons.append('margin inconsistent')
        if evidence and evidence.get('independent_margin') is not None:
            independent=evidence['independent_margin']
            if independent*report['margin']<0:
                counter.append('Independent recent-margin assessment favors the opposite side.')
            if abs(independent-report['margin'])>20:
                reasons.append('large disagreement with independent recent-margin evidence')
        if market:
            if market['market_type']=='three_way':reasons.append('NBA cannot use three-way result market')
            if market['game_id']!=report['fixture']['game_id'] or market['scope']!='including_overtime':
                reasons.append('market fixture/scope mismatch')
            if datetime.fromisoformat(market['collected_at'])>cutoff:
                reasons.append('market unavailable at cutoff')
            if (cutoff-datetime.fromisoformat(market['collected_at'])).total_seconds()>900:
                reasons.append('market older than 15 minutes')
            if market['quality'] not in ('verified','TEST/FIXTURE'):
                reasons.append('unverified market source')
        result={'prediction_id':report['id'],'evidence_id':evidence_id,'report_hash':digest(report),
                'verdict':'NO' if reasons else 'PASS','reasons':reasons or ['Point-in-time lineage and score consistency verified; limitations disclosed.'],
                'counter_evidence':counter,'at':utcnow().isoformat(),'reviewer':'nba-independent-rules-v1',
                'meaning':'Publication review only; no outcome/profit guarantee.'}
        key=str(uuid4()); store.append('critical_reviews',key,result)
        return key,result
