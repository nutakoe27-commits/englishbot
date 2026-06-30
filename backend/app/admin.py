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

import logging
from datetime import date, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from . import broadcast as broadcast_mod
from . import presence
from .config import settings
from .db import db_session
from .db.repo import Repo, msk_today

logger = logging.getLogger(__name__)

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

class ModeStat(BaseModel):
    sessions: int = 0
    minutes: int = 0


class ActiveAvg(BaseModel):
    """Средние показатели «активного» юзера (заходил в бота более 2 раз)."""
    active_users: int = 0
    avg_minutes_total: float = 0.0
    # Среднее время на активного юзера по режимам (минуты).
    by_mode_minutes: dict[str, float] = Field(default_factory=dict)


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
    # Сколько юзеров вообще активировали бота в Telegram (написали /start
    # или хоть что-то). Считается отдельно от total_users, т.к. до миграции
    # 0009 в users попадали только те, кто открыл Mini App.
    bot_activated_total: int = 0
    bot_activated_today: int = 0
    # Разбивка сессий за сегодня по режимам (voice/chat/listening/grammar).
    modes_today: dict[str, ModeStat] = Field(default_factory=dict)
    # Топ категорий listening-подкастов за 7 дней.
    listening_top_categories: list[dict] = Field(default_factory=list)
    # Средний «активный» юзер (заходил более 2 раз) — время по режимам.
    active_avg: ActiveAvg = Field(default_factory=ActiveAvg)        # за всё время
    active_avg_30d: ActiveAvg = Field(default_factory=ActiveAvg)    # за последние 30 дней


