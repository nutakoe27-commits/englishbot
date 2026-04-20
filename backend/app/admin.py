"""Admin REST API.

Все эндпоинты требуют заголовок X-Admin-Token: <ADMIN_TOKEN>.
Если ADMIN_TOKEN не задан в .env — все эндпоинты возвращают 503.

Маршруты:
    GET  /api/admin/me                 — пинг + кто я (для проверки токена)
    GET  /api/admin/metrics            — общие метрики (DAU/WAU/MAU, подписки, выручка)
    GET  /api/admin/users?q=...        — поиск юзеров (по username/имени/tg_id)
    GET  /api/admin/users/{user_id}    — полный профиль юзера
    POST /api/admin/users/{user_id}/grant-subscription
                                       — продлить подписку на N дней (и/или
                                         проставить как «подарок»)
    POST /api/admin/users/{user_id}/block
                                       — заблокировать/разблокировать юзера
    POST /api/admin/users/{user_id}/reminder
                                       — настроить напоминание
    GET  /api/admin/settings/maintenance
    POST /api/admin/settings/maintenance
                                       — режим тех.работ + сообщение
    GET  /api/admin/payments/recent    — последние записи в payments
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from .config import settings
from .db import db_session
from .db.repo import Repo, msk_today

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ─── Auth dependency ──────────────────────────────────────────────────────────

async def require_admin_token(
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
) -> None:
    if not settings.ADMIN_TOKEN:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_TOKEN не задан в .env — админка отключена",
        )
    if not x_admin_token or x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Неверный или отсутствует X-Admin-Token",
        )


# ─── Schemas ──────────────────────────────────────────────────────────────────

class MetricsResponse(BaseModel):
    total_users: int
    active_subscriptions: int
    blocked_users: int
    dau: int = Field(description="Уникальные юзеры за сегодня (МСК)")
    wau: int = Field(description="За последние 7 дней включая сегодня")
    mau: int = Field(description="За последние 30 дней")
    minutes_today: int = Field(description="Суммарное время за сегодня (минуты)")
    total_revenue_rub: float


class UserBrief(BaseModel):
    id: int
    tg_id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    is_blocked: bool
    has_subscription: bool
    subscription_until: Optional[str]
    created_at: str


class UserDetail(UserBrief):
    language_code: Optional[str]
    reminder_enabled: bool
    reminder_hour_msk: int
    used_seconds_today: int
    free_seconds_per_day: int


class GrantRequest(BaseModel):
    days: int = Field(ge=1, le=3650)
    notes: Optional[str] = None
    plan: str = Field(default="admin_grant", description="admin_grant | gift | manual_pay")
    amount_rub: float = 0.0
    granted_by_tg_id: Optional[int] = None


class BlockRequest(BaseModel):
    blocked: bool


class ReminderRequest(BaseModel):
    enabled: Optional[bool] = None
    hour_msk: Optional[int] = Field(default=None, ge=0, le=23)


class MaintenanceRequest(BaseModel):
    enabled: bool
    message: Optional[str] = None


class MaintenanceResponse(BaseModel):
    enabled: bool
    message: str


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _user_to_brief(repo: Repo, u) -> UserBrief:
    has_sub = await repo.has_active_subscription(u)
    return UserBrief(
        id=u.id,
        tg_id=u.tg_id,
        username=u.username,
        first_name=u.first_name,
        last_name=u.last_name,
        is_blocked=u.is_blocked,
        has_subscription=has_sub,
        subscription_until=(
            u.subscription_until.isoformat() if u.subscription_until else None
        ),
        created_at=u.created_at.isoformat(),
    )


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.get("/me", dependencies=[Depends(require_admin_token)])
async def me() -> dict:
    return {"ok": True, "admin_ids_count": len(settings.admin_ids_list)}


@router.get(
    "/metrics",
    response_model=MetricsResponse,
    dependencies=[Depends(require_admin_token)],
)
async def metrics() -> MetricsResponse:
    today = msk_today()
    async with db_session() as s:
        repo = Repo(s)
        total = await repo.count_users()
        subs = await repo.count_active_subscriptions()
        blocked = await repo.count_blocked_users()
        dau = await repo.count_active_users_since(today)
        wau = await repo.count_active_users_since(today - timedelta(days=6))
        mau = await repo.count_active_users_since(today - timedelta(days=29))
        seconds_today = await repo.total_used_seconds_today()
        revenue = await repo.total_revenue_rub()
        return MetricsResponse(
            total_users=total,
            active_subscriptions=subs,
            blocked_users=blocked,
            dau=dau,
            wau=wau,
            mau=mau,
            minutes_today=seconds_today // 60,
            total_revenue_rub=revenue,
        )


@router.get(
    "/users",
    response_model=list[UserBrief],
    dependencies=[Depends(require_admin_token)],
)
async def search_users(
    q: str = Query(default="", description="username, имя или tg_id"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[UserBrief]:
    async with db_session() as s:
        repo = Repo(s)
        users = await repo.search_users(q, limit=limit)
        return [await _user_to_brief(repo, u) for u in users]


@router.get(
    "/users/{user_id}",
    response_model=UserDetail,
    dependencies=[Depends(require_admin_token)],
)
async def user_detail(user_id: int) -> UserDetail:
    async with db_session() as s:
        repo = Repo(s)
        u = await repo.get_user_by_id(user_id)
        if u is None:
            raise HTTPException(404, "Юзер не найден")
        brief = await _user_to_brief(repo, u)
        used = await repo.get_used_seconds_today(u.id)
        free_seconds = await repo.get_kv_int("free_seconds_per_day", 600)
        return UserDetail(
            **brief.model_dump(),
            language_code=u.language_code,
            reminder_enabled=u.reminder_enabled,
            reminder_hour_msk=u.reminder_time.hour if u.reminder_time else 19,
            used_seconds_today=used,
            free_seconds_per_day=free_seconds,
        )


@router.post(
    "/users/{user_id}/grant-subscription",
    dependencies=[Depends(require_admin_token)],
)
async def grant_subscription(user_id: int, req: GrantRequest) -> dict:
    async with db_session() as s:
        repo = Repo(s)
        u = await repo.get_user_by_id(user_id)
        if u is None:
            raise HTTPException(404, "Юзер не найден")
        await repo.add_subscription_days(
            user=u,
            days=req.days,
            plan=req.plan,
            granted_by_tg_id=req.granted_by_tg_id,
            amount_rub=req.amount_rub,
            notes=req.notes,
        )
        # Прочитаем обновлённого юзера
        u2 = await repo.get_user_by_id(user_id)
        return {
            "ok": True,
            "subscription_until": (
                u2.subscription_until.isoformat() if u2.subscription_until else None
            ),
        }


@router.post(
    "/users/{user_id}/block", dependencies=[Depends(require_admin_token)]
)
async def block_user(user_id: int, req: BlockRequest) -> dict:
    async with db_session() as s:
        repo = Repo(s)
        u = await repo.get_user_by_id(user_id)
        if u is None:
            raise HTTPException(404, "Юзер не найден")
        await repo.set_blocked(u, req.blocked)
        return {"ok": True, "blocked": req.blocked}


@router.post(
    "/users/{user_id}/reminder", dependencies=[Depends(require_admin_token)]
)
async def update_reminder(user_id: int, req: ReminderRequest) -> dict:
    async with db_session() as s:
        repo = Repo(s)
        u = await repo.get_user_by_id(user_id)
        if u is None:
            raise HTTPException(404, "Юзер не найден")
        await repo.set_reminder(
            u, enabled=req.enabled, reminder_hour=req.hour_msk
        )
        return {"ok": True}


@router.get(
    "/settings/maintenance",
    response_model=MaintenanceResponse,
    dependencies=[Depends(require_admin_token)],
)
async def get_maintenance() -> MaintenanceResponse:
    async with db_session() as s:
        repo = Repo(s)
        enabled = await repo.get_kv_bool("maintenance_mode", False)
        msg = await repo.get_kv(
            "maintenance_message",
            "Бот временно недоступен — ведутся технические работы.",
        )
        return MaintenanceResponse(enabled=enabled, message=msg or "")


@router.post(
    "/settings/maintenance",
    response_model=MaintenanceResponse,
    dependencies=[Depends(require_admin_token)],
)
async def set_maintenance(req: MaintenanceRequest) -> MaintenanceResponse:
    async with db_session() as s:
        repo = Repo(s)
        await repo.set_kv("maintenance_mode", "1" if req.enabled else "0")
        if req.message is not None:
            await repo.set_kv("maintenance_message", req.message)
        msg = await repo.get_kv(
            "maintenance_message",
            "Бот временно недоступен — ведутся технические работы.",
        )
        return MaintenanceResponse(enabled=req.enabled, message=msg or "")


@router.get(
    "/payments/recent", dependencies=[Depends(require_admin_token)]
)
async def recent_payments(limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    async with db_session() as s:
        repo = Repo(s)
        rows = await repo.recent_payments(limit=limit)
        # Для фронта удобнее сразу отдавать tg_id юзера.
        out: list[dict] = []
        for p in rows:
            tg_id: Optional[int] = None
            try:
                u = await repo.get_user_by_id(p.user_id)
                tg_id = u.tg_id if u else None
            except Exception:
                tg_id = None
            out.append(
                {
                    "id": p.id,
                    "user_id": p.user_id,
                    "tg_id": tg_id,
                    "amount_rub": float(p.amount_rub or 0),
                    "plan": p.plan,
                    "status": p.status,
                    "days_granted": p.days_granted,
                    "granted_by_tg_id": p.granted_by_tg_id,
                    "notes": p.notes,
                    "created_at": p.created_at.isoformat(),
                }
            )
        return out
