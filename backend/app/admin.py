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

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from . import broadcast as broadcast_mod
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

class BattleMetrics(BaseModel):
    total: int = 0
    open: int = 0
    in_play: int = 0  # accepted + recording
    judged: int = 0
    judged_today: int = 0
    expired: int = 0


class QuestMetrics(BaseModel):
    assigned_total: int = 0
    completed_total: int = 0
    completed_today: int = 0
    active_now: int = 0  # выданные сегодня, ещё не выполнены/не протухли
    completion_rate: float = 0.0


class MetricsResponse(BaseModel):
    total_users: int
    active_subscriptions: int
    blocked_users: int
    dau: int = Field(description="Уникальные юзеры за сегодня (МСК)")
    wau: int = Field(description="За последние 7 дней включая сегодня")
    mau: int = Field(description="За последние 30 дней")
    minutes_today: int = Field(description="Суммарное время за сегодня (минуты)")
    total_revenue_rub: float
    new_users_today: int = 0
    battles: BattleMetrics = BattleMetrics()
    quests: QuestMetrics = QuestMetrics()


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


class UserBattleStats(BaseModel):
    total: int = 0
    won: int = 0
    lost: int = 0
    draw: int = 0
    in_progress: int = 0
    last_at: Optional[str] = None


class UserQuestStats(BaseModel):
    completed_total: int = 0
    completed_7d: int = 0
    active_key: Optional[str] = None
    active_title_ru: Optional[str] = None
    active_assigned_at: Optional[str] = None


class UserDetail(UserBrief):
    language_code: Optional[str]
    reminder_enabled: bool
    reminder_hour_msk: int
    used_seconds_today: int
    free_seconds_per_day: int
    bonus_seconds_today: int = 0
    used_seconds_total: int = 0
    battles: UserBattleStats = UserBattleStats()
    quests: UserQuestStats = UserQuestStats()


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


class BulkExtendRequest(BaseModel):
    days: int = Field(ge=1, le=3650)
    notes: Optional[str] = None
    granted_by_tg_id: Optional[int] = None