class UserBrief(BaseModel):
    id: int
    tg_id: Optional[int] = None
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    email: Optional[str] = None
    auth_providers: list[str] = Field(default_factory=list)
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
    bonus_seconds_today: int = 0
    used_seconds_total: int = 0
    # Активность (retention v1 + listening)
    streak_current: int = 0
    streak_best: int = 0
    last_practice_date: Optional[str] = None
    minutes_by_mode: dict[str, int] = Field(default_factory=dict)  # {'voice': N, ...}
    words_count: int = 0
    achievements_earned: int = 0
    achievements_total: int = 0
    # Grammar Learn: пройдено тем / всего активных тем в каталоге.
    grammar_topics_done: int = 0
    grammar_topics_total: int = 0


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
    from sqlalchemy import select, func
    from .db.models import DailyUsage

    brief = await _user_to_brief(repo, u)
    used = await repo.get_used_seconds_today(u.id)
    free_seconds = await repo.get_kv_int("free_seconds_per_day", 300)
    bonus = await repo.get_bonus_seconds_today(u.id)

    # Всего практики за всё время.
    total_used_res = await repo.s.execute(
        select(func.coalesce(func.sum(DailyUsage.used_seconds), 0)).where(
            DailyUsage.user_id == u.id,
        )
    )
    used_total = int(total_used_res.scalar() or 0)

    # Активность: стрик, минуты по режимам, словарь, медали.
    streak_current, streak_best, last_practice = await repo.get_streak(u.id)
    seconds_by_mode = await repo.user_total_seconds_by_mode(u.id)
    minutes_by_mode = {m: sec // 60 for m, sec in seconds_by_mode.items()}
    words_count = await repo.count_user_words(u.id)
    try:
        grammar_done, grammar_total = await repo.grammar_learn_counters(u.id)
    except Exception as exc:
        # До миграции 0011 таблиц нет — не валим профиль целиком.
        logger.warning("[admin] grammar counters failed: %r", exc)
        grammar_done, grammar_total = 0, 0
    try:
        from .achievements import ACHIEVEMENTS, get_earned_keys
        earned = await get_earned_keys(repo, u.id)
        achievements_earned = len(earned)
        achievements_total = len(ACHIEVEMENTS)
    except Exception as exc:
        logger.warning("[admin] achievements load failed: %r", exc)
        achievements_earned = 0
        achievements_total = 0

    return UserDetail(
        **brief.model_dump(),
        language_code=u.language_code,
        reminder_enabled=u.reminder_enabled,
        reminder_hour_msk=u.reminder_time.hour if u.reminder_time else 19,
        used_seconds_today=used,
        free_seconds_per_day=free_seconds,
        bonus_seconds_today=bonus,
        used_seconds_total=used_total,
        streak_current=streak_current,
        streak_best=streak_best,
        last_practice_date=last_practice.isoformat() if last_practice else None,
        minutes_by_mode=minutes_by_mode,
        words_count=words_count,
        achievements_earned=achievements_earned,
        achievements_total=achievements_total,
        grammar_topics_done=grammar_done,
        grammar_topics_total=grammar_total,
    )


async def _user_to_brief(repo: Repo, u) -> UserBrief:
    has_sub = await repo.has_active_subscription(u)
    try:
        providers = [i["provider"] for i in await repo.list_identities(u.id)]
    except Exception:
        providers = []
    return UserBrief(
        id=u.id,
        tg_id=u.tg_id,
        username=u.username,
        first_name=u.first_name,
        last_name=u.last_name,
        email=getattr(u, "email", None),
        auth_providers=providers,
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
    # /metrics — самый тяжёлый запрос в админке (десяток aggregate-запросов).
    # Кешируем 60 сек: дашборд опрашивает его при каждом открытии админки,
    # а данные меняются медленно. На single-process бэкенде хватает простого
    # in-memory TTL (_cached_60s ниже).
    return await _cached_60s("admin:metrics", _build_metrics)


async def _build_metrics() -> MetricsResponse:
    from datetime import datetime
    from sqlalchemy import select, func
    from .db.models import User as UserModel

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

        # Bot activations (миграция 0009): сколько вообще написало боту.
        bot_activated_total = await repo.count_bot_activated()
        bot_activated_today = await repo.count_bot_activated_today()

        # Разбивка сегодняшних сессий по режимам + топ listening-категорий (7д).
        breakdown = await repo.sessions_breakdown_since(day_start_utc)
        modes_today = {
            mode: ModeStat(sessions=cnt, minutes=secs // 60)
            for mode, (cnt, secs) in breakdown.items()
        }
        top_categories = await repo.listening_top_categories(
            day_start_utc - timedelta(days=6), limit=5,
        )

        # Средний «активный» юзер (заходил более 2 раз) — время по режимам.
        # voice+chat сводим в «speaking», как и в mini-app / профиле.
        def _to_active_avg(avg_raw: dict) -> ActiveAvg:
            n_active = int(avg_raw.get("active_users") or 0)
            by_mode_sec = avg_raw.get("by_mode_seconds") or {}
            if n_active <= 0:
                return ActiveAvg()
            speaking_sec = int(by_mode_sec.get("voice", 0)) + int(by_mode_sec.get("chat", 0))
            grouped = {
                "speaking": speaking_sec,
                "listening": int(by_mode_sec.get("listening", 0)),
                "grammar": int(by_mode_sec.get("grammar", 0)),
                "srs": int(by_mode_sec.get("srs", 0)),
            }
            return ActiveAvg(
                active_users=n_active,
                avg_minutes_total=round(int(avg_raw.get("total_seconds") or 0) / n_active / 60, 1),
                by_mode_minutes={m: round(s / n_active / 60, 1) for m, s in grouped.items()},
            )

        active_avg = _to_active_avg(
            await repo.active_user_avg_seconds_by_mode(min_sessions_exclusive=2)
        )
        active_avg_30d = _to_active_avg(
            await repo.active_user_avg_seconds_by_mode(
                min_sessions_exclusive=2,
                since_dt=datetime.utcnow() - timedelta(days=30),
            )
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
            bot_activated_total=bot_activated_total,
            bot_activated_today=bot_activated_today,
            modes_today=modes_today,
            listening_top_categories=top_categories,
            active_avg=active_avg,
            active_avg_30d=active_avg_30d,
        )


# ─── Онлайн: кто сейчас занимается ─────────────────────────────────────────────

class OnlineSession(BaseModel):
    user_id: int
    tg_id: Optional[int] = None
    username: Optional[str] = None
    first_name: Optional[str] = None
    mode: str
    level: Optional[str] = None
    role: Optional[str] = None
    duration_sec: int = 0


class OnlineResponse(BaseModel):
    count: int = 0
    by_mode: dict[str, int] = Field(default_factory=dict)
    sessions: list[OnlineSession] = Field(default_factory=list)


@router.get(
    "/online",
    response_model=OnlineResponse,
    dependencies=[Depends(require_admin_token)],
)
async def admin_online() -> OnlineResponse:
    """In-memory снапшот активных сессий (voice/chat WS + listening/grammar/srs).
    Обновляется онлайн-панелью каждые ~5с. Работает на single-worker backend."""
    entries = presence.snapshot()
    by_mode: dict[str, int] = {
        "voice": 0, "chat": 0, "listening": 0, "grammar": 0, "srs": 0,
    }
    for e in entries:
        by_mode[e["mode"]] = by_mode.get(e["mode"], 0) + 1

    sessions: list[OnlineSession] = []
    if entries:
        async with db_session() as s:
            repo = Repo(s)
            for e in entries:
                u = await repo.get_user_by_id(e["user_id"])
                sessions.append(OnlineSession(
                    user_id=e["user_id"],
                    tg_id=u.tg_id if u else None,
                    username=u.username if u else None,
                    first_name=u.first_name if u else None,
                    mode=e["mode"],
                    level=e["level"],
                    role=e["role"],
                    duration_sec=e["duration_sec"],
                ))
    sessions.sort(key=lambda x: x.duration_sec, reverse=True)
    return OnlineResponse(count=len(entries), by_mode=by_mode, sessions=sessions)


@router.get(
    "/users",
    response_model=list[UserBrief],
    dependencies=[Depends(require_admin_token)],
)
async def search_users(
    q: str = Query(default="", description="username, имя или tg_id"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[UserBrief]:
    async with db_session() as s:
        repo = Repo(s)
        users = await repo.search_users(q, limit=limit, offset=offset)
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


@router.delete(
    "/users/{user_id}",
    dependencies=[Depends(require_admin_token)],
)
async def delete_user(user_id: int) -> dict:
    """Hard-delete юзера (CASCADE подчистит sessions/words/payments/identities).

    Подтверждение делает фронт (карточка с двойным чек-боксом + ввод имени).
    """
    async with db_session() as s:
        repo = Repo(s)
        u = await repo.get_user_by_id(user_id)
        if u is None:
            raise HTTPException(404, "Юзер не найден")
        ok = await repo.delete_user(user_id)
        if not ok:
            raise HTTPException(500, "Удаление не выполнено")
        await s.commit()
    return {"ok": True}


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


# ─── Полный список платежей (вкладка «Платежи» в admin) ──────────────
@router.get("/payments", dependencies=[Depends(require_admin_token)])
async def list_payments(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: Optional[str] = Query(default=None),
    plan: Optional[str] = Query(default=None),
) -> dict:
    async with db_session() as s:
        repo = Repo(s)
        rows, total = await repo.list_payments(
            limit=limit, offset=offset, status=status, plan=plan,
        )
        items: list[dict] = []
        for p in rows:
            tg_id: Optional[int] = None
            username: Optional[str] = None
            try:
                u = await repo.get_user_by_id(p.user_id)
                if u:
                    tg_id = u.tg_id
                    username = u.username
            except Exception:
                pass
            items.append(
                {
                    "id": p.id,
                    "user_id": p.user_id,
                    "tg_id": tg_id,
                    "username": username,
                    "amount_rub": float(p.amount_rub or 0),
                    "plan": p.plan,
                    "status": p.status,
                    "days_granted": p.days_granted,
                    "granted_by_tg_id": p.granted_by_tg_id,
                    "notes": p.notes,
                    "created_at": p.created_at.isoformat(),
                }
            )
        return {"items": items, "total": total, "limit": limit, "offset": offset}


# График платежей за ТЕКУЩИЙ календарный месяц (1-е → конец).
@router.get(
    "/payments/month-chart", dependencies=[Depends(require_admin_token)]
)
async def payments_month_chart() -> dict:
    async with db_session() as s:
        repo = Repo(s)
        return await repo.revenue_month_chart()


# ─── Промокоды ────────────────────────────────────────────────────────────────
class _CreatePromoIn(BaseModel):
    code: str
    discount_percent: int


class _TogglePromoIn(BaseModel):
    active: bool


@router.get("/promo", dependencies=[Depends(require_admin_token)])
async def admin_list_promos() -> dict:
    async with db_session() as s:
        repo = Repo(s)
        rows = await repo.list_promos()
    return {"items": [
        {
            "code": p.code,
            "discount_percent": p.discount_percent,
            "active": bool(p.active),
            "used_count": p.used_count,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in rows
    ]}


@router.post("/promo", dependencies=[Depends(require_admin_token)])
async def admin_create_promo(body: _CreatePromoIn) -> dict:
    code = (body.code or "").strip().upper()
    if not code or len(code) > 32:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_code")
    pct = int(body.discount_percent)
    if pct < 1 or pct > 100:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_discount")
    async with db_session() as s:
        repo = Repo(s)
        if await repo.get_promo(code) is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "promo_exists")
        p = await repo.create_promo(code, pct)
        await s.commit()
        return {
            "code": p.code,
            "discount_percent": p.discount_percent,
            "active": bool(p.active),
            "used_count": p.used_count,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }


@router.post("/promo/{code}/toggle", dependencies=[Depends(require_admin_token)])
async def admin_toggle_promo(code: str, body: _TogglePromoIn) -> dict:
    async with db_session() as s:
        repo = Repo(s)
        ok = await repo.set_promo_active(code, bool(body.active))
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "promo_not_found")
        await s.commit()
    return {"ok": True, "code": code.strip().upper(), "active": bool(body.active)}


@router.get("/promo/{code}/activations", dependencies=[Depends(require_admin_token)])
async def admin_promo_activations(code: str) -> dict:
    async with db_session() as s:
        repo = Repo(s)
        items = await repo.list_promo_activations(code)
    return {"items": items, "total": len(items)}


# ─── Admin v2: charts/retention/sessions ─────────────────────────────────────
# Для дашборда с графиками. Метрики тяжёлые (агрегаты по datetime/group by),
# поэтому держим в памяти 60-сек TTL-кеш. На single-worker'е этого хватает;
# multi-worker — кеш per-process, что приемлемо.

import time as _time_mod  # noqa: E402

_METRICS_CACHE: dict[str, tuple[float, object]] = {}
_METRICS_TTL_SEC = 60.0


async def _cached_60s(key: str, builder):
    """key → cached value. builder — async callable, вызывается при miss."""
    now = _time_mod.time()
    hit = _METRICS_CACHE.get(key)
    if hit is not None and now - hit[0] < _METRICS_TTL_SEC:
        return hit[1]
    val = await builder()
    _METRICS_CACHE[key] = (now, val)
    return val


def _clamp_days(d: int) -> int:
    return max(7, min(d, 90))


@router.get("/charts/dau", dependencies=[Depends(require_admin_token)])
async def chart_dau(days: int = Query(30, ge=7, le=90)) -> dict:
    days = _clamp_days(days)

    async def build():
        async with db_session() as s:
            return await Repo(s).dau_series(days)

    series = await _cached_60s(f"dau:{days}", build)
    return {"series": series}


@router.get("/charts/new-users", dependencies=[Depends(require_admin_token)])
async def chart_new_users(days: int = Query(30, ge=7, le=90)) -> dict:
    days = _clamp_days(days)

    async def build():
        async with db_session() as s:
            return await Repo(s).new_users_series(days)

    series = await _cached_60s(f"new_users:{days}", build)
    return {"series": series}


@router.get("/charts/revenue", dependencies=[Depends(require_admin_token)])
async def chart_revenue(days: int = Query(30, ge=7, le=90)) -> dict:
    days = _clamp_days(days)

    async def build():
        async with db_session() as s:
            return await Repo(s).revenue_series(days)

    series = await _cached_60s(f"revenue:{days}", build)
    return {"series": series}


@router.get("/retention", dependencies=[Depends(require_admin_token)])
async def retention(days: int = Query(30, ge=7, le=90)) -> dict:
    days = _clamp_days(days)

    async def build():
        async with db_session() as s:
            return await Repo(s).retention_cohort(days)

    cohorts = await _cached_60s(f"retention:{days}", build)
    return {"cohorts": cohorts}


@router.get(
    "/users/{user_id}/sessions",
    dependencies=[Depends(require_admin_token)],
)
async def user_sessions(
    user_id: int, limit: int = Query(30, ge=1, le=100)
) -> dict:
    """Список последних сессий юзера (без транскрипта диалога)."""
    async with db_session() as s:
        sessions = await Repo(s).user_sessions(user_id, limit)
    return {"sessions": sessions}
