"""Публичное API веб-пушей: получить ключ, подписаться, отписаться.

Подписаться можно и без входа — например, с лендинга теста уровня, куда
человек приходит до всякой регистрации. Если он залогинен, подписка сразу
привязывается к аккаунту; если войдёт позже, привязка проставится при
следующем вызове /subscribe (клиент шлёт её при каждом запуске).

Разослать что-либо через эти эндпоинты нельзя — отправка живёт только в
админке под X-Admin-Token.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from . import push
from .config import settings
from .db import db_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/push", tags=["Push"])


class _Keys(BaseModel):
    p256dh: str = Field(min_length=8, max_length=255)
    auth: str = Field(min_length=4, max_length=255)


class _SubscribeIn(BaseModel):
    endpoint: str = Field(min_length=8, max_length=512)
    keys: _Keys
    init_data: Optional[str] = None
    source: str = "web"


class _UnsubscribeIn(BaseModel):
    endpoint: str = Field(min_length=8, max_length=512)


async def _maybe_user(init_data: Optional[str], authorization: Optional[str]) -> Optional[int]:
    """user_id, если человек залогинен. Аноним — это нормально, не ошибка."""
    if not settings.DATABASE_URL:
        return None
    a = (authorization or "").strip()
    if not init_data and not a.lower().startswith("bearer "):
        return None
    from .auth import resolve_user
    from .db import Repo
    try:
        async with db_session() as session:
            user = await resolve_user(
                Repo(session), authorization=authorization, init_data=init_data or "",
            )
            return int(user.id)
    except Exception:
        # Протухший токен не должен мешать подписаться — просто останется
        # анонимной, а привяжется при следующем заходе.
        return None


@router.get("/key")
async def push_key() -> dict:
    """Публичный VAPID-ключ для подписки в браузере."""
    return {"enabled": push.is_configured(), "public_key": push.public_key()}


@router.post("/subscribe")
async def subscribe(
    request: Request,
    body: _SubscribeIn,
    authorization: Optional[str] = Header(None),
) -> dict:
    if not push.is_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "push_not_configured")
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db_not_configured")
    user_id = await _maybe_user(body.init_data, authorization)
    ua = request.headers.get("user-agent")
    from .db import Repo
    async with db_session() as session:
        await Repo(session).save_push_subscription(
            endpoint=body.endpoint, p256dh=body.keys.p256dh, auth=body.keys.auth,
            user_id=user_id, user_agent=ua, source=body.source,
        )
        await session.commit()
    return {"ok": True, "linked": user_id is not None}


@router.post("/unsubscribe")
async def unsubscribe(body: _UnsubscribeIn) -> dict:
    """Человек отключил уведомления. Endpoint — секрет его браузера, так что
    подтверждать личность не требуется: чужой отписать нельзя, не зная его."""
    if not settings.DATABASE_URL:
        return {"ok": True}
    from .db import Repo
    async with db_session() as session:
        await Repo(session).delete_push_subscription(body.endpoint)
        await session.commit()
    return {"ok": True}