class BulkExtendResponse(BaseModel):
    ok: bool
    affected: int


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class BroadcastStartRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _user_to_detail(repo: Repo, u) -> UserDetail:
    """Полный профиль юзера — вернуть после POST-действия, чтобы фронт сразу обновился."""
    from sqlalchemy import select, func, and_, or_
    from .db.models import Battle, UserQuest, QuestCatalog, DailyUsage

    brief = await _user_to_brief(repo, u)
    used = await repo.get_used_seconds_today(u.id)
    free_seconds = await repo.get_kv_int("free_seconds_per_day", 600)
    bonus = await repo.get_bonus_seconds_today(u.id)

    # Всего практики за всё время.
    total_used_res = await repo.s.execute(
        select(func.coalesce(func.sum(DailyUsage.used_seconds), 0)).where(
            DailyUsage.user_id == u.id,
        )
    )
    used_total = int(total_used_res.scalar() or 0)

    # Battle-статистика.
    battles_stats = UserBattleStats()
    tg_id = u.tg_id
    battles_res = await repo.s.execute(
        select(Battle).where(
            or_(Battle.initiator_tg_id == tg_id, Battle.opponent_tg_id == tg_id)
        ).order_by(Battle.created_at.desc())
    )
    user_battles = battles_res.scalars().all()
    battles_stats.total = len(user_battles)
    if user_battles:
        battles_stats.last_at = (
            user_battles[0].created_at.isoformat() if user_battles[0].created_at else None
        )
    for b in user_battles:
        if b.status in ("open", "accepted", "recording"):
            battles_stats.in_progress += 1
            continue
        if b.status != "judged":
            continue
        is_a = b.initiator_tg_id == tg_id
        if b.winner == "tie":
            battles_stats.draw += 1
        elif (is_a and b.winner == "a") or (not is_a and b.winner == "b"):
            battles_stats.won += 1
        elif b.winner in ("a", "b"):
            battles_stats.lost += 1

    # Quest-статистика.
    quests_stats = UserQuestStats()
    completed_total_res = await repo.s.execute(
        select(func.count(UserQuest.id)).where(
            UserQuest.user_id == u.id,
            UserQuest.completed_at.is_not(None),
        )
    )
    quests_stats.completed_total = int(completed_total_res.scalar() or 0)
    week_ago = msk_today() - timedelta(days=6)
    completed_7d_res = await repo.s.execute(
        select(func.count(UserQuest.id)).where(
            UserQuest.user_id == u.id,
            UserQuest.completed_at.is_not(None),
            UserQuest.assigned_at >= week_ago,
        )
    )
    quests_stats.completed_7d = int(completed_7d_res.scalar() or 0)
    # Активный квест (сегодняшний, незавершённый).
    active_res = await repo.s.execute(
        select(UserQuest, QuestCatalog)
        .join(QuestCatalog, QuestCatalog.key == UserQuest.quest_key)
        .where(
            UserQuest.user_id == u.id,
            UserQuest.assigned_at >= msk_today(),
            UserQuest.completed_at.is_(None),
            UserQuest.expired_at.is_(None),
        )
        .order_by(UserQuest.assigned_at.desc())
        .limit(1)
    )
    active_row = active_res.first()
    if active_row is not None:
        uq, qc = active_row
        quests_stats.active_key = uq.quest_key
        quests_stats.active_title_ru = qc.title_ru if qc else None
        quests_stats.active_assigned_at = uq.assigned_at.isoformat() if uq.assigned_at else None

    return UserDetail(
        **brief.model_dump(),
        language_code=u.language_code,
        reminder_enabled=u.reminder_enabled,
        reminder_hour_msk=u.reminder_time.hour if u.reminder_time else 19,
        used_seconds_today=used,
        free_seconds_per_day=free_seconds,
        bonus_seconds_today=bonus,
        used_seconds_total=used_total,
        battles=battles_stats,
        quests=quests_stats,
    )


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
    from datetime import datetime
    from sqlalchemy import select, func
    from .db.models import Battle, UserQuest, User as UserModel

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

        # Новые сегодня: по created_at ≥ начало дня МСК (today-дата в колонке сохраняется UTC,
        # но для счётчика достаточно «UTC-день назад»: сравниваем с today в UTC)
        day_start_utc = datetime.combine(today, datetime.min.time())
        new_today_res = await s.execute(
            select(func.count(UserModel.id)).where(UserModel.created_at >= day_start_utc)
        )
        new_users_today = int(new_today_res.scalar() or 0)

        # Battle metrics.
        bm = BattleMetrics()
        battle_rows = await s.execute(
            select(Battle.status, func.count(Battle.id)).group_by(Battle.status)
        )
        for status_val, cnt in battle_rows.all():
            cnt = int(cnt or 0)
            bm.total += cnt
            if status_val == "open":
                bm.open = cnt
            elif status_val in ("accepted", "recording"):
                bm.in_play += cnt
            elif status_val == "judged":
                bm.judged = cnt
            elif status_val == "expired":
                bm.expired = cnt
        judged_today_res = await s.execute(
            select(func.count(Battle.id)).where(
                Battle.status == "judged",
                Battle.updated_at >= day_start_utc,
            )
        )
        bm.judged_today = int(judged_today_res.scalar() or 0)

        # Quest metrics.
        qm = QuestMetrics()
        assigned_res = await s.execute(select(func.count(UserQuest.id)))
        qm.assigned_total = int(assigned_res.scalar() or 0)
        completed_res = await s.execute(
            select(func.count(UserQuest.id)).where(UserQuest.completed_at.is_not(None))
        )
        qm.completed_total = int(completed_res.scalar() or 0)
        completed_today_res = await s.execute(
            select(func.count(UserQuest.id)).where(
                UserQuest.completed_at.is_not(None),
                UserQuest.completed_at >= day_start_utc,
            )
        )
        qm.completed_today = int(completed_today_res.scalar() or 0)
        active_res = await s.execute(
            select(func.count(UserQuest.id)).where(
                UserQuest.assigned_at >= day_start_utc,
                UserQuest.completed_at.is_(None),
                UserQuest.expired_at.is_(None),
            )
        )
        qm.active_now = int(active_res.scalar() or 0)
        qm.completion_rate = (
            round(qm.completed_total / qm.assigned_total, 3) if qm.assigned_total else 0.0
        )

        return MetricsResponse(
            total_users=total,
            active_subscriptions=subs,
            blocked_users=blocked,
            dau=dau,
            wau=wau,
            mau=mau,
            minutes_today=seconds_today // 60,
            total_revenue_rub=revenue,
            new_users_today=new_users_today,
            battles=bm,
            quests=qm,
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
        return await _user_to_detail(repo, u)


@router.post(
    "/users/{user_id}/grant-subscription",
    response_model=UserDetail,
    dependencies=[Depends(require_admin_token)],
)
async def grant_subscription(user_id: int, req: GrantRequest) -> UserDetail:
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
        u2 = await repo.get_user_by_id(user_id)
        return await _user_to_detail(repo, u2)


@router.post(
    "/users/{user_id}/block",
    response_model=UserDetail,
    dependencies=[Depends(require_admin_token)],
)
async def block_user(user_id: int, req: BlockRequest) -> UserDetail:
    async with db_session() as s:
        repo = Repo(s)
        u = await repo.get_user_by_id(user_id)
        if u is None:
            raise HTTPException(404, "Юзер не найден")
        await repo.set_blocked(u, req.blocked)
        u2 = await repo.get_user_by_id(user_id)
        return await _user_to_detail(repo, u2)


@router.post(
    "/users/{user_id}/reminder",
    response_model=UserDetail,
    dependencies=[Depends(require_admin_token)],
)
async def update_reminder(user_id: int, req: ReminderRequest) -> UserDetail:
    async with db_session() as s:
        repo = Repo(s)
        u = await repo.get_user_by_id(user_id)
        if u is None:
            raise HTTPException(404, "Юзер не найден")
        await repo.set_reminder(
            u, enabled=req.enabled, reminder_hour=req.hour_msk
        )
        u2 = await repo.get_user_by_id(user_id)
        return await _user_to_detail(repo, u2)


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


# ─── Массовые операции ──────────────────────────────────────────────────────

@router.post(
    "/subscription/extend-all",
    response_model=BulkExtendResponse,
    dependencies=[Depends(require_admin_token)],
)
async def extend_all_active_subscriptions(req: BulkExtendRequest) -> BulkExtendResponse:
    """Продлить подписку всем юзерам, у которых она сейчас активна."""
    async with db_session() as s:
        repo = Repo(s)
        # plan=admin_grant — единственный подходящий из ENUM (payments.plan):
        # monthly | yearly | gift | admin_grant.
        # Чтобы отличать массовое продление от одиночного — помечаем в notes.
        tag = "[bulk]"
        user_note = (req.notes or "").strip()
        combined_notes = (
            f"{tag} +{req.days}d" if not user_note else f"{tag} +{req.days}d — {user_note}"
        )
        affected = await repo.bulk_extend_active_subscriptions(
            days=req.days,
            plan="admin_grant",
            granted_by_tg_id=req.granted_by_tg_id,
            notes=combined_notes,
        )
        return BulkExtendResponse(ok=True, affected=affected)


@router.post(
    "/users/{user_id}/message",
    dependencies=[Depends(require_admin_token)],
)
async def send_user_message(user_id: int, req: MessageRequest) -> dict:
    """Отправить сообщение одному юзеру от имени бота."""
    if not settings.BOT_TOKEN:
        raise HTTPException(503, "BOT_TOKEN не задан в .env")
    async with db_session() as s:
        repo = Repo(s)
        u = await repo.get_user_by_id(user_id)
        if u is None:
            raise HTTPException(404, "Юзер не найден")
        if not u.tg_id:
            raise HTTPException(400, "У юзера нет tg_id")

        async with httpx.AsyncClient() as client:
            ok, code, retry_after = await broadcast_mod.send_message_to_tg(
                client, u.tg_id, req.text
            )

        if ok:
            return {"ok": True, "delivered": True}

        # 403 — бот заблокирован узером: помечаем и возвращаем ошибку
        if code == 403:
            await repo.set_blocked(u, True)
            raise HTTPException(
                409, "Пользователь заблокировал бота (помечен как is_blocked)"
            )
        detail = f"Telegram API error (status={code})"
        if retry_after:
            detail += f", retry_after={retry_after}s"
        raise HTTPException(502, detail)


@router.post(
    "/broadcast",
    dependencies=[Depends(require_admin_token)],
)
async def start_broadcast(req: BroadcastStartRequest) -> dict:
    """Запустить фоновую рассылку всем незаблокированным юзерам."""
    if not settings.BOT_TOKEN:
        raise HTTPException(503, "BOT_TOKEN не задан в .env")
    try:
        job = await broadcast_mod.start_broadcast(req.text)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "job_id": job.job_id}


