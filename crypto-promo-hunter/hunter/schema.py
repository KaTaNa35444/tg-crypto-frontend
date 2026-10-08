from datetime import datetime, timezone
from enum import StrEnum
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

class Exchange(StrEnum):
    BYBIT = 'bybit'
    BITGET = 'bitget'
    MEXC = 'mexc'

DOMAINS = {Exchange.BYBIT: {'bybit.com', 'www.bybit.com', 'announcements.bybit.com'},
           Exchange.BITGET: {'bitget.com', 'www.bitget.com'},
           Exchange.MEXC: {'mexc.com', 'www.mexc.com'}}

class RewardType(StrEnum):
    CASH = 'withdrawable'
    BONUS = 'trading_bonus'
    VOUCHER = 'voucher'
    DRAW = 'draw'
    POOL = 'pool'
    UNKNOWN = 'unknown'


def canonical_url(url: str, exchange: Exchange) -> str:
    p = urlsplit(url)
    if (p.scheme != 'https' or p.hostname not in DOMAINS[exchange] or p.username
            or p.password or p.port not in (None, 443) or '\\' in url):
        raise ValueError('Only an HTTPS official exchange URL is allowed')
    query = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in {'ref','affiliate','referralcode'}]
    return urlunsplit(('https', p.hostname, p.path or '/', urlencode(sorted(query)), ''))


def utcnow():
    return datetime.now(timezone.utc)

class PromoInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    exchange: Exchange
    source_id: str = Field(min_length=1, max_length=180)
    official_url: str = Field(max_length=2048)
    title: str = Field(min_length=1, max_length=180)
    description: str | None = Field(default=None, max_length=400)
    reward: str | None = Field(default=None, max_length=250)
    reward_type: RewardType = RewardType.UNKNOWN
    reward_guaranteed: bool | None = None
    audience: str | None = Field(default=None, max_length=150)
    kyc: str | None = Field(default=None, max_length=180)
    countries: str | None = Field(default=None, max_length=250)
    steps: list[str] = Field(default_factory=list, max_length=8)
    deposit: str | None = Field(default=None, max_length=150)
    volume: str | None = Field(default=None, max_length=150)
    holding: str | None = Field(default=None, max_length=150)
    start_at: datetime | None = None
    end_at: datetime | None = None
    start_text: str | None = Field(default=None, max_length=100)
    end_text: str | None = Field(default=None, max_length=100)
    source_timezone: str | None = None
    limits: str | None = Field(default=None, max_length=250)
    crediting: str | None = Field(default=None, max_length=200)
    usage: str | None = Field(default=None, max_length=200)
    withdrawal: str | None = Field(default=None, max_length=200)
    raw_text: str = Field(min_length=1, max_length=100000)
    ambiguity: list[str] = Field(default_factory=list, max_length=20)
    demo: bool = False

    @field_validator('ambiguity')
    @classmethod
    def ambiguity_limit(cls,values):
        if any(len(x)>200 for x in values): raise ValueError('Ambiguity note exceeds 200 characters')
        return values

    @field_validator('steps')
    @classmethod
    def steps_limit(cls, values):
        if any(len(x)>220 for x in values):
            raise ValueError('Step exceeds 220 characters')
        return values

    @field_validator('start_at', 'end_at')
    @classmethod
    def aware(cls, dt):
        if dt and (dt.tzinfo is None or dt.utcoffset() is None):
            raise ValueError('Deadline must have an explicit timezone; otherwise use end_text')
        return dt.astimezone(timezone.utc) if dt else None

    @field_validator('source_timezone')
    @classmethod
    def timezone_valid(cls, value):
        if value:
            try: ZoneInfo(value)
            except ZoneInfoNotFoundError: raise ValueError('Unknown IANA timezone')
        return value

    @model_validator(mode='after')
    def validate_source(self):
        self.official_url = canonical_url(self.official_url, self.exchange)
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValueError('End must follow start')
        if self.reward_guaranteed and re.search(r'up\s+to|maximum|\bmax\b|максим|до\s+\d', self.reward or '',re.I):
            raise ValueError('A maximum reward cannot be asserted as guaranteed')
        if self.reward_type == RewardType.CASH and re.search(r'trading\s+bonus|торгов[а-яіїє]*\s+бонус',self.reward or '',re.I):
            raise ValueError('A trading bonus cannot be labelled withdrawable cash')
        if self.reward_type in {RewardType.POOL, RewardType.DRAW} and self.reward_guaranteed:
            raise ValueError('A pool or draw cannot be guaranteed per user')
        if self.end_text and not self.end_at and 'Deadline timezone or exact date unknown' not in self.ambiguity:
            self.ambiguity.append('Deadline timezone or exact date unknown')
        if self.reward_type == RewardType.UNKNOWN and 'Reward type unknown' not in self.ambiguity:
            self.ambiguity.append('Reward type unknown')
        return self

    def payload(self):
        return self.model_dump(mode='json')


def material_signature(data: dict) -> dict:
    # Ignore source text, check timestamps, title/description typography and discovery metadata.
    keys = {'reward','reward_type','reward_guaranteed','audience','kyc','countries','steps',
            'deposit','volume','holding','start_at','end_at','start_text','end_text',
            'source_timezone','limits','crediting','usage','withdrawal'}
    def normalize(v):
        if isinstance(v,str): return ' '.join(v.split())
        if isinstance(v,list): return [normalize(x) for x in v]
        return v
    return {k:normalize(data.get(k)) for k in sorted(keys)}
