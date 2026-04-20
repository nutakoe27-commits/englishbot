"""Бизнес-репозиторий: всё, что нужно из БД, выражено как методы Repo.

Дизайн: Repo — тонкая обёртка вокруг AsyncSession. Создаётся внутри
db_session() и не переживает её. Никакой кешированной валидации.
"""

from __future__ import annotations

from datetime import datetime, date, time, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DailyUsage, Payment, SettingKV, Session as SessionRow, User


# Europe/Moscow без зависимости от системного tz — фикс UTC+3.
# (МСК круглый год +3, без перехода на летнее время с 2014.)
MSK = timezone(timedelta(hours=3))


def msk_today() -> date:
    return datetime.now(MSK).date()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Repo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    # ─── users ──────────────────────────────────────────────────────────
    async def upsert_user(
        self,
        *,
        tg_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        language_code: Optional[str] = None,
    ) -> User:
        """Создать юзера, если его нет; иначе обновить профиль."""
        now = utcnow()
        stmt = mysql_insert(User).values(
            tg_id=tg_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            language_code=language_code,
            reminder_time=time(19, 0),
            reminder_enabled=True,
            is_blocked=False,
            created_at=now,
            updated_at=now,
        )
        stmt = stmt.on_duplicate_key_update(
            username=stmt.inserted.username,
            first_name=stmt.inserted.first_name,
            last_name=stmt.inserted.last_name,
            language_code=stmt.inserted.language_code,
            updated_at=now,
        )
        await self.s.execute(stmt)
        return await self.get_user_by_tg_id(tg_id)

    async def get_user_by_tg_id(self, tg_id: int) -> Optional[User]:
        res = await self.s.execute(select(User).where(User.tg_id == tg_id))
        return res.scalar_one_or_none()

    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        res = await self.s.execute(select(User).where(User.id == user_id))
        return res.scalar_one_or_none()

    async def has_active_subscription(self, user: User) -> bool:
        if user.subscription_until is None:
            return False
        return user.subscription_until > utcnow()

    async def add_subscription_days(
        self,
        *,
        user: User,
        days: int,
        plan: str = "admin_grant",
        granted_by_tg_id: Optional[int] = None,
        amount_rub: float = 0.0,
        notes: Optional[str] = None,
    ) -> None:
        """Продлить подписку на N дней. Если уже активна — прибавить к концу."""
        now = utcnow()
        base = (
            user.subscription_until
            if user.subscription_until and user.subscription_until > now
            else now
        )
        new_until = base + timedelta(days=days)
        await self.s.execute(
            update(User).where(User.id == user.id).values(subscription_until=new_until)
        )
        # Запись в payments для аудита
        self.s.add(
            Payment(
                user_id=user.id,
                amount_rub=amount_rub,
                plan=plan,
                status="succeeded",
                days_granted=days,
                granted_by_tg_id=granted_by_tg_id,
                notes=notes,
                created_at=now,
                updated_at=now,
            )
        )

    # ─── usage / лимиты ────────────────────────────────────────────────
    async def get_used_seconds_today(self, user_id: int) -> int:
        res = await self.s.execute(
            select(DailyUsage.used_seconds).where(
                DailyUsage.user_id == user_id,
                DailyUsage.usage_date == msk_today(),
            )
        )
        return int(res.scalar_one_or_none() or 0)

    async def add_used_seconds(self, *, user_id: int, seconds: int) -> int:
        """Прибавить N секунд к дневному счётчику. Возвращает итоговое значение."""
        if seconds <= 0:
            return await self.get_used_seconds_today(user_id)
        today = msk_today()
        now = utcnow()
        stmt = mysql_insert(DailyUsage).values(
            user_id=user_id,
            usage_date=today,
            used_seconds=seconds,
            updated_at=now,
        )
        stmt = stmt.on_duplicate_key_update(
            used_seconds=DailyUsage.used_seconds + seconds,
            updated_at=now,
        )
        await self.s.execute(stmt)
        return await self.get_used_seconds_today(user_id)

    # ─── sessions ───────────────────────────────────────────────────────
    async def open_session(
        self,
        *,
        user_id: int,
        mode: str,
        level: Optional[str],
        role: Optional[str],
    ) -> SessionRow:
        row = SessionRow(
            user_id=user_id,
            started_at=utcnow(),
            mode=mode,
            level=level,
            role=role,
        )
        self.s.add(row)
        await self.s.flush()  # чтобы row.id появился
        return row

    async def close_session(self, *, session_id: int, used_seconds: int) -> None:
        await self.s.execute(
            update(SessionRow)
            .where(SessionRow.id == session_id)
            .values(ended_at=utcnow(), used_seconds=used_seconds)
        )

    # ─── settings_kv ────────────────────────────────────────────────────
    async def get_kv(self, key: str, default: Optional[str] = None) -> Optional[str]:
        res = await self.s.execute(select(SettingKV.value).where(SettingKV.key == key))
        val = res.scalar_one_or_none()
        return val if val is not None else default

    async def set_kv(self, key: str, value: str) -> None:
        now = utcnow()
        stmt = mysql_insert(SettingKV).values(key=key, value=value, updated_at=now)
        stmt = stmt.on_duplicate_key_update(value=value, updated_at=now)
        await self.s.execute(stmt)

    async def get_kv_int(self, key: str, default: int) -> int:
        v = await self.get_kv(key)
        try:
            return int(v) if v is not None else default
        except (ValueError, TypeError):
            return default

    async def get_kv_bool(self, key: str, default: bool) -> bool:
        v = await self.get_kv(key)
        if v is None:
            return default
        return v.strip().lower() in ("1", "true", "yes", "on")
