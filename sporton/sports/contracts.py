from datetime import datetime
from typing import Literal
from pydantic import Field, field_validator, model_validator
from ..contracts import Record, Fixture, Game, aware
from . import sport_for


class SportFixture(Fixture):
    league: Literal['EPL','LA_LIGA','BUNDESLIGA','SERIE_A','K_LEAGUE','UEFA','MLB','KBO']


class Result(Record):
    game_id:str=Field(min_length=1)
    season:str
    starts_at:datetime
    ends_at:datetime
    home_team:str
    away_team:str
    home_score:int=Field(ge=0)
    away_score:int=Field(ge=0)

    @field_validator('starts_at','ends_at')
    @classmethod
    def times(cls,v):return aware(v)

    @model_validator(mode='after')
    def chronology(self):
        super().chronology()
        if self.home_team==self.away_team:raise ValueError('teams must differ')
        if not self.starts_at<self.ends_at<=self.published_at:
            raise ValueError('result must be published after game end')
        return self


class FootballGame(Result):
    league:Literal['EPL','LA_LIGA','BUNDESLIGA','SERIE_A','K_LEAGUE','UEFA']
    # Scores always refer to 90 minutes + stoppage time, excluding extra time/penalties.
    scope:Literal['regulation']='regulation'
    home_periods:list[int]=Field(min_length=2,max_length=2)
    away_periods:list[int]=Field(min_length=2,max_length=2)
    home_xg:float|None=Field(default=None,ge=0,allow_inf_nan=False)
    away_xg:float|None=Field(default=None,ge=0,allow_inf_nan=False)
    home_ppda:float|None=Field(default=None,gt=0,allow_inf_nan=False)
    away_ppda:float|None=Field(default=None,gt=0,allow_inf_nan=False)

    @model_validator(mode='after')
    def periods(self):
        if any(v<0 for v in self.home_periods+self.away_periods):raise ValueError('negative half score')
        if sum(self.home_periods)!=self.home_score or sum(self.away_periods)!=self.away_score:
            raise ValueError('90-minute half totals differ from regulation result')
        return self


class BaseballGame(Result):
    league:Literal['MLB','KBO']
    scope:Literal['including_extra_innings']='including_extra_innings'
    regulation_innings:int=Field(default=9,ge=5,le=9)
    home_periods:list[int|None]=Field(min_length=5)
    away_periods:list[int]=Field(min_length=5)
    rules_version:str=Field(min_length=1)

    @model_validator(mode='after')
    def innings(self):
        h,a=self.home_periods,self.away_periods
        if len(h)!=len(a) or len(h)<self.regulation_innings:raise ValueError('invalid inning count')
        if any(v<0 for v in a) or any(v is not None and v<0 for v in h):raise ValueError('negative runs')
        if any(v is None for v in h[:-1]):raise ValueError('unplayed home half only allowed last')
        if h[-1] is None and sum(v or 0 for v in h)<=sum(a):raise ValueError('unplayed bottom half requires home lead')
        if sum(v or 0 for v in h)!=self.home_score or sum(a)!=self.away_score:raise ValueError('inning totals differ')
        for i in range(self.regulation_innings,len(h)):
            if sum(v or 0 for v in h[:i])!=sum(a[:i]):raise ValueError('extra innings require previous tie')
        if self.league=='MLB' and self.home_score==self.away_score:raise ValueError('MLB completed game cannot tie')
        return self


class BaseballContext(Record):
    """A sourced pre-game snapshot, never statistics from the target game."""
    game_id:str
    league:Literal['MLB','KBO']
    team_id:str
    starter_id:str|None=None
    starter_fip:float|None=Field(default=None,ge=0,allow_inf_nan=False)
    starter_k_bb:float|None=Field(default=None,ge=-1,le=1,allow_inf_nan=False)
    starter_last_pitch_count:float|None=Field(default=None,ge=0,allow_inf_nan=False)
    starter_rest_days:float|None=Field(default=None,ge=0,allow_inf_nan=False)
    lineup_wrc_plus:float|None=Field(default=None,ge=0,allow_inf_nan=False)
    bullpen_pitches_3d:float|None=Field(default=None,ge=0,allow_inf_nan=False)
    park_factor:float|None=Field(default=None,gt=0,allow_inf_nan=False)
    temperature_c:float|None=Field(default=None,ge=-50,le=60,allow_inf_nan=False)
    lineup_confirmed:bool=False


def parse_fixture(payload):
    return Fixture.model_validate(payload) if payload.get('league','NBA')=='NBA' else SportFixture.model_validate(payload)


def parse_game(payload):
    sport=sport_for(payload.get('league','NBA'))
    cls={'basketball':Game,'football':FootballGame,'baseball':BaseballGame}[sport]
    return cls.model_validate(payload)
