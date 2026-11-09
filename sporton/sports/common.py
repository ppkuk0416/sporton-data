from datetime import datetime, timedelta
import numpy as np
from ..contracts import aware
from ..storage import digest
from .contracts import parse_fixture,parse_game


def available(store,league,cutoff,test_mode):
    quality='TEST/FIXTURE' if test_mode else 'verified'
    from ..governance import games_as_of
    games=games_as_of(store,league,cutoff)
    return sorted([g for g in games if g.quality==quality and g.published_at<=cutoff and g.ends_at<cutoff],key=lambda g:(g.starts_at,g.game_id))


def histories(store,fixture,cutoff,test_mode,window):
    cutoff=aware(cutoff)
    if cutoff>=fixture.starts_at:raise ValueError('feature cutoff must precede game')
    past=[g for g in available(store,fixture.league,cutoff,test_mode) if g.game_id!=fixture.game_id]
    histories={}
    for side,team in [('home',fixture.home_team),('away',fixture.away_team)]:
        h=[g for g in past if team in (g.home_team,g.away_team)][-window:]
        if len(h)<3:raise ValueError(f'insufficient {fixture.league} history for {team}')
        histories[side]=h
    return histories


def team_scores(game,team):
    return (game.home_score,game.away_score) if game.home_team==team else (game.away_score,game.home_score)


def make_snapshot(plugin,fixture,cutoff,history,values,extra=None,test_mode=False):
    source={g.game_id:g for h in history.values() for g in h}
    return {'fixture':fixture.model_dump(mode='json'),'sport':plugin.sport,'league':fixture.league,
        'feature_version':plugin.version,'as_of_timestamp':cutoff.isoformat(),'feature_cutoff_timestamp':cutoff.isoformat(),
        'values':values,'source_game_ids':sorted(source),'minimum_team_games':min(map(len,history.values())),
        'lineage_hash':digest([source[k].model_dump(mode='json') for k in sorted(source)]),
        'context_lineage':extra or [],'latest_information':max(getattr(g,'_knowledge_at',g.published_at) for g in source.values()).isoformat(),
        'data_mode':'TEST/FIXTURE' if test_mode else 'real'}


def rows_for(store,plugin,test_mode=False):
    from ..governance import games_as_of
    games=[g for g in games_as_of(store,plugin.league) if g.quality==('TEST/FIXTURE' if test_mode else 'verified')]
    rows=[]
    for g in sorted(games,key=lambda g:(g.starts_at,g.game_id)):
        f=parse_fixture({k:g.model_dump()[k] for k in ('game_id','league','season','starts_at','home_team','away_team')})
        try:s=plugin.snapshot(store,f,g.starts_at-timedelta(minutes=30),test_mode)
        except ValueError:continue
        outcome='draw' if g.home_score==g.away_score else 'home' if g.home_score>g.away_score else 'away'
        rows.append({'game':g,'snapshot':s,'x':[s['values'][k] for k in plugin.features],
                     'y':plugin.outcomes.index(outcome),'scores':[g.home_score,g.away_score]})
    return rows


def calibrated(base,calibrator):
    return calibrator.predict_proba(np.log(np.clip(base,1e-9,1)))
