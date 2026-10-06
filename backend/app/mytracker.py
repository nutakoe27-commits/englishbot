"""
mytracker.py — серверные события Android-приложения в VK MyTracker (S2S API).

Зачем. Кампания VK на установку видит установку через SDK MyTracker внутри
APK. Регистрация и оплата происходят в веб-части приложения (TWA), и SDK о
них не знает: моста между страницей и нативным кодом у TWA нет. Поэтому:

  1. Нативная обёртка при запуске добавляет к стартовому адресу
     ?mt_iid=<instanceId> (scripts/twa-mytracker.sh).
  2. Сайт запоминает его и после входа отдаёт сюда:
     POST /api/analytics/mt-install {instance_id}.
  3. Бэкенд хранит связку аккаунт ↔ установка (mytracker_installs) и шлёт в
     MyTracker события с этим instanceId:
       - registration — аккаунт создан незадолго до связки (вошёл впервые
         прямо в приложении);
       - login        — уже существующий аккаунт вошёл в приложении;
       - customRevenue + customEvent «subscription_paid» — оплата подписки.

Формат запросов — по официальному клиенту github.com/tracker-my-com/s2s-api:
POST https://tracker-s2s.my.com/v1/<method>/?idApp=<id>,
заголовок Authorization: <S2S-ключ>, JSON-тело с instanceId, customUserId,
eventTimestamp и полями метода. Параметры customEvent — только строки.

Всё fire-and-forget: ошибки MyTracker не должны мешать входу и оплате.
Без MYTRACKER_APP_ID / MYTRACKER_S2S_TOKEN модуль молчит.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from .config import settings
from .db import db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])

S2S_ENDPOINT = "https://tracker-s2s.my.com/v1"

# Аккаунт, созданный не раньше чем за это время до связки, считаем
# зарегистрированным в приложении (регистрация → вход → связка идут подряд).
REGISTRATION_WINDOW = timedelta(hours=2)

# instanceId у MyTracker — hex/UUID-подобная строка. Берём с запасом, но не
# пускаем в БД и в запросы что попало из адресной строки.
_IID_RE = re.compile(r"^[A-Za-z0-9\-_.]{8,64}$")


def enabled() -> bool:
    return bool(settings.MYTRACKER_APP_ID and settings.MYTRACKER_S2S_TOKEN)


def valid_instance_id(value: str) -> bool:
    return bool(_IID_RE.match(value or ""))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ts(dt: Optional[datetime]) -> int:
    """Unix timestamp; naive datetime в БД хранится в UTC."""
    if dt is None:
        return int(_utcnow().replace(tzinfo=timezone.utc).timestamp())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


async def _post(method: str, body: dict) -> bool:
    """Один запрос к S2S API. True — MyTracker ответил 2xx."""
    if not enabled():
        return False
    url = f"{S2S_ENDPOINT}/{method}/"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=5.0)) as client:
            r = await client.post(
                url,
                params={"idApp": int(settings.MYTRACKER_APP_ID or 0)},
                headers={"Authorization": str(settings.MYTRACKER_S2S_TOKEN)},
                json=body,
            )
        if 200 <= r.status_code < 300:
            return True
        logger.warning("[mytracker] %s → HTTP %s: %s", method, r.status_code, r.text[:300])
    except Exception as e:  # noqa: BLE001
        logger.warning("[mytracker] %s failed: %r", method, e)
    return False


def _fire(coro) -> None:
    try:
        asyncio.create_task(coro)
    except RuntimeError:
        logger.warning("[mytracker] нет event loop — событие пропущено")


# ─── События ─────────────────────────────────────────────────────────────────


async def send_user_event(kind: str, *, user_id: int, instance_id: str,
                          at: Optional[datetime] = None) -> bool:
    """registration | login."""
    return await _post(kind, {
        "customUserId": str(user_id),
        "instanceId": instance_id,
        "eventTimestamp": _ts(at),
    })


async def send_payment(*, user_id: int, instance_id: str, payment_id: int,
                       amount_rub: float, plan: str, days: int,
                       at: Optional[datetime] = None) -> None:
    """Выручка (customRevenue) + одноимённое веб-цели событие subscription_paid."""
    base = {
        "customUserId": str(user_id),
        "instanceId": instance_id,
        "eventTimestamp": _ts(at),
    }
    await _post("customRevenue", {
        **base,
        "idTransaction": f"yk-{payment_id}",
        "currency": "RUB",
        "total": float(amount_rub),
    })
    await _post("customEvent", {
        **base,
        "customEventName": "subscription_paid",
        "customEventParams": {
            "plan": str(plan),
            "amount_rub": str(int(amount_rub)) if float(amount_rub).is_integer() else str(amount_rub),
            "days": str(int(days)),
            "platform": "app",
        },
    })


# ─── Хранилище связок ────────────────────────────────────────────────────────


async def _link(user_id: int, instance_id: str) -> bool:
    """Сохранить связку. True — связка новая (первый вход с этой установки)."""
    from .db.models import MytrackerInstall
    now = _utcnow()
    async with db_session() as s:
        row = await s.get(MytrackerInstall, (user_id, instance_id))
        if row is not None:
            row.last_seen_at = now
            return False
        s.add(MytrackerInstall(user_id=user_id, instance_id=instance_id,
                               created_at=now, last_seen_at=now))
        return True


async def latest_instance_id(user_id: int) -> Optional[str]:
    """Установка, с которой аккаунт заходил последним."""
    from sqlalchemy import select
    from .db.models import MytrackerInstall
    async with db_session() as s:
        res = await s.execute(
            select(MytrackerInstall.instance_id)
            .where(MytrackerInstall.user_id == user_id)
            .order_by(MytrackerInstall.last_seen_at.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()


def track_payment_async(*, user_id: int, payment_id: int, amount_rub: float,
                        plan: str, days: int) -> None:
    """Вызывается из вебхука ЮKassa после зачисления. Шлёт оплату, только если
    аккаунт хоть раз заходил из Android-приложения."""
    if not enabled():
        return

    async def _run() -> None:
        try:
            iid = await latest_instance_id(user_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("[mytracker] lookup instance failed: %r", e)
            return
        if not iid:
            return
        await send_payment(user_id=user_id, instance_id=iid, payment_id=payment_id,
                           amount_rub=amount_rub, plan=plan, days=days)

    _fire(_run())


# ─── API для веб-части ───────────────────────────────────────────────────────


class _InstallIn(BaseModel):
    instance_id: str = Field(..., min_length=8, max_length=64)


@router.post("/mt-install")
async def mt_install(body: _InstallIn, authorization: Optional[str] = Header(None)) -> dict:
    """Привязать установку Android-приложения к вошедшему аккаунту."""
    iid = body.instance_id.strip()
    if not valid_instance_id(iid):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "bad instance_id")
    if not settings.DATABASE_URL:
        return {"ok": True, "linked": False}

    from . import auth as auth_lib
    from .db.repo import Repo
    async with db_session() as s:
        user = await auth_lib.resolve_user(Repo(s), authorization=authorization)
        user_id = int(user.id)
        created_at = user.created_at

    is_new = await _link(user_id, iid)
    if is_new and enabled():
        fresh = created_at is not None and (_utcnow() - created_at) <= REGISTRATION_WINDOW
        kind = "registration" if fresh else "login"
        _fire(send_user_event(kind, user_id=user_id, instance_id=iid, at=created_at if fresh else None))
        logger.info("[mytracker] linked user_id=%s iid=%s… event=%s", user_id, iid[:8], kind)
    return {"ok": True, "linked": is_new}
