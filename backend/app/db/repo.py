"""Бизнес-репозиторий: всё, что нужно из БД, выражено как методы Repo.

Дизайн: Repo — тонкая обёртка вокруг AsyncSession. Создаётся внутри
db_session() и не переживает её. Никакой кешированной валидации.
"""

from __future__ import annotations

from datetime import datetime, date, time, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import func, or_, select, update
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

    async def get_bonus_seconds_today(self, user_id: int) -> int:
        """Бонус за выполненный Daily Quest (сбрасывается в 00:00 МСК)."""
        res = await self.s.execute(
            select(DailyUsage.bonus_seconds).where(
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

    # ─── Админские запросы ───────────────────────────────────────────────────
    async def count_users(self) -> int:
        res = await self.s.execute(select(func.count(User.id)))
        return int(res.scalar() or 0)

    async def count_active_subscriptions(self) -> int:
        """Активные сейчас подписки (subscription_until > now)."""
        now = utcnow()
        res = await self.s.execute(
            select(func.count(User.id)).where(User.subscription_until > now)
        )
        return int(res.scalar() or 0)

    async def count_blocked_users(self) -> int:
        res = await self.s.execute(
            select(func.count(User.id)).where(User.is_blocked.is_(True))
        )
        return int(res.scalar() or 0)

    async def count_active_users_since(self, since_date: date) -> int:
        """Сколько уникальных юзеров было активно начиная с since_date включительно."""
        res = await self.s.execute(
            select(func.count(func.distinct(DailyUsage.user_id))).where(
                DailyUsage.usage_date >= since_date
            )
        )
        return int(res.scalar() or 0)

    async def total_used_seconds_today(self) -> int:
        res = await self.s.execute(
            select(func.coalesce(func.sum(DailyUsage.used_seconds), 0)).where(
                DailyUsage.usage_date == msk_today()
            )
        )
        return int(res.scalar() or 0)

    async def search_users(self, query: str, limit: int = 50) -> Sequence[User]:
        """Поиск юзеров по tg_id или username/имени. Пустой query = последние созданные."""
        q = (query or "").strip()
        stmt = select(User).order_by(User.created_at.desc()).limit(limit)
        if q:
            like = f"%{q}%"
            conds = [
                User.username.like(like),
                User.first_name.like(like),
                User.last_name.like(like),
            ]
            if q.lstrip("-").isdigit():
                conds.append(User.tg_id == int(q))
            stmt = (
                select(User)
                .where(or_(*conds))
                .order_by(User.created_at.desc())
                .limit(limit)
            )
        res = await self.s.execute(stmt)
        return list(res.scalars().all())

    async def set_blocked(self, user: User, blocked: bool) -> None:
        await self.s.execute(
            update(User).where(User.id == user.id).values(is_blocked=blocked)
        )

    async def set_reminder(
        self,
        user: User,
        *,
        enabled: Optional[bool] = None,
        reminder_hour: Optional[int] = None,
    ) -> None:
        """Обновить настройки напоминания. Час в МСК (0–23)."""
        values: dict = {}
        if enabled is not None:
            values["reminder_enabled"] = enabled
        if reminder_hour is not None:
            h = max(0, min(23, int(reminder_hour)))
            values["reminder_time"] = time(h, 0)
        if values:
            await self.s.execute(
                update(User).where(User.id == user.id).values(**values)
            )

    async def get_users_for_reminder_hour(
        self, hour_msk: int
    ) -> Sequence[User]:
        """Все юзеры, которым надо послать напоминание в этот час МСК."""
        res = await self.s.execute(
            select(User).where(
                User.reminder_enabled.is_(True),
                User.is_blocked.is_(False),
                func.hour(User.reminder_time) == hour_msk,
            )
        )
        return list(res.scalars().all())

    async def total_revenue_rub(self) -> float:
        res = await self.s.execute(
            select(func.coalesce(func.sum(Payment.amount_rub), 0)).where(
                Payment.status == "succeeded"
            )
        )
        return float(res.scalar() or 0)

    async def recent_payments(self, limit: int = 20) -> Sequence[Payment]:
        res = await self.s.execute(
            select(Payment).order_by(Payment.created_at.desc()).limit(limit)
        )
        return list(res.scalars().all())

    # ─── Массовые операции ──────────────────────────────────────────────
    async def get_active_subscribers(self) -> Sequence[User]:
        """Юзеры с активной подпиской сейчас (subscription_until > now)."""
        now = utcnow()
        res = await self.s.execute(
            select(User).where(User.subscription_until > now)
        )
        return list(res.scalars().all())

    async def get_broadcast_recipients(self) -> Sequence[User]:
        """Получатели рассылки: все незаблокированные юзеры с tg_id."""
        res = await self.s.execute(
            select(User).where(
                User.is_blocked.is_(False),
                User.tg_id.is_not(None),
            )
        )
        return list(res.scalars().all())

    async def bulk_extend_active_subscriptions(
        self,
        *,
        days: int,
        plan: str = "admin_grant",
        granted_by_tg_id: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> int:
        """Продлить подписку всем активным подписчикам на N дней.

        Возвращает количество затронутых юзеров. Создаёт Payment-запись
        для каждого для аудита.
        """
        now = utcnow()
        active = await self.get_active_subscribers()
        count = 0
        for u in active:
            base = (
                u.subscription_until
                if u.subscription_until and u.subscription_until > now
                else now
            )
            new_until = base + timedelta(days=days)
            await self.s.execute(
                update(User)
                .where(User.id == u.id)
                .values(subscription_until=new_until)
            )
            self.s.add(
                Payment(
                    user_id=u.id,
                    amount_rub=0.0,
                    plan=plan,
                    status="succeeded",
                    days_granted=days,
                    granted_by_tg_id=granted_by_tg_id,
                    notes=notes,
                    created_at=now,
                    updated_at=now,
                )
            )
            count += 1
        return count
