"""
internal_routes.py — эндпоинты bot ↔ backend.

Доступ только из docker-сети, защищён общим секретом BACKEND_BOT_SECRET в
заголовке X-Bot-Secret. Никогда не выставляется наружу через nginx (см.
docker/nginx/vps-site/api-english.conf — только /api/v1, /api/auth, /api/admin
и т.п. в публичном vhost).
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from . import auth as auth_lib
from .config import settings
from .db import db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal", tags=["Internal"])


def _check_bot_secret(x_bot_secret: Optional[str]) -> None:
    if not settings.BACKEND_BOT_SECRET:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "BACKEND_BOT_SECRET not configured"
        )
    if not x_bot_secret or x_bot_secret != settings.BACKEND_BOT_SECRET:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_secret")


class _ApplyTelegramIn(BaseModel):
    token: str
    tg_id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    language_code: Optional[str] = None


class _CancelIn(BaseModel):
    token: str


class _ApplyUnlinkNativeIn(BaseModel):
    token: str
    tg_id: int                     # tg_id того, кто нажал кнопку в боте


@router.post("/auth/apply-telegram")
async def apply_telegram(
    body: _ApplyTelegramIn,
    x_bot_secret: Optional[str] = Header(None, alias="X-Bot-Secret"),
) -> dict:
    """Бот вызывает после /start <token>. Применяет login или link/merge.

    Возвращает {kind, resulting_user_id, merged?}. kind = 'login'|'link'.
    """
    _check_bot_secret(x_bot_secret)
    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        action = await repo.get_pending_action(body.token)
        if action is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "action_invalid")
        if action.action == "login_telegram":
            user = await repo.upsert_user(
                tg_id=body.tg_id,
                first_name=body.first_name,
                last_name=body.last_name,
                username=body.username,
                language_code=body.language_code,
            )
            await repo.mark_action_done(body.token, resulting_user_id=user.id)
            await session.commit()
            return {"kind": "login", "resulting_user_id": int(user.id)}
        if action.action == "link_telegram":
            if action.user_id is None:
                await repo.mark_action_failed(body.token)
                await session.commit()
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "no_initiator")
            res = await repo.link_or_merge(
                action.user_id, "telegram", str(body.tg_id), None,
            )
            if res["kind"] == "conflict":
                # У обоих аккаунтов есть identity одного провайдера — нельзя
                # молча слить. Помечаем action failed, возвращаем 409.
                await repo.mark_action_failed(body.token)
                await session.commit()
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={
                        "error": "identity_conflict",
                        "providers": res["conflict_providers"],
                    },
                )
            primary_id = int(res["primary_id"])
            merged = res["kind"] == "merged"
            # У primary мог быть NULL tg_id — у TG-аккаунта он точно есть после
            # merge/link, проставим страховочно.
            primary = await repo.get_user_by_id(primary_id)
            if primary is not None and primary.tg_id is None:
                from sqlalchemy import update as _upd
                from .db.models import User as _U
                from .db.repo import utcnow as _now
                try:
                    await repo.s.execute(
                        _upd(_U).where(_U.id == primary_id).values(
                            tg_id=int(body.tg_id), updated_at=_now(),
                        )
                    )
                except Exception:
                    pass
            await repo.mark_action_done(body.token, resulting_user_id=primary_id)
            await session.commit()
            return {
                "kind": "link",
                "resulting_user_id": primary_id,
                "merged": merged,
            }
        # Неподдерживаемый action для этого эндпоинта (например unlink_native)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unsupported_action")


@router.post("/auth/apply-unlink-native")
async def apply_unlink_native(
    body: _ApplyUnlinkNativeIn,
    x_bot_secret: Optional[str] = Header(None, alias="X-Bot-Secret"),
) -> dict:
    """Бот зовёт после «Подтвердить» в чате. Проверяет, что нажавший — тот же
    юзер, что инициировал (по action.user_id ↔ users.tg_id). Удаляет
    native-identity и обнуляет password_hash."""
    _check_bot_secret(x_bot_secret)
    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        action = await repo.get_pending_action(body.token)
        if action is None or action.action != "unlink_native" or action.user_id is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "action_invalid")
        user = await repo.get_user_by_id(action.user_id)
        if user is None or user.tg_id is None or int(user.tg_id) != int(body.tg_id):
            # Не тот юзер нажал — отказ.
            await repo.mark_action_failed(body.token)
            await session.commit()
            raise HTTPException(status.HTTP_403_FORBIDDEN, "wrong_user")
        ok = await repo.delete_native_identity(action.user_id)
        if not ok:
            await repo.mark_action_failed(body.token)
            await session.commit()
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "no_telegram_left")
        await repo.mark_action_done(body.token, resulting_user_id=action.user_id)
        await session.commit()
    return {"ok": True}


@router.post("/auth/cancel")
async def apply_cancel(
    body: _CancelIn,
    x_bot_secret: Optional[str] = Header(None, alias="X-Bot-Secret"),
) -> dict:
    """Бот вызывает на «Отмена» — помечаем cancelled."""
    _check_bot_secret(x_bot_secret)
    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        action = await repo.get_action(body.token)
        if action is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "action_invalid")
        if action.status != "pending":
            return {"ok": True, "already": action.status}
        await repo.mark_action_cancelled(body.token)
        await session.commit()
    return {"ok": True}


# ─── B2B: подключение ученика к школе из бота (/start school_<code>) ──────

class _OrgJoinIn(BaseModel):
    invite_code: str
    tg_id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    language_code: Optional[str] = None


@router.post("/org/join")
async def org_join(
    body: _OrgJoinIn,
    x_bot_secret: Optional[str] = Header(None, alias="X-Bot-Secret"),
) -> dict:
    """Бот вызывает после /start school_<invite_code>: find-or-create юзера
    по tg_id и подключает к школе. {status: ok|already|no_seats|invalid,
    org_name}."""
    _check_bot_secret(x_bot_secret)
    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await repo.upsert_user(
            tg_id=body.tg_id,
            first_name=body.first_name,
            last_name=body.last_name,
            username=body.username,
            language_code=body.language_code,
        )
        status_str, org = await repo.join_org(body.invite_code, user.id)
        await session.commit()
    if status_str == "no_seats":
        # Владелец узнаёт о нехватке мест сразу — повод расширить пакет.
        # Импорт внутри функции — main импортирует этот модуль на старте.
        from .main import notify_admins_org_no_seats
        notify_admins_org_no_seats(org, user)
    return {
        "status": status_str,
        "org_name": getattr(org, "name", None),
    }
