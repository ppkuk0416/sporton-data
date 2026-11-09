"""Independent sport contracts, features, models and reviewers; no NBA fallback."""
FOOTBALL_LEAGUES=('EPL','LA_LIGA','BUNDESLIGA','SERIE_A','K_LEAGUE','UEFA')
BASEBALL_LEAGUES=('MLB','KBO')
LEAGUES=('NBA',)+FOOTBALL_LEAGUES+BASEBALL_LEAGUES


def sport_for(league):
    if league=='NBA':return 'basketball'
    if league in FOOTBALL_LEAGUES:return 'football'
    if league in BASEBALL_LEAGUES:return 'baseball'
    raise ValueError(f'unsupported league: {league}')


def plugin(league):
    sport=sport_for(league)
    if sport=='football':
        from .football import FootballPlugin
        return FootballPlugin(league)
    if sport=='baseball':
        from .baseball import BaseballPlugin
        return BaseballPlugin(league)
    raise ValueError('NBA uses its own engine')
