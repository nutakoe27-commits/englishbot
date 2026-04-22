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
from sqlalchemy import select, update
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


class _DailyUsage(_Base):
    __tablename__ = "daily_usage"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    used_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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

    Поля:
        user_db_id: int
        username: Optional[str]
        first_name: Optional[str]
        subscription_until: Optional[datetime]   # UTC
        has_subscription: bool
        days_left: int                           # 0 если нет подписки
        reminder_enabled: bool
        reminder_hour: int
        used_seconds_today: int
        used_seconds_total: int
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
                select(_DailyUsage.used_seconds).where(
                    _DailyUsage.user_id == u.id,
                    _DailyUsage.usage_date == today,
                )
            )
            used_today = int(r_today.scalar() or 0)

            # Всего за всё время
            r_total = await s.execute(
                select(func.coalesce(func.sum(_DailyUsage.used_seconds), 0)).where(
                    _DailyUsage.user_id == u.id,
                )
            )
            used_total = int(r_total.scalar() or 0)

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
                text=REMINDER_TEXT,
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
            await _send_reminders_for_hour(bot, now_msk.hour, miniapp_url)
        except asyncio.CancelledError:
            logger.info("[reminders] цикл остановлен")
            raise
        except Exception as exc:
            logger.error("[reminders] ошибка в цикле: %s", exc, exc_info=True)
            # Не спамим — ждём минуту перед повтором
            await asyncio.sleep(60)