@router.get(
    "/broadcast/status",
    dependencies=[Depends(require_admin_token)],
)
async def broadcast_status() -> dict:
    """Текущий статус рассылки (или последней завершённой)."""
    job = broadcast_mod.current_job()
    if job is None:
        return {"ok": True, "job": None}
    return {"ok": True, "job": job.to_dict()}


@router.post(
    "/broadcast/cancel",
    dependencies=[Depends(require_admin_token)],
)
async def cancel_broadcast() -> dict:
    ok = await broadcast_mod.cancel_broadcast()
    if not ok:
        raise HTTPException(409, "В данный момент нет активной рассылки")
    return {"ok": True, "cancelled": True}


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


# ─── Battle & Quest (админка) ────────────────────────────────────────────────

@router.get(
    "/battles",
    dependencies=[Depends(require_admin_token)],
)
async def admin_battles(
    limit: int = Query(50, ge=1, le=500),
    status_filter: Optional[str] = Query(None, alias="status"),
) -> list[dict]:
    """Список последних battle'ов. Фильтр по статусу опционален."""
    from sqlalchemy import select, desc
    from .db.models import Battle, User

    async with db_session() as s:
        stmt = select(Battle).order_by(desc(Battle.created_at)).limit(limit)
        if status_filter:
            stmt = stmt.where(Battle.status == status_filter)
        res = await s.execute(stmt)
        battles = res.scalars().all()

        # Подтянем имена участников одним запросом
        tg_ids: set[int] = set()
        for b in battles:
            if b.initiator_tg_id:
                tg_ids.add(b.initiator_tg_id)
            if b.opponent_tg_id:
                tg_ids.add(b.opponent_tg_id)
        users_by_tg: dict[int, User] = {}
        if tg_ids:
            urs = await s.execute(select(User).where(User.tg_id.in_(tg_ids)))
            for u in urs.scalars().all():
                users_by_tg[u.tg_id] = u

        def _name(tg_id: Optional[int]) -> Optional[str]:
            if not tg_id:
                return None
            u = users_by_tg.get(tg_id)
            if not u:
                return f"tg:{tg_id}"
            name = " ".join(x for x in (u.first_name, u.last_name) if x)
            return name.strip() or (f"@{u.username}" if u.username else f"tg:{tg_id}")

        out = []
        for b in battles:
            a_sum = sum(int(v) for v in (b.a_score or {}).values()) if b.a_score else None
            b_sum = sum(int(v) for v in (b.b_score or {}).values()) if b.b_score else None
            out.append({
                "id": b.id,
                "status": b.status,
                "topic_key": b.topic_key,
                "initiator_tg_id": b.initiator_tg_id,
                "opponent_tg_id": b.opponent_tg_id,
                "initiator_name": _name(b.initiator_tg_id),
                "opponent_name": _name(b.opponent_tg_id),
                "a_recorded": bool(b.a_audio_path),
                "b_recorded": bool(b.b_audio_path),
                "a_score_total": a_sum,
                "b_score_total": b_sum,
                "winner": b.winner,
                "judge_comment": b.judge_comment,
                "created_at": b.created_at.isoformat() if b.created_at else None,
                "updated_at": b.updated_at.isoformat() if b.updated_at else None,
                "expires_at": b.expires_at.isoformat() if b.expires_at else None,
            })
        return out


