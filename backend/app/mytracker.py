"""Android funnel events linked to the native SDK instanceId and queued in SQL.

The S2S wire format follows github.com/tracker-my-com/s2s-api. Credentials are
read only when sending, never saved in payloads. A 2xx response means accepted
by MyTracker, not yet visible in its reports or delivered to VK Ads.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import logging
import re
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert

from .admin import require_admin_token
from .config import settings
from .db import db_session
from .db.models import MytrackerEvent, MytrackerInstall, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["Analytics"])
S2S_ENDPOINT = "https://tracker-s2s.my.com/v1"
REGISTRATION_WINDOW = timedelta(hours=2)
_IID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
current_instance_id: ContextVar[Optional[str]] = ContextVar("mytracker_instance_id", default=None)


def _app_id() -> Optional[int]:
    raw = str(settings.MYTRACKER_APP_ID or "").strip().strip('"').strip("'")
    return int(raw) if raw.isascii() and raw.isdigit() and int(raw) > 0 else None


def _token() -> str:
    return str(settings.MYTRACKER_S2S_TOKEN or "").strip().strip('"').strip("'")


def enabled() -> bool:
    return bool(_app_id() and _token())


def valid_instance_id(value: Optional[str]) -> bool:
    # The native SDK supplies the UUID documented in the official S2S client.
    return bool(_IID_RE.fullmatch(value or ""))


def _utcnow() -> datetime:
    # The migrations use DATETIME(0); do not let MySQL round a due event forward.
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _ts(at: Optional[datetime] = None) -> int:
    at = at or _utcnow()
    return int(at.replace(tzinfo=timezone.utc).timestamp() if at.tzinfo is None else at.timestamp())


def _base(user_id: int, instance_id: str, at: Optional[datetime] = None) -> dict:
    return {"customUserId": str(user_id), "instanceId": instance_id, "eventTimestamp": _ts(at)}


async def _post(method: str, body: dict) -> bool:
    if not enabled():
        return False
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=5.0)) as client:
            response = await client.post(
                f"{S2S_ENDPOINT}/{method}", params={"idApp": _app_id()},
                headers={"Authorization": _token()}, json=body,
            )
        if 200 <= response.status_code < 300:
            logger.info("[mytracker] accepted method=%s", method)
            return True
        # Do not log response bodies: remote errors may echo identifiers/secrets.
        logger.warning("[mytracker] method=%s HTTP=%s", method, response.status_code)
    except httpx.HTTPError as exc:
        logger.warning("[mytracker] method=%s transport=%s", method, type(exc).__name__)
    return False


async def enqueue(session, *, user_id: int, key: str, method: str, payload: dict) -> None:
    now = _utcnow()
    statement = insert(MytrackerEvent).values(
        user_id=user_id, dedup_key=key, method=method, payload=payload,
        attempts=0, created_at=now, next_attempt_at=now,
    )
    # Duplicate business callbacks must never reset a successful delivery.
    await session.execute(statement.on_duplicate_key_update(dedup_key=statement.inserted.dedup_key))


async def link_in_session(session, *, user_id: int, instance_id: str) -> bool:
    """Link and enqueue the first account event in the same transaction.

    Existing links from migration 0038 have no delivery history. Do not invent
    historical registrations or replay events which may already have arrived.
    """
    row = await session.get(MytrackerInstall, (user_id, instance_id))
    is_new = row is None
    now = _utcnow()
    statement = insert(MytrackerInstall).values(
        user_id=user_id, instance_id=instance_id, created_at=now, last_seen_at=now,
    )
    await session.execute(statement.on_duplicate_key_update(last_seen_at=now))
    if is_new:
        user = await session.get(User, user_id)
        if user is not None:
            created_at = user.created_at
            if created_at and created_at.tzinfo:
                created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
            # Existing users.created_at writes may round up to the next second.
            fresh = created_at is not None and -timedelta(seconds=1) <= now - created_at <= REGISTRATION_WINDOW
            kind = "registration" if fresh else "login"
            await enqueue(session, user_id=user_id, key=f"install:{user_id}:{instance_id}",
                          method=kind, payload=_base(user_id, instance_id, created_at if fresh else now))
    return is_new


async def enqueue_session_started(session, *, user_id: int, session_id: int, mode: str,
                                  at: datetime, instance_id: Optional[str]) -> None:
    if not valid_instance_id(instance_id):
        return
    # Link here too: login's asynchronous HTTP link can race the first lesson.
    await link_in_session(session, user_id=user_id, instance_id=instance_id)
    await enqueue(session, user_id=user_id, key=f"session:{session_id}", method="customEvent",
                  payload={**_base(user_id, instance_id, at), "customEventName": "session_started",
                           "customEventParams": {"mode": str(mode), "session_id": str(session_id), "platform": "app"}})


async def enqueue_payment(session, payment) -> None:
    """Only called for a verified succeeded payment, in the credit transaction."""
    iid = await session.scalar(select(MytrackerInstall.instance_id)
                               .where(MytrackerInstall.user_id == payment.user_id)
                               .order_by(MytrackerInstall.last_seen_at.desc(), MytrackerInstall.instance_id)
                               .limit(1))
    if not iid or not valid_instance_id(iid):
        return
    base = _base(int(payment.user_id), iid, payment.updated_at)
    amount = float(payment.amount_rub or 0)
    await enqueue(session, user_id=int(payment.user_id), key=f"payment:{payment.id}:revenue", method="customRevenue",
                  payload={**base, "idTransaction": f"yk-{payment.id}", "currency": "RUB", "total": amount})
    await enqueue(session, user_id=int(payment.user_id), key=f"payment:{payment.id}:event", method="customEvent",
                  payload={**base, "customEventName": "subscription_paid", "customEventParams": {
                      "plan": str(payment.plan), "amount_rub": str(int(amount)) if amount.is_integer() else str(amount),
                      "days": str(int(payment.days_granted or 0)), "payment_id": str(payment.id), "platform": "app"}})


async def deliver_pending(limit: int = 25) -> int:
    """Lock one row at a time; successful sub-events are never replayed locally.

    Like any HTTP outbox, ambiguous network timeouts can cause a remote replay.
    customRevenue carries the stable transaction ID required by MyTracker.
    """
    if not enabled():
        return 0
    processed = 0
    for _ in range(limit):
        async with db_session() as session:
            row = await session.scalar(select(MytrackerEvent)
                                       .where(MytrackerEvent.sent_at.is_(None), MytrackerEvent.next_attempt_at <= _utcnow())
                                       .order_by(MytrackerEvent.id).limit(1).with_for_update())
            if row is None:
                break
            row.attempts += 1
            if await _post(row.method, row.payload):
                row.sent_at = _utcnow()
            else:
                delay = min(3600, 15 * 2 ** min(row.attempts, 8))
                row.next_attempt_at = _utcnow() + timedelta(seconds=delay)
            processed += 1
    return processed


async def delivery_loop() -> None:
    logger.info("[mytracker] S2S enabled=%s app_id_configured=%s", enabled(), bool(_app_id()))
    while True:
        try:
            await deliver_pending()
        except Exception as exc:
            logger.warning("[mytracker] delivery queue failed=%s; check migration 0041", type(exc).__name__)
        await asyncio.sleep(5)


class _InstallIn(BaseModel):
    instance_id: str = Field(..., min_length=36, max_length=36)


@router.post("/mt-install")
async def mt_install(body: _InstallIn, authorization: Optional[str] = Header(None)) -> dict:
    iid = body.instance_id.strip()
    if not valid_instance_id(iid):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "bad instance_id")
    if not settings.DATABASE_URL:
        return {"ok": True, "linked": False, "tracking_enabled": enabled()}
    from .auth import resolve_user
    from .db.repo import Repo
    async with db_session() as session:
        user = await resolve_user(Repo(session), authorization=authorization)
        is_new = await link_in_session(session, user_id=int(user.id), instance_id=iid)
    logger.info("[mytracker] install linked new=%s S2S_enabled=%s", is_new, enabled())
    return {"ok": True, "linked": is_new, "tracking_enabled": enabled()}


@router.get("/mt-status", dependencies=[Depends(require_admin_token)])
async def mt_status() -> dict:
    async with db_session() as session:
        pending = await session.scalar(select(func.count()).select_from(MytrackerEvent).where(MytrackerEvent.sent_at.is_(None)))
        accepted = await session.scalar(select(func.count()).select_from(MytrackerEvent).where(MytrackerEvent.sent_at.is_not(None)))
        retrying = await session.scalar(select(func.count()).select_from(MytrackerEvent).where(MytrackerEvent.sent_at.is_(None), MytrackerEvent.attempts > 0))
        linked = await session.scalar(select(func.count()).select_from(MytrackerInstall))
    return {"tracking_enabled": enabled(), "app_id": _app_id(), "linked_installs": linked,
            "pending": pending, "accepted": accepted, "retrying": retrying}
