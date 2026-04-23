"""ORM-модели. Зеркалят схему из db/migrations/0001_init.sql.

Таблицы создаёт DBA вручную через SQL — мы тут только описываем mapping
для запросов из Python. Это упрощает миграции на проде.
"""

from __future__ import annotations

from datetime import datetime, time, date
from typing import Optional

from sqlalchemy import (
    JSON,
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
    # Добавлено в миграции 0002: бонус от выполненного квеста (сбрасывается вместе с днём).
    bonus_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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


# ─── Battle Mode (миграция 0002) ───────────────────────────────────────

class Battle(Base):
    """Дуэль между двумя юзерами — inline-вызов в групповом чате."""

    __tablename__ = "battles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    initiator_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    opponent_tg_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_message_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    topic_key: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(
        SAEnum(
            "open", "accepted", "recording",
            "judged", "expired", "canceled",
            name="battle_status",
        ),
        nullable=False,
        default="open",
    )

    a_audio_path: Mapped[Optional[str]] = mapped_column(String(500))
    b_audio_path: Mapped[Optional[str]] = mapped_column(String(500))
    a_transcript: Mapped[Optional[str]] = mapped_column(Text)
    b_transcript: Mapped[Optional[str]] = mapped_column(Text)

    a_score: Mapped[Optional[dict]] = mapped_column(JSON)
    b_score: Mapped[Optional[dict]] = mapped_column(JSON)

    winner: Mapped[Optional[str]] = mapped_column(
        SAEnum("a", "b", "tie", name="battle_winner")
    )
    judge_comment: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# ─── Daily Quests (миграция 0002) ────────────────────────────────────

class QuestCatalog(Base):
    """Статический каталог квестов. Пополняется миграциями (INSERT IGNORE)."""

    __tablename__ = "quests_catalog"

    key: Mapped[str] = mapped_column("key", String(64), primary_key=True)

    type: Mapped[str] = mapped_column(
        SAEnum("lexical", "grammar", "role", name="quest_type"),
        nullable=False,
    )
    difficulty: Mapped[str] = mapped_column(
        SAEnum("easy", "medium", "hard", name="quest_difficulty"),
        nullable=False,
        default="medium",
    )
    target_level: Mapped[str] = mapped_column(
        SAEnum("A2", "B1", "B2", "C1", "any", name="quest_level"),
        nullable=False,
        default="any",
    )

    title_ru: Mapped[str] = mapped_column(String(200), nullable=False)
    description_ru: Mapped[str] = mapped_column(String(500), nullable=False)

    verification_rule: Mapped[dict] = mapped_column(JSON, nullable=False)

    reward_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=1800)
    badge_key: Mapped[Optional[str]] = mapped_column(String(64))

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class UserQuest(Base):
    """Назначенный юзеру квест (один на день) + статус выполнения."""

    __tablename__ = "user_quests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    quest_key: Mapped[str] = mapped_column(
        String(64), ForeignKey("quests_catalog.key", ondelete="CASCADE"), nullable=False
    )

    assigned_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    expired_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    verification_data: Mapped[Optional[dict]] = mapped_column(JSON)
