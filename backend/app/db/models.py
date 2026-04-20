"""ORM-модели. Зеркалят схему из db/migrations/0001_init.sql.

Таблицы создаёт DBA вручную через SQL — мы тут только описываем mapping
для запросов из Python. Это упрощает миграции на проде.
"""

from __future__ import annotations

from datetime import datetime, time, date
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    first_name: Mapped[Optional[str]] = mapped_column(String(128))
    last_name: Mapped[Optional[str]] = mapped_column(String(128))
    language_code: Mapped[Optional[str]] = mapped_column(String(8))

    subscription_until: Mapped[Optional[datetime]] = mapped_column(DateTime)

    reminder_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(19, 0))
    reminder_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    used_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mode: Mapped[str] = mapped_column(
        SAEnum("voice", "chat", name="session_mode"), nullable=False, default="voice"
    )
    level: Mapped[Optional[str]] = mapped_column(String(8))
    role: Mapped[Optional[str]] = mapped_column(String(64))


class DailyUsage(Base):
    __tablename__ = "daily_usage"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    used_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    amount_rub: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    plan: Mapped[str] = mapped_column(
        SAEnum("monthly", "yearly", "gift", "admin_grant", name="payment_plan"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        SAEnum("pending", "succeeded", "canceled", "refunded", name="payment_status"),
        nullable=False,
        default="pending",
    )
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    days_granted: Mapped[int] = mapped_column(Integer, nullable=False)
    granted_by_tg_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    notes: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SettingKV(Base):
    __tablename__ = "settings_kv"

    key: Mapped[str] = mapped_column("key", String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
