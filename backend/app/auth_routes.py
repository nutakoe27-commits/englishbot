"""
auth_routes.py — REST-эндпоинты авторизации (/api/auth/*).

Логика подписи/проверки — в auth.py. Здесь только HTTP-обвязка:
вход через Telegram (initData или Login Widget) и Google, /me, привязка/отвязка.
"""

from __future__ import annotations

import logging
from typing import Optional

from urllib.parse import urlencode

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import auth as auth_lib
from .config import settings
from .db import db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Auth"])


# ─── Schemas ─────────────────────────────────────────────────────────────────

class _TelegramIn(BaseModel):
    init_data: Optional[str] = None        # Mini App
    widget: Optional[dict] = None          # Login Widget (плоский dict полей)


class _GoogleIn(BaseModel):
    id_token: str


class _LinkIn(BaseModel):
    provider: str                          # telegram | google
    init_data: Optional[str] = None
    widget: Optional[dict] = None
    id_token: Optional[str] = None


class _UnlinkIn(BaseModel):
    provider: str


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _user_summary(user, identities: list[dict]) -> dict:
    return {
        "id": int(user.id),
        "tg_id": int(user.tg_id) if user.tg_id is not None else None,
        "first_name": user.first_name,
        "username": user.username,
        "email": user.email,
        "identities": [
            {"provider": i["provider"], "email": i.get("email")} for i in identities
        ],
    }


def _require_db() -> None:
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db not configured")


def _telegram_fields(body_init_data: Optional[str], widget: Optional[dict]) -> dict:
    """Из initData или widget вернуть {tg_id, first_name, username, language_code}.
    Кидает 401 при невалидной подписи."""
    if body_init_data:
        from .main import validate_telegram_init_data
        validated = validate_telegram_init_data(body_init_data, settings.BOT_TOKEN or "")
        if not validated or not validated.get("user"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid initData")
        import json
        u = json.loads(validated["user"])
        return {
            "tg_id": int(u["id"]),
            "first_name": u.get("first_name"),
            "username": u.get("username"),
            "language_code": u.get("language_code"),
        }
    if widget:
        validated = auth_lib.validate_telegram_login_widget(widget)
        if not validated:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid widget")
        return {
            "tg_id": int(validated["id"]),
            "first_name": validated.get("first_name"),
            "username": validated.get("username"),
            "language_code": None,
        }
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "init_data or widget required")


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/telegram")
async def auth_telegram(body: _TelegramIn) -> dict:
    """Вход/регистрация через Telegram (Mini App initData или Login Widget)."""
    _require_db()
    fields = _telegram_fields(body.init_data, body.widget)
    tg_id = fields["tg_id"]
    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await repo.get_user_by_identity("telegram", str(tg_id))
        if user is None:
            user = await repo.get_user_by_tg_id(tg_id)  # legacy без identity
        if user is None:
            user = await repo.create_user_with_identity(
                provider="telegram",
                provider_uid=str(tg_id),
                tg_id=tg_id,
                first_name=fields.get("first_name"),
                username=fields.get("username"),
                language_code=fields.get("language_code"),
            )
        else:
            # гарантируем telegram-identity для legacy-юзеров
            await repo._ensure_identity(user.id, "telegram", str(tg_id), None)
        identities = await repo.list_identities(user.id)
        await session.commit()
        token = auth_lib.issue_jwt(user.id)
        return {"token": token, "user": _user_summary(user, identities)}


@router.post("/google")
async def auth_google(body: _GoogleIn) -> dict:
    """Вход/регистрация через Google (ID-token из Google Identity Services)."""
    _require_db()
    info = await auth_lib.verify_google_id_token(body.id_token)
    if info is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_google_token")
    sub = info["sub"]
    email = info.get("email")
    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await repo.get_user_by_identity("google", sub)
        if user is None:
            # email уже у другого аккаунта → просим войти прежним способом и
            # привязать Google в настройках (без сайлент-мержа).
            if email and await repo.get_user_by_email(email):
                raise HTTPException(status.HTTP_409_CONFLICT, "email_taken")
            user = await repo.create_user_with_identity(
                provider="google",
                provider_uid=sub,
                email=email,
                first_name=info.get("name"),
            )
        identities = await repo.list_identities(user.id)
        await session.commit()
        token = auth_lib.issue_jwt(user.id)
        return {"token": token, "user": _user_summary(user, identities)}


@router.get("/me")
async def auth_me(authorization: Optional[str] = Header(None)) -> dict:
    """Текущий аккаунт + привязки (по Bearer JWT)."""
    _require_db()
    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await auth_lib.resolve_user(repo, authorization=authorization)
        identities = await repo.list_identities(user.id)
        has_sub = await repo.has_active_subscription(user)
    out = _user_summary(user, identities)
    out["has_subscription"] = has_sub
    out["subscription_until"] = (
        user.subscription_until.isoformat() if user.subscription_until else None
    )
    return out