@router.get(
    "/battles/stats",
    dependencies=[Depends(require_admin_token)],
)
async def admin_battles_stats() -> dict:
    """Счётчики по статусам battle."""
    from sqlalchemy import select, func
    from .db.models import Battle

    async with db_session() as s:
        res = await s.execute(
            select(Battle.status, func.count(Battle.id)).group_by(Battle.status)
        )
        rows = res.all()
    out = {"open": 0, "accepted": 0, "recording": 0,
           "judged": 0, "expired": 0, "canceled": 0, "total": 0}
    for status_val, cnt in rows:
        out[status_val] = int(cnt)
        out["total"] += int(cnt)
    return out


@router.get(
    "/quests/stats",
    dependencies=[Depends(require_admin_token)],
)
async def admin_quests_stats() -> dict:
    """Агрегация user_quests по quest_key: сколько выдано / выполнено / протухло."""
    from sqlalchemy import select, func
    from .db.models import UserQuest, QuestCatalog

    async with db_session() as s:
        # По каждому key — assigned, completed, expired.
        res = await s.execute(
            select(
                UserQuest.quest_key,
                func.count(UserQuest.id).label("total"),
                func.sum(
                    func.if_(UserQuest.completed_at.is_not(None), 1, 0)
                ).label("completed"),
                func.sum(
                    func.if_(UserQuest.expired_at.is_not(None), 1, 0)
                ).label("expired"),
            ).group_by(UserQuest.quest_key)
        )
        rows = res.all()

        # Также тянем каталог для отображения заголовков/типов.
        cat_res = await s.execute(select(QuestCatalog))
        catalog = {q.key: q for q in cat_res.scalars().all()}

    per_quest = []
    total_assigned = 0
    total_completed = 0
    total_expired = 0
    for key, total, completed, expired in rows:
        completed = int(completed or 0)
        expired = int(expired or 0)
        total = int(total or 0)
        total_assigned += total
        total_completed += completed
        total_expired += expired
        q = catalog.get(key)
        per_quest.append({
            "key": key,
            "title_ru": q.title_ru if q else key,
            "type": q.type if q else "?",
            "difficulty": q.difficulty if q else "?",
            "target_level": q.target_level if q else "?",
            "assigned": total,
            "completed": completed,
            "expired": expired,
            "completion_rate": round(completed / total, 3) if total else 0.0,
        })
    per_quest.sort(key=lambda r: -r["assigned"])

    return {
        "total_assigned": total_assigned,
        "total_completed": total_completed,
        "total_expired": total_expired,
        "completion_rate": round(total_completed / total_assigned, 3) if total_assigned else 0.0,
        "per_quest": per_quest,
    }
