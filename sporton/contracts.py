from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow():
    return datetime.now(timezone.utc)


def aware(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timezone required')
    return value.astimezone(timezone.utc)


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    collected_at: datetime
    published_at: datetime
    data_version: str = Field(min_length=1)
    quality: Literal['verified', 'unverified', 'TEST/FIXTURE']

    @field_validator('collected_at', 'published_at')
    @classmethod
    def timestamps(cls, v):
        return aware(v)

    @model_validator(mode='after')
    def chronology(self):
        if self.published_at > self.collected_at:
            raise ValueError('publication cannot follow collection')
        return self


class Game(Record):
    game_id: str = Field(min_length=1)
    league: Literal['NBA'] = 'NBA'
    season: str
    starts_at: datetime
    ends_at: datetime
    home_team: str
    away_team: str
    home_score: int = Field(ge=0)
    away_score: int = Field(ge=0)
    # First 4 periods are regulation; remaining periods are overtime.
    home_periods: list[int] = Field(min_length=4)
    away_periods: list[int] = Field(min_length=4)
    home_possessions: float | None = Field(default=None, gt=0)
    away_possessions: float | None = Field(default=None, gt=0)

    @field_validator('starts_at', 'ends_at')
    @classmethod
    def times(cls, v):
        return aware(v)

    @model_validator(mode='after')
    def consistent(self):
        if self.home_team == self.away_team or self.home_score == self.away_score:
            raise ValueError('NBA final requires distinct teams and no tie')
        if not self.starts_at < self.ends_at <= self.published_at:
            raise ValueError('result must be published after game end')
        if len(self.home_periods) != len(self.away_periods):
            raise ValueError('period counts differ')
        if any(p < 0 for p in self.home_periods + self.away_periods):
            raise ValueError('negative period score')
        if sum(self.home_periods) != self.home_score or sum(self.away_periods) != self.away_score:
            raise ValueError('period totals differ from final score')
        if len(self.home_periods) > 4 and sum(self.home_periods[:4]) != sum(self.away_periods[:4]):
            raise ValueError('overtime requires tied regulation score')
        for i in range(4, len(self.home_periods)-1):
            if sum(self.home_periods[:i+1]) != sum(self.away_periods[:i+1]):
                raise ValueError('additional overtime requires preceding tie')
        return self


class Fixture(BaseModel):
    model_config = ConfigDict(extra='forbid')
    game_id: str
    league: Literal['NBA'] = 'NBA'
    season: str
    starts_at: datetime
    home_team: str
    away_team: str

    @field_validator('starts_at')
    @classmethod
    def times(cls, v):
        return aware(v)

    @model_validator(mode='after')
    def distinct(self):
        if self.home_team == self.away_team:
            raise ValueError('teams must differ')
        return self


class Market(Record):
    game_id: str
    market_type: Literal['moneyline', 'three_way', 'spread', 'total']
    scope: Literal['including_overtime', 'including_extra_innings', 'regulation']
    selection: Literal['home', 'away', 'draw', 'over', 'under']
    line: float | None = Field(default=None, allow_inf_nan=False)
    decimal_odds: float = Field(gt=1, allow_inf_nan=False)
    bookmaker: str = Field(min_length=1)

    @model_validator(mode='after')
    def valid_market(self):
        if self.market_type in ('moneyline','three_way'):
            allowed=('home','away','draw') if self.market_type=='three_way' else ('home','away')
            if self.line is not None or self.selection not in allowed:
                raise ValueError('invalid moneyline')
        elif self.line is None:
            raise ValueError('line required')
        elif self.market_type == 'total' and self.selection not in ('over', 'under'):
            raise ValueError('invalid total side')
        elif self.market_type == 'spread' and self.selection not in ('home', 'away'):
            raise ValueError('invalid spread side')
        return self


class Injury(Record):
    game_id: str
    player_id: str
    team_id: str
    status: Literal['out', 'doubtful', 'questionable', 'probable', 'available']
