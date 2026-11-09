import csv
import json
from typing import Protocol
from uuid import uuid4
import httpx
from .contracts import Game, Market, utcnow
from .storage import digest
from .sports.contracts import parse_game
from .sports import sport_for


class DataSourceAdapter(Protocol):
    def collect(self) -> list[Game]: ...


class CSVAdapter:
    """Strict normalized CSV; lists encoded as JSON. No guessed timestamps."""
    def __init__(self, path):
        self.path = path

    def collect(self):
        with open(self.path, newline='', encoding='utf-8-sig') as f:
            result = []
            for row in csv.DictReader(f):
                for key in ('home_periods', 'away_periods'):
                    row[key] = json.loads(row[key])
                for key in ('home_possessions', 'away_possessions'):
                    if row.get(key) == '':
                        row[key] = None
                for key in list(row):
                    if row[key]=='':row.pop(key)
                result.append(parse_game(row))
            return result


def ingest(store, path, test_mode=False):
    with open(path, 'rb') as f:
        content = f.read()
    # Raw bytes retained before normalization, even if validation fails.
    raw = {'adapter': 'csv-v1', 'content_utf8': content.decode('utf-8-sig'), 'collected_at': utcnow().isoformat()}
    try:
        raw_rows=list(csv.DictReader(content.decode('utf-8-sig').splitlines()))
        if any((r.get('quality')=='TEST/FIXTURE')!=test_mode for r in raw_rows):
            raise ValueError('TEST/FIXTURE and real data require separate stores/modes')
        store.append('raw_data', str(uuid4()), raw)
        games = CSVAdapter(path).collect()
        if any((g.quality == 'TEST/FIXTURE') != test_mode for g in games):
            raise ValueError('TEST/FIXTURE and real data require separate stores/modes')
        if not test_mode and any(g.collected_at > utcnow() for g in games):
            raise ValueError('real data collection cannot be in the future')
        if len({g.game_id for g in games}) != len(games):
            raise ValueError('duplicate game IDs within import batch')
        previous = store.rows('historical_game_results')
        if any((r['payload']['quality'] == 'TEST/FIXTURE') != test_mode for r in previous):
            raise ValueError('cannot mix fixture and real data in one store')
        for g in games:
            p = g.model_dump(mode='json')
            fixture = {k:p[k] for k in ('game_id','league','season','starts_at','home_team','away_team')}
            old_fixture = store.get('fixtures',g.game_id)
            if old_fixture and old_fixture!=fixture:
                raise ValueError(f'conflicting fixture {g.game_id}')
            old = store.get('historical_game_results', g.game_id)
            if old and old != g.model_dump(mode='json'):
                raise ValueError(f'conflicting result {g.game_id}')
        for g in games:
            p = g.model_dump(mode='json')
            store.append('historical_game_results', g.game_id, p, idempotent=True)
            store.append('fixtures', g.game_id, {k: p[k] for k in ('game_id','league','season','starts_at','home_team','away_team')}, idempotent=True)
            store.append('actual_results', g.game_id, p, idempotent=True)
            sport=sport_for(g.league)
            for table, key, entity in [('sports',sport,{'name':sport}), ('leagues',g.league,{'sport':sport,'name':g.league}), ('seasons',g.season if g.league=='NBA' else g.league+':'+g.season,{'league':g.league,'season':g.season}), ('teams',g.home_team if g.league=='NBA' else g.league+':'+g.home_team,{'league':g.league,'name':g.home_team}), ('teams',g.away_team if g.league=='NBA' else g.league+':'+g.away_team,{'league':g.league,'name':g.away_team})]:
                store.append(table, key, entity, idempotent=True)
        return len(games)
    except Exception as exc:
        store.append('source_failures', str(uuid4()), {'adapter':'csv-v1','error':str(exc),'at':utcnow().isoformat()})
        raise


def collect_nba(store, seasons):
    """Official NBA historical transport. Preserve raw; never invent unavailable metadata.

    LeagueGameLog only gives date, not actual tipoff/publication or period scores.
    It cannot enter verified training until joined with timestamped boxscores.
    """
    results = []
    for season in seasons:
        url = 'https://stats.nba.com/stats/leaguegamelog'
        params = {'LeagueID':'00','Season':season,'SeasonType':'Regular Season','PlayerOrTeam':'T','Sorter':'DATE','Direction':'ASC'}
        try:
            response = httpx.get(url, params=params, timeout=25, headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.nba.com/'})
            response.raise_for_status()
            body = response.json()
            sets = body.get('resultSets', [])
            if not sets or not sets[0].get('rowSet'):
                raise ValueError('NBA returned no games')
            payload = {'source':'NBA official LeagueGameLog','source_id':str(response.url),'season':season,'collected_at':utcnow().isoformat(),'quality':'timestamp_unknown','body':body}
            store.append('raw_data', digest(payload), payload)
            results.append({'season':season,'team_game_rows':len(sets[0]['rowSet']),'status':'raw_only'})
        except Exception as exc:
            failure = {'season':season,'source':url,'error':str(exc),'at':utcnow().isoformat()}
            store.append('source_failures', str(uuid4()), failure)
            results.append({**failure,'status':'failed'})
    return results


def add_market(store, payload, test_mode=False):
    market = Market.model_validate(payload)
    if (market.quality == 'TEST/FIXTURE') != test_mode:
        raise ValueError('market data mode mismatch')
    fixture = store.get('fixtures', market.game_id)
    if not fixture:
        raise ValueError('unknown fixture')
    from datetime import datetime
    if market.collected_at >= datetime.fromisoformat(fixture['starts_at']):
        raise ValueError('market snapshot must precede tipoff')
    p = market.model_dump(mode='json')
    return store.append('market_snapshots', digest(p), p, idempotent=True)


def add_injury(store,payload,test_mode=False):
    from .contracts import Injury
    injury=Injury.model_validate(payload)
    if (injury.quality=='TEST/FIXTURE')!=test_mode:
        raise ValueError('injury data mode mismatch')
    if not store.get('fixtures',injury.game_id):
        raise ValueError('unknown fixture')
    p=injury.model_dump(mode='json')
    return store.append('injuries',digest(p),p,idempotent=True)


def injuries_as_of(store,game_id,cutoff,test_mode=False):
    from datetime import datetime
    allowed='TEST/FIXTURE' if test_mode else 'verified'
    known=[r['payload'] for r in store.rows('injuries') if r['payload']['game_id']==game_id
           and r['payload']['quality']==allowed
           and datetime.fromisoformat(r['payload']['published_at'])<=cutoff
           and datetime.fromisoformat(r['payload']['collected_at'])<=cutoff]
    latest={}
    for r in sorted(known,key=lambda p:p['published_at']):latest[r['player_id']]=r
    return list(latest.values())


def add_baseball_context(store,payload,test_mode=False):
    from .sports.contracts import BaseballContext
    c=BaseballContext.model_validate(payload)
    if (c.quality=='TEST/FIXTURE')!=test_mode:raise ValueError('context data mode mismatch')
    if not test_mode and c.collected_at>utcnow():raise ValueError('future real collection timestamp')
    f=store.get('fixtures',c.game_id)
    if not f or f['league']!=c.league or c.team_id not in (f['home_team'],f['away_team']):raise ValueError('context fixture/league/team mismatch')
    p=c.model_dump(mode='json');p['kind']='baseball_pregame'
    return store.append('team_statistics',digest(p),p,idempotent=True)
