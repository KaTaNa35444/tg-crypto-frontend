from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from hunter.schema import utcnow

class Base(DeclarativeBase): pass

class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    language: Mapped[str] = mapped_column(String(2), default='ru')
    timezone: Mapped[str] = mapped_column(String(80), default='Europe/Kyiv')
    notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Subscription(Base):
    __tablename__ = 'subscriptions'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    exchange: Mapped[str] = mapped_column(String(16), primary_key=True)

class Source(Base):
    __tablename__ = 'sources'
    exchange: Mapped[str] = mapped_column(String(16), primary_key=True)
    initialized: Mapped[bool] = mapped_column(Boolean, default=False)
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

class Promotion(Base):
    __tablename__ = 'promotions'
    __table_args__ = (UniqueConstraint('exchange','source_id'), UniqueConstraint('exchange','canonical_url'))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[str] = mapped_column(String(180))
    canonical_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default='pending')
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    candidate: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    baseline: Mapped[bool] = mapped_column(Boolean, default=False)
    demo: Mapped[bool] = mapped_column(Boolean, default=False)

class Version(Base):
    __tablename__ = 'versions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    promotion_id: Mapped[int] = mapped_column(ForeignKey('promotions.id'))
    data: Mapped[dict] = mapped_column(JSON)
    event: Mapped[str] = mapped_column(String(20))
    actor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Saved(Base):
    __tablename__ = 'saved'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    promotion_id: Mapped[int] = mapped_column(ForeignKey('promotions.id'), primary_key=True)

class Reminder(Base):
    __tablename__ = 'reminders'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    promotion_id: Mapped[int] = mapped_column(ForeignKey('promotions.id'), primary_key=True)
    minutes: Mapped[int] = mapped_column(Integer)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[str] = mapped_column(String(50))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(200), unique=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    promotion_id: Mapped[int | None] = mapped_column(ForeignKey('promotions.id'), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(16), default='pending', index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    expected_deadline: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error: Mapped[str | None] = mapped_column(String(100), nullable=True)


def database(url):
    engine = create_async_engine(url, pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def aware(dt):
    from datetime import timezone
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
