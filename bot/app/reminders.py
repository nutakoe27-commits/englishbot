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
from datetime import datetime, time, timedelta, timezone
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
from sqlalchemy import BigInteger, Boolean, DateTime, String, Time

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
    reminder_time: Mapped[time] = mapped_column(Time, nullable=False)
    reminder_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False)
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
