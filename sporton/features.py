"""NBA-only, point-in-time team aggregates; current target never enters its features."""
from datetime import datetime
import numpy as np
from .contracts import Game, Fixture, aware
from .storage import digest

FEATURE_VERSION = 'nba-rolling-v1'
FEATURES = ['home_for','home_against','away_for','away_against','home_rest','away_rest','home_elo','away_elo','home_net','away_net','home_pace','away_pace','home_rtg','away_rtg','possessions_known']


def snapshot(games: list[Game], fixture: Fixture, cutoff: datetime, test_mode=False):
    cutoff = aware(cutoff)
    if cutoff >= fixture.starts_at:
        raise ValueError('feature cutoff must precede game')
    allowed_quality = 'TEST/FIXTURE' if test_mode else 'verified'
    past = sorted([g for g in games if g.game_id != fixture.game_id and g.league == fixture.league
                   and g.published_at <= cutoff and g.ends_at < cutoff and g.quality == allowed_quality],
                  key=lambda g:(g.starts_at,g.game_id))
    elo = {}
    for g in past:
        h,a = elo.get(g.home_team,1500.),elo.get(g.away_team,1500.)
        expected = 1 / (1+10**((a-h-60)/400))
        delta = 20 * (int(g.home_score > g.away_score)-expected)
        elo[g.home_team],elo[g.away_team] = h+delta,a-delta
    values = {}
    source_ids = set()
    minimum = 999
    possessions_known = True
    for prefix, team in [('home',fixture.home_team),('away',fixture.away_team)]:
        history = [g for g in past if team in (g.home_team,g.away_team)][-10:]
        minimum = min(minimum,len(history))
        if len(history) < 3:
            raise ValueError(f'insufficient verified history for {team}: {len(history)} < 3')
        points_for,points_against,pace,rtg = [],[],[],[]
        for g in history:
            source_ids.add(g.game_id)
            h = team == g.home_team
            pf,pa = (g.home_score,g.away_score) if h else (g.away_score,g.home_score)
            points_for.append(pf); points_against.append(pa)
            poss = g.home_possessions if h else g.away_possessions
            if poss:
                pace.append(poss * 48/(48+5*(len(g.home_periods)-4)))
                rtg.append(100*pf/poss)
        if len(pace) != len(history):
            possessions_known = False
        values.update({prefix+'_for':float(np.mean(points_for)),prefix+'_against':float(np.mean(points_against)),
                       prefix+'_net':float(np.mean(np.array(points_for)-points_against)),
                       prefix+'_rest':min(14.,(fixture.starts_at-history[-1].ends_at).total_seconds()/86400),
                       prefix+'_elo':elo.get(team,1500.),prefix+'_pace':float(np.mean(pace)) if pace else 0.,
                       prefix+'_rtg':float(np.mean(rtg)) if rtg else 0.})
    values['possessions_known'] = float(possessions_known)
    # Elo depends on all earlier games: include their hashes in lineage.
    provenance = [{'id':g.game_id,'record_version':getattr(g,'_record_version','original'),'sha256':digest(g.model_dump(mode='json'))} for g in past]
    return {'fixture':fixture.model_dump(mode='json'),'as_of_timestamp':cutoff.isoformat(),
            'feature_cutoff_timestamp':cutoff.isoformat(),'feature_version':FEATURE_VERSION,
            'values':values,'minimum_team_games':minimum,'source_game_ids':sorted(source_ids),
            'lineage_hash':digest(provenance),'latest_information':max(getattr(g,'_knowledge_at',g.published_at) for g in past).isoformat(),
            'possessions_complete':possessions_known,'data_mode':'TEST/FIXTURE' if test_mode else 'real'}


def dataset(games, test_mode=False, store=None):
    from datetime import timedelta
    rows=[]
    for g in sorted(games,key=lambda g:(g.starts_at,g.game_id)):
        if g.league!='NBA' or g.quality != ('TEST/FIXTURE' if test_mode else 'verified'):
            continue
        fixture=Fixture.model_validate({k:v for k,v in g.model_dump().items() if k in Fixture.model_fields})
        try:
            cutoff=g.starts_at-timedelta(minutes=30)
            if store is not None:
                from .governance import games_as_of
                history=games_as_of(store,'NBA',cutoff)
            else:history=games
            s=snapshot(history,fixture,cutoff,test_mode)
        except ValueError:
            continue
        rows.append({'game':g,'snapshot':s,'x':[s['values'][k] for k in FEATURES],
                     'y':int(g.home_score>g.away_score),'scores':[g.home_score,g.away_score]})
    return rows