@router.post("/link")
async def auth_link(body: _LinkIn, authorization: Optional[str] = Header(None)) -> dict:
    """Привязать провайдер к текущему аккаунту (Bearer JWT)."""
    _require_db()
    if body.provider not in ("telegram", "google"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_provider")
    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await auth_lib.resolve_user(repo, authorization=authorization)

        if body.provider == "telegram":
            fields = _telegram_fields(body.init_data, body.widget)
            uid, email = str(fields["tg_id"]), None
        else:  # google
            if not body.id_token:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "id_token required")
            info = await auth_lib.verify_google_id_token(body.id_token)
            if info is None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_google_token")
            uid, email = info["sub"], info.get("email")

        # Сначала проверяем занятость identity — иначе для telegram попытка
        # выставить users.tg_id (UNIQUE) упадёт дублем (500), если у этого TG
        # уже есть свой аккаунт.
        result = await repo.link_identity(user.id, body.provider, uid, email)
        if result == "taken":
            await session.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, "taken")

        # Только после успешной привязки telegram проставляем tg_id (если пуст).
        if body.provider == "telegram" and user.tg_id is None:
            from sqlalchemy import update as _upd
            from .db.models import User as _U
            from .db.repo import utcnow as _now
            await repo.s.execute(
                _upd(_U).where(_U.id == user.id).values(
                    tg_id=int(uid), updated_at=_now(),
                )
            )

        identities = await repo.list_identities(user.id)
        await session.commit()
    return {"ok": True, "identities": [
        {"provider": i["provider"], "email": i.get("email")} for i in identities
    ]}


@router.post("/unlink")
async def auth_unlink(body: _UnlinkIn, authorization: Optional[str] = Header(None)) -> dict:
    """Отвязать провайдер (нельзя удалить последний способ входа)."""
    _require_db()
    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await auth_lib.resolve_user(repo, authorization=authorization)
        ok = await repo.unlink_identity(user.id, body.provider)
        if not ok:
            await session.rollback()
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "last_identity")
        identities = await repo.list_identities(user.id)
        await session.commit()
    return {"ok": True, "identities": [
        {"provider": i["provider"], "email": i.get("email")} for i in identities
    ]}


# ─── Google OAuth redirect-флоу (работает в браузере и из Telegram через ───────
# внешний браузер — в отличие от GIS, который webview Telegram блокирует) ───────

def _site_redirect(redirect: Optional[str]) -> str:
    """Куда вернуть браузер после OAuth. Только разрешённые хосты (анти-open-redirect)."""
    allowed = (settings.WEB_APP_URL or settings.MINIAPP_URL or "").rstrip("/")
    if redirect:
        for base in (allowed, "http://localhost:5173", "http://localhost:5174"):
            if base and redirect.startswith(base):
                return redirect.rstrip("/")
    return allowed or "/"


def _api_base(request: Request) -> str:
    if settings.API_PUBLIC_URL:
        return settings.API_PUBLIC_URL.rstrip("/")
    return str(request.base_url).rstrip("/")


@router.get("/google/start")
async def google_start(
    request: Request, redirect: str = "", link_token: str = "",
):
    """Старт Google OAuth: 302 на Google. redirect — куда вернуть сайт;
    link_token (наш JWT) — режим привязки к существующему аккаунту."""
    if not (settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "google not configured")
    redirect_uri = f"{_api_base(request)}/api/auth/google/callback"
    state_payload: dict = {"site": _site_redirect(redirect)}
    if link_token:
        uid = auth_lib.verify_jwt(link_token)
        if uid is not None:
            state_payload["link_uid"] = uid
    state = auth_lib.make_oauth_state(state_payload)
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
        "access_type": "online",
    }
    return RedirectResponse(
        "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params),
        status_code=302,
    )


@router.get("/google/callback")
async def google_callback(
    request: Request, code: str = "", state: str = "", error: str = "",
):
    """Google вернул code → меняем на токен, логиним/привязываем, редирект на сайт
    с #token=… (вход) или #linked=google / #link_error=… (привязка)."""
    st = auth_lib.read_oauth_state(state) or {}
    site = (st.get("site") or settings.WEB_APP_URL or settings.MINIAPP_URL or "/").rstrip("/")
    if error or not code or not st:
        return RedirectResponse(f"{site}/#auth_error=google", status_code=302)

    redirect_uri = f"{_api_base(request)}/api/auth/google/callback"
    info = await auth_lib.exchange_google_code(code, redirect_uri)
    if info is None:
        return RedirectResponse(f"{site}/#auth_error=google", status_code=302)

    sub = info["sub"]
    email = info.get("email")
    from .db import Repo
    link_uid = st.get("link_uid")
    async with db_session() as session:
        repo = Repo(session)
        if link_uid:
            res = await repo.link_identity(int(link_uid), "google", sub, email)
            await session.commit()
            frag = "linked=google" if res == "ok" else "link_error=taken"
            return RedirectResponse(f"{site}/#{frag}", status_code=302)

        user = await repo.get_user_by_identity("google", sub)
        if user is None:
            if email and await repo.get_user_by_email(email):
                return RedirectResponse(f"{site}/#auth_error=email_taken", status_code=302)
            user = await repo.create_user_with_identity(
                provider="google", provider_uid=sub, email=email,
                first_name=info.get("name"),
            )
        await session.commit()
        token = auth_lib.issue_jwt(user.id)
    return RedirectResponse(f"{site}/#token={token}", status_code=302)
