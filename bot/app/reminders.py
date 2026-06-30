"""Ежедневные напоминания юзерам.

Логика:
    1. Раз в час asyncio-таск спит до начала следующего часа МСК.
    2. На границе часа смотрит, у кого reminder_time.hour == текущий_час и
       reminder_enabled = TRUE и is_blocked = FALSE.
    3. Шлёт всем сообщение с кнопкой «🎤 Начать разговор».

База — та же MySQL, что использует backend (DATABASE_URL из .env).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, Numeric, String, Text, Time, func

logger = logging.getLogger(__name__)

# МСК = UTC+3 фиксированно.
MSK = timezone(timedelta(hours=3))


# ─── Минимальная ORM-модель User ──────────────────────────────────────────────
# Не импортируем из backend, чтобы не тащить весь его код в bot-контейнер.
# Зеркало схемы из backend/app/db/models.py — строго те же имена.

class _Base(DeclarativeBase):
    pass


class _User(_Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    first_name: Mapped[Optional[str]] = mapped_column(String(128))
    subscription_until: Mapped[Optional[datetime]] = mapped_column(DateTime)
    reminder_time: Mapped[time] = mapped_column(Time, nullable=False)
    reminder_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Миграция 0004 — стрик и онбординг-цель.
    streak_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    best_streak_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_practice_date: Mapped[Optional[date]] = mapped_column(Date)
    learning_goal: Mapped[Optional[str]] = mapped_column(String(32))
    # Миграция 0005 — роль из последней сессии для умной выдачи role-quest.
    last_session_role: Mapped[Optional[str]] = mapped_column(String(64))
    # Миграция 0007 — анти-спам для win-back-рассылки (не чаще 1/7 дней).
    last_winback_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # Миграция 0009 — первый апдейт от юзера в Telegram-боте (NULL = только Mini App).
    bot_activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class _DailyUsage(_Base):
    __tablename__ = "daily_usage"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    used_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bonus_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Миграция 0016: время только говорения (voice/chat) — для лимита разговора.
    speaking_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class _SettingKV(_Base):
    __tablename__ = "settings_kv"

    key: Mapped[str] = mapped_column("key", String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class _Payment(_Base):
    """Зеркало backend/app/db/models.py::Payment (те же колонки и типы).
    Бот пишет сюда при successful_payment от ЮКассы.
    """

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_rub: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    # ENUM payment_plan: monthly | yearly | gift | admin_grant
    plan: Mapped[str] = mapped_column(String(32), nullable=False)
    # ENUM payment_status: pending | succeeded | canceled | refunded
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    days_granted: Mapped[int] = mapped_column(Integer, nullable=False)
    granted_by_tg_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    notes: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# ─── Engine / session ─────────────────────────────────────────────────────────

_engine = None
_SessionMaker: Optional[async_sessionmaker[AsyncSession]] = None


def _init_engine() -> bool:
    """Создаёт engine один раз. Возвращает True если БД настроена."""
    global _engine, _SessionMaker
    if _engine is not None:
        return True
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.warning("DATABASE_URL не задан — напоминания и /reminder отключены")
        return False
    # Бесшовный переход с aiomysql на asyncmy — см. backend/app/db/engine.py.
    if db_url.startswith("mysql+aiomysql://"):
        db_url = "mysql+asyncmy://" + db_url[len("mysql+aiomysql://"):]
        logger.warning("[reminders] URL normalized: aiomysql → asyncmy")
    try:
        _engine = create_async_engine(db_url, pool_pre_ping=True, pool_size=5)
        _SessionMaker = async_sessionmaker(_engine, expire_on_commit=False)
        logger.info("[reminders] engine готов")
        return True
    except Exception as exc:
        logger.error("[reminders] не удалось создать engine: %s", exc, exc_info=True)
        return False


def is_db_ready() -> bool:
    return _init_engine() and _SessionMaker is not None


# ─── Публичные функции для main.py ────────────────────────────────────────────

REMINDER_TEXT = (
    "👋 Привет! Время для практики английского.\n\n"
    "Даже 10 минут в день — и через месяц ты уже почувствуешь разницу. "
    "Жми кнопку ниже и начнём."
)

REMINDER_TEXT_FREE_PERIOD = (
    "👋 Привет! Время для практики английского.\n\n"
    "Сейчас бот открыт <b>полностью бесплатно и без лимитов</b> — "
    "заходи хоть на 10 минут, хоть на час. Регулярность важнее длительности. "
    "Жми кнопку ниже и начнём."
)


def _render_reminder_text(user: Optional["_User"] = None) -> str:
    """Текст напоминания. Если передан user — пытаемся подобрать
    персонализированный вариант по streak/last_practice_date.

    Без user или для streak=0 + новичков — старый generic-текст
    (FREE_PERIOD имеет приоритет, чтобы сохранить акцию).
    """
    if os.getenv("FREE_PERIOD", "0") == "1":
        return REMINDER_TEXT_FREE_PERIOD
    if user is None:
        return REMINDER_TEXT

    streak = int(user.streak_days or 0)
    if streak >= 30:
        return (
            f"🔥 <b>Стрик {streak} дней</b> — это уже привычка. "
            "Не дай ему сгореть: 2 минуты сегодня — и плюс к рекорду."
        )
    if streak >= 7:
        return (
            f"🔥 <b>Стрик {streak} дней!</b> Не теряй темп — 2 минуты, "
            "и поедем дальше."
        )
    if streak >= 3:
        return (
            f"У тебя стрик <b>{streak}</b>. Добьём до 7? "
            "Сегодня — твоя минута."
        )
    if user.last_practice_date is None:
        return (
            "👋 Открой mini-app и поговори со мной по-английски — "
            "2 минуты и ты в потоке."
        )
    return REMINDER_TEXT


def _reminder_keyboard(miniapp_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎤 Начать разговор",
                    web_app=WebAppInfo(url=miniapp_url),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏰ Изменить время напоминания",
                    callback_data="reminder:settings",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔕 Отключить напоминания",
                    callback_data="reminder:off",
                )
            ],
        ]
    )


# ─── Maintenance mode (читается из settings_kv) ────────────────────────────

_MAINT_CACHE: dict = {"ts": 0.0, "enabled": False, "message": ""}
_MAINT_TTL_SEC = 5.0  # короткий кеш, чтобы не бить в БД на каждый апдейт

_DEFAULT_MAINT_MESSAGE = (
    "🔧 Бот временно недоступен — ведутся технические работы. "
    "Возвращайся через 10–15 минут."
)


async def get_maintenance_status() -> tuple[bool, str]:
    """Читает maintenance_mode и maintenance_message из settings_kv.

    Кешируется на 5 секунд — чтобы высоконагруженный бот не слал DoS в MySQL.
    При проблемах с БД отдаёт (False, "") — fail-open (бот продолжает
    работать), чтобы сбой БД не ломал весь бот.
    """
    import time as _time

    now = _time.time()
    if now - _MAINT_CACHE["ts"] < _MAINT_TTL_SEC:
        return bool(_MAINT_CACHE["enabled"]), str(_MAINT_CACHE["message"])

    if not is_db_ready():
        return False, ""

    assert _SessionMaker is not None
    try:
        async with _SessionMaker() as s:
            res = await s.execute(
                select(_SettingKV).where(
                    _SettingKV.key.in_(["maintenance_mode", "maintenance_message"])
                )
            )
            rows = {r.key: r.value for r in res.scalars().all()}
        raw = rows.get("maintenance_mode", "0") or "0"
        enabled = raw.strip().lower() in ("1", "true", "yes", "on")
        message = rows.get("maintenance_message") or _DEFAULT_MAINT_MESSAGE
        _MAINT_CACHE.update({"ts": now, "enabled": enabled, "message": message})
        return enabled, message
    except Exception as exc:
        logger.warning("[maintenance] не удалось читать флаг: %s", exc)
        return False, ""


async def mark_bot_activated(
    tg_id: int,
    *,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    language_code: Optional[str] = None,
) -> None:
    """Зафиксировать, что юзер написал боту в Telegram (миграция 0009).

    Вызывается из outer-middleware на ЛЮБОМ апдейте от юзера.

    Поведение:
      - Если юзера нет в `users` — INSERT с bot_activated_at=now (дефолтные
        reminder_time=19:00, reminder_enabled=TRUE, is_blocked=FALSE).
        Так юзеры, которые написали /start, но никогда не открыли Mini App,
        теперь тоже попадают в БД.
      - Если юзер уже есть и bot_activated_at IS NULL — проставляем (первая
        активация после миграции). При желании заодно обновляем профиль.
      - Если bot_activated_at уже не NULL — ничего не делаем (single-write).

    Best-effort: исключение проглатывается и логируется. БД-сбой не должен
    рушить ответ бота.
    """
    if not is_db_ready():
        return
    assert _SessionMaker is not None
    try:
        from sqlalchemy import text as _sql
        async with _SessionMaker() as s:
            await s.execute(
                _sql(
                    """
                    INSERT INTO users (
                        tg_id, username, first_name, last_name, language_code,
                        reminder_time, reminder_enabled, is_blocked,
                        created_at, updated_at, bot_activated_at
                    )
                    VALUES (
                        :tg_id, :username, :first_name, :last_name, :language_code,
                        '19:00:00', TRUE, FALSE,
                        UTC_TIMESTAMP(), UTC_TIMESTAMP(), UTC_TIMESTAMP()
                    )
                    ON DUPLICATE KEY UPDATE
                        username = COALESCE(VALUES(username), users.username),
                        first_name = COALESCE(VALUES(first_name), users.first_name),
                        last_name = COALESCE(VALUES(last_name), users.last_name),
                        language_code = COALESCE(VALUES(language_code), users.language_code),
                        updated_at = UTC_TIMESTAMP(),
                        bot_activated_at = COALESCE(users.bot_activated_at, UTC_TIMESTAMP())
                    """
                ),
                {
                    "tg_id": tg_id,
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name,
                    "language_code": language_code,
                },
            )
            await s.commit()
    except Exception as exc:
        logger.warning("[bot-activation] mark failed для tg_id=%s: %s", tg_id, exc)


async def get_user_reminder(tg_id: int) -> Optional[tuple[bool, int]]:
    """Возвращает (enabled, hour_msk) или None если юзера нет/БД нет."""
    if not is_db_ready():
        return None
    assert _SessionMaker is not None
    async with _SessionMaker() as s:
        res = await s.execute(select(_User).where(_User.tg_id == tg_id))
        u = res.scalar_one_or_none()
        if u is None:
            return None
        h = u.reminder_time.hour if u.reminder_time else 19
        return bool(u.reminder_enabled), int(h)


async def set_user_reminder(
    tg_id: int,
    *,
    enabled: Optional[bool] = None,
    hour_msk: Optional[int] = None,
) -> bool:
    """Возвращает True если что-то обновлено."""
    if not is_db_ready():
        return False
    assert _SessionMaker is not None
    values: dict = {"updated_at": datetime.utcnow()}
    if enabled is not None:
        values["reminder_enabled"] = enabled
    if hour_msk is not None:
        h = max(0, min(23, int(hour_msk)))
        values["reminder_time"] = time(h, 0)
    if len(values) == 1:  # только updated_at — нечего менять
        return False
    async with _SessionMaker() as s:
        await s.execute(update(_User).where(_User.tg_id == tg_id).values(**values))
        await s.commit()
    return True


# ─── Профиль пользователя ────────────────────────────────────────────────────

def _msk_today() -> date:
    """Сегодняшняя дата в МСК (для ключа daily_usage)."""
    return datetime.now(MSK).date()


async def get_user_profile(tg_id: int) -> Optional[dict]:
    """Возвращает словарь с данными профиля или None, если юзер не найден / БД нет.

    Поля (новая модель — без battle/quest):
        user_db_id, username, first_name
        subscription_until (UTC), has_subscription, days_left
        reminder_enabled, reminder_hour
        used_seconds_today, used_seconds_total, bonus_seconds_today
        streak_days, best_streak_days, last_practice_date
        speaking_minutes, listening_minutes, grammar_minutes, srs_minutes
        grammar_topics_done, grammar_topics_total
        total_words
        achievements_earned, achievements_total
    """
    if not is_db_ready():
        return None
    assert _SessionMaker is not None
    try:
        async with _SessionMaker() as s:
            res = await s.execute(select(_User).where(_User.tg_id == tg_id))
            u = res.scalar_one_or_none()
            if u is None:
                return None

            # Подписка
            now_utc = datetime.utcnow()
            has_sub = bool(u.subscription_until and u.subscription_until > now_utc)
            days_left = 0
            if has_sub and u.subscription_until:
                delta = u.subscription_until - now_utc
                days_left = max(0, delta.days)

            # Использование сегодня
            today = _msk_today()
            r_today = await s.execute(
                select(
                    _DailyUsage.used_seconds,
                    _DailyUsage.bonus_seconds,
                    _DailyUsage.speaking_seconds,
                ).where(
                    _DailyUsage.user_id == u.id,
                    _DailyUsage.usage_date == today,
                )
            )
            row_today = r_today.first()
            if row_today is not None:
                used_today = int(row_today[0] or 0)
                bonus_today = int(row_today[1] or 0)
                speaking_today = int(row_today[2] or 0)
            else:
                used_today = 0
                bonus_today = 0
                speaking_today = 0

            # Всего за всё время
            r_total = await s.execute(
                select(func.coalesce(func.sum(_DailyUsage.used_seconds), 0)).where(
                    _DailyUsage.user_id == u.id,
                )
            )
            used_total = int(r_total.scalar() or 0)

            # Минуты по режимам — из sessions.
            r_modes = await s.execute(
                text(
                    "SELECT mode, COALESCE(SUM(used_seconds), 0) "
                    "FROM sessions WHERE user_id = :uid GROUP BY mode"
                ),
                {"uid": int(u.id)},
            )
            by_mode = {row[0]: int(row[1] or 0) for row in r_modes.all()}
            speaking_seconds = by_mode.get("voice", 0) + by_mode.get("chat", 0)
            listening_seconds = by_mode.get("listening", 0)
            grammar_seconds = by_mode.get("grammar", 0)
            srs_seconds = by_mode.get("srs", 0)

            # Grammar Learn — пройдено тем / всего активных.
            try:
                r_topics_done = await s.execute(
                    text(
                        "SELECT COUNT(*) FROM user_grammar_progress "
                        "WHERE user_id = :uid AND completed_at IS NOT NULL"
                    ),
                    {"uid": int(u.id)},
                )
                grammar_done = int(r_topics_done.scalar() or 0)
                r_topics_total = await s.execute(
                    text("SELECT COUNT(*) FROM grammar_topics WHERE is_active = TRUE")
                )
                grammar_total = int(r_topics_total.scalar() or 0)
            except Exception:
                grammar_done, grammar_total = 0, 0

            # Словарь — сколько user-слов.
            try:
                r_words = await s.execute(
                    text(
                        "SELECT COUNT(*) FROM user_vocabulary "
                        "WHERE user_id = :uid AND source = 'user'"
                    ),
                    {"uid": int(u.id)},
                )
                words_count = int(r_words.scalar() or 0)
            except Exception:
                words_count = 0

            # Медали.
            try:
                r_ach = await s.execute(
                    text(
                        "SELECT COUNT(*) FROM user_achievements WHERE user_id = :uid"
                    ),
                    {"uid": int(u.id)},
                )
                ach_earned = int(r_ach.scalar() or 0)
            except Exception:
                ach_earned = 0
            # Total медалей знает только backend (achievements.ACHIEVEMENTS) —
            # бот эту константу не тянет; поставим разумное число, а если
            # earned > total — фронт-логика спрячет блок.
            ach_total = max(ach_earned, 12)

            # Бесплатные лимиты — единый источник истины settings_kv (их же
            # читает backend). Тянем, чтобы профиль показывал актуальные числа.
            free_seconds_per_day = 300
            free_listening_per_day = 1
            free_grammar_per_day = 1
            try:
                r_kv = await s.execute(
                    select(_SettingKV).where(
                        _SettingKV.key.in_([
                            "free_seconds_per_day",
                            "free_listening_per_day",
                            "free_grammar_per_day",
                        ])
                    )
                )
                kv = {row.key: row.value for row in r_kv.scalars().all()}
                free_seconds_per_day = int(kv.get("free_seconds_per_day") or free_seconds_per_day)
                free_listening_per_day = int(kv.get("free_listening_per_day") or free_listening_per_day)
                free_grammar_per_day = int(kv.get("free_grammar_per_day") or free_grammar_per_day)
            except Exception:
                pass

            return {
                "user_db_id": int(u.id),
                "username": u.username,
                "first_name": u.first_name,
                "subscription_until": u.subscription_until,
                "has_subscription": has_sub,
                "days_left": days_left,
                "reminder_enabled": bool(u.reminder_enabled),
                "reminder_hour": int(u.reminder_time.hour if u.reminder_time else 19),
                "used_seconds_today": used_today,
                "used_seconds_total": used_total,
                "bonus_seconds_today": bonus_today,
                "speaking_seconds_today": speaking_today,
                "free_seconds_per_day": free_seconds_per_day,
                "free_listening_per_day": free_listening_per_day,
                "free_grammar_per_day": free_grammar_per_day,
                "streak_days": int(u.streak_days or 0),
                "best_streak_days": int(u.best_streak_days or 0),
                "last_practice_date": u.last_practice_date,
                "speaking_minutes": speaking_seconds // 60,
                "listening_minutes": listening_seconds // 60,
                "grammar_minutes": grammar_seconds // 60,
                "srs_minutes": srs_seconds // 60,
                "grammar_topics_done": grammar_done,
                "grammar_topics_total": grammar_total,
                "total_words": words_count,
                "achievements_earned": ach_earned,
                "achievements_total": ach_total,
            }
    except Exception as exc:
        logger.warning("[profile] get_user_profile упал: %s", exc)
        return None


# ─── Платежи (ЮКасса / successful_payment) ────────────────────────────────────

async def credit_subscription_payment(
    *,
    tg_id: int,
    plan: str,
    days: int,
    amount_rub: float,
    provider_payment_id: Optional[str],
    notes: Optional[str] = None,
) -> Optional[datetime]:
    """Продлить подписку после успешной оплаты через Telegram Payments / ЮКассу.

    Идемпотентность: если запись с таким provider_payment_id уже есть в payments,
    повторно дни не начисляем (возвращаем текущий subscription_until юзера).

    Логика продления зеркалит backend/app/db/repo.py::add_subscription_days:
    если subscription_until > now — база = он (добавляем к концу), иначе от now.
    Возвращает новое subscription_until (UTC naive, как во всей схеме).
    """
    if not is_db_ready():
        logger.error("[payments] DB не готова — не могу зачислить платёж tg_id=%s", tg_id)
        raise RuntimeError("DB not ready")
    assert _SessionMaker is not None

    now = datetime.utcnow()
    async with _SessionMaker() as s:
        # 1) Идемпотентность по provider_payment_id
        if provider_payment_id:
            existing = await s.execute(
                select(_Payment).where(_Payment.provider_payment_id == provider_payment_id)
            )
            if existing.scalar_one_or_none() is not None:
                logger.info(
                    "[payments] повторный successful_payment tg_id=%s provider_id=%s — пропускаю",
                    tg_id, provider_payment_id,
                )
                # Вернём текущий until юзера — он уже был продлён в первый раз.
                user_res = await s.execute(select(_User).where(_User.tg_id == tg_id))
                u = user_res.scalar_one_or_none()
                return u.subscription_until if u else None

        # 2) Найти юзера
        user_res = await s.execute(select(_User).where(_User.tg_id == tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            logger.error("[payments] tg_id=%s не найден в users — платёж получен, но зачислить некому", tg_id)
            raise RuntimeError(f"user tg_id={tg_id} not found")

        # 3) Новый subscription_until
        current_until = user.subscription_until
        base = current_until if (current_until and current_until > now) else now
        new_until = base + timedelta(days=days)

        # 4) Обновляем users.subscription_until
        await s.execute(
            update(_User).where(_User.id == user.id).values(
                subscription_until=new_until, updated_at=now,
            )
        )

        # 5) Записываем в payments
        s.add(
            _Payment(
                user_id=user.id,
                amount_rub=amount_rub,
                plan=plan,
                status="succeeded",
                provider_payment_id=provider_payment_id,
                days_granted=days,
                granted_by_tg_id=None,  # самооплата — не админ
                notes=notes,
                created_at=now,
                updated_at=now,
            )
        )

        await s.commit()

    logger.info(
        "[payments] зачислено: tg_id=%s plan=%s days=%d amount=%.2f until=%s",
        tg_id, plan, days, amount_rub, new_until.isoformat() if new_until else "?",
    )
    return new_until


# ─── Фоновая корутина рассылки ────────────────────────────────────────────────

# ─── Ежедневное напоминание ──────────────────────────────────────────────
# Шлём в час, выбранный юзером (reminder_time). Текст подбирается под
# streak/активность в _render_reminder_text. (Квесты убраны — здесь только
# напоминание о практике.)


async def _send_reminders_for_hour(bot: Bot, hour_msk: int, miniapp_url: str) -> None:
    """Один проход: найти всех юзеров для этого часа и разослать."""
    if not is_db_ready():
        return
    assert _SessionMaker is not None
    async with _SessionMaker() as s:
        # MySQL: HOUR(reminder_time) = hour_msk
        from sqlalchemy import func
        res = await s.execute(
            select(_User).where(
                _User.reminder_enabled.is_(True),
                _User.is_blocked.is_(False),
                func.hour(_User.reminder_time) == hour_msk,
            )
        )
        users = list(res.scalars().all())
    if not users:
        logger.info("[reminders] hour=%d: получателей нет", hour_msk)
        return
    logger.info("[reminders] hour=%d: рассылка %d получателям", hour_msk, len(users))
    kb = _reminder_keyboard(miniapp_url)
    sent, failed, blocked = 0, 0, 0
    for u in users:
        try:
            await bot.send_message(
                chat_id=u.tg_id,
                text=_render_reminder_text(u),
                parse_mode="HTML",
                reply_markup=kb,
            )
            sent += 1
        except TelegramForbiddenError:
            # Юзер заблокировал бота — отключаем напоминания
            blocked += 1
            try:
                await set_user_reminder(u.tg_id, enabled=False)
            except Exception:
                pass
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
        except Exception as exc:
            failed += 1
            logger.warning("[reminders] не отправлено tg_id=%s: %s", u.tg_id, exc)
        # Соблюдаем лимит Telegram ~30 msg/sec
        await asyncio.sleep(0.05)
    logger.info(
        "[reminders] hour=%d итог: sent=%d failed=%d blocked=%d",
        hour_msk, sent, failed, blocked,
    )


def _seconds_until_next_msk_hour() -> float:
    """Сколько секунд спать до начала следующего часа МСК."""
    now = datetime.now(MSK)
    next_hour = (now + timedelta(hours=1)).replace(
        minute=0, second=5, microsecond=0
    )
    delta = (next_hour - now).total_seconds()
    return max(5.0, delta)


async def reminders_loop(bot: Bot, miniapp_url: str) -> None:
    """Бесконечный цикл: на каждой границе часа МСК шлёт напоминания.

    Запусти как `asyncio.create_task(reminders_loop(bot, MINIAPP_URL))` в main().
    """
    logger.info("[reminders] цикл напоминаний стартовал")
    # Дадим backend-у инициализировать БД
    await asyncio.sleep(10)
    while True:
        try:
            sleep_s = _seconds_until_next_msk_hour()
            logger.info("[reminders] следующая отправка через %.0f сек", sleep_s)
            await asyncio.sleep(sleep_s)
            now_msk = datetime.now(MSK)
            # Обычные reminders (кто включил напоминания на этот час).
            await _send_reminders_for_hour(bot, now_msk.hour, miniapp_url)
        except asyncio.CancelledError:
            logger.info("[reminders] цикл остановлен")
            raise
        except Exception as exc:
            logger.error("[reminders] ошибка в цикле: %s", exc, exc_info=True)
            # Не спамим — ждём минуту перед повтором
            await asyncio.sleep(60)


# ─── Win-back для неактивных юзеров (retention v1) ────────────────────────────

WINBACK_INACTIVE_DAYS = 3   # сколько дней без практики, прежде чем дергать
WINBACK_COOLDOWN_DAYS = 7   # минимум дней между двумя win-back-сообщениями
WINBACK_PUSH_HOURS_MSK = {10, 18}  # часы (МСК), когда шлём — не ночью

_WINBACK_KB_CACHE: dict[str, InlineKeyboardMarkup] = {}


def _winback_keyboard(miniapp_url: str) -> InlineKeyboardMarkup:
    if miniapp_url not in _WINBACK_KB_CACHE:
        _WINBACK_KB_CACHE[miniapp_url] = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎤 Вернуться к практике",
                        web_app=WebAppInfo(url=miniapp_url),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔕 Отключить напоминания",
                        callback_data="reminder:off",
                    )
                ],
            ]
        )
    return _WINBACK_KB_CACHE[miniapp_url]


def _winback_text(user: "_User") -> str:
    """Подбираем тон по best_streak — у юзера с длинным streak больше потерь."""
    best = int(user.best_streak_days or 0)
    if best >= 7:
        return (
            f"У тебя был стрик <b>{best} дней</b> 🔥 Один разговор — и ты "
            "вернёшься в форму. Сегодня хватит и одной минуты."
        )
    if user.last_practice_date is None:
        return (
            "👋 Так и не успел попробовать? Открой mini-app и поговори "
            "со мной 1 минуту — почувствуешь, как это."
        )
    return (
        "👋 Давно не виделись. 1 минута в mini-app — и ты снова в потоке. "
        "Я подстроюсь под уровень."
    )


async def _select_winback_users() -> list["_User"]:
    """SQL: кого пора дернуть. Реализация зеркалит repo.users_for_winback."""
    if not is_db_ready():
        return []
    assert _SessionMaker is not None
    today = _msk_today()
    inactive_cutoff = today - timedelta(days=WINBACK_INACTIVE_DAYS)
    cooldown_cutoff = datetime.utcnow() - timedelta(days=WINBACK_COOLDOWN_DAYS)
    from sqlalchemy import and_, or_
    async with _SessionMaker() as s:
        res = await s.execute(
            select(_User).where(
                _User.reminder_enabled.is_(True),
                _User.is_blocked.is_(False),
                or_(
                    _User.last_practice_date < inactive_cutoff,
                    and_(
                        _User.last_practice_date.is_(None),
                        _User.updated_at < datetime.combine(inactive_cutoff, time.min),
                    ),
                ),
                or_(
                    _User.last_winback_at.is_(None),
                    _User.last_winback_at < cooldown_cutoff,
                ),
            )
        )
        return list(res.scalars().all())


async def _mark_winback_sent(tg_id: int) -> None:
    if not is_db_ready():
        return
    assert _SessionMaker is not None
    async with _SessionMaker() as s:
        await s.execute(
            update(_User).where(_User.tg_id == tg_id).values(
                last_winback_at=datetime.utcnow(),
            )
        )
        await s.commit()


async def _send_winback_round(bot: Bot, miniapp_url: str) -> None:
    """Один проход рассылки. Дедупликация через last_winback_at."""
    users = await _select_winback_users()
    if not users:
        logger.info("[winback] никто не подходит")
        return
    logger.info("[winback] подходит %d юзеров", len(users))
    kb = _winback_keyboard(miniapp_url)
    sent, failed, blocked = 0, 0, 0
    for u in users:
        try:
            await bot.send_message(
                chat_id=u.tg_id,
                text=_winback_text(u),
                parse_mode="HTML",
                reply_markup=kb,
            )
            sent += 1
            await _mark_winback_sent(u.tg_id)
        except TelegramForbiddenError:
            blocked += 1
            try:
                await set_user_reminder(u.tg_id, enabled=False)
            except Exception:
                pass
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
        except Exception as exc:
            failed += 1
            logger.warning("[winback] не отправлено tg_id=%s: %s", u.tg_id, exc)
        await asyncio.sleep(0.05)
    logger.info(
        "[winback] итог: sent=%d failed=%d blocked=%d", sent, failed, blocked,
    )


async def winback_loop(bot: Bot, miniapp_url: str) -> None:
    """Бесконечный цикл: на каждой границе часа МСК — если час в
    WINBACK_PUSH_HOURS_MSK, делаем round. Это разделяет нагрузку и
    избегает ночных push'ей.
    """
    logger.info("[winback] цикл запущен (hours=%s)", sorted(WINBACK_PUSH_HOURS_MSK))
    await asyncio.sleep(15)  # позже reminders_loop'а, чтобы не толкаться
    while True:
        try:
            sleep_s = _seconds_until_next_msk_hour()
            await asyncio.sleep(sleep_s)
            now_hour = datetime.now(MSK).hour
            if now_hour in WINBACK_PUSH_HOURS_MSK:
                await _send_winback_round(bot, miniapp_url)
        except asyncio.CancelledError:
            logger.info("[winback] цикл остановлен")
            raise
        except Exception as exc:
            logger.error("[winback] ошибка в цикле: %s", exc, exc_info=True)
            await asyncio.sleep(60)
