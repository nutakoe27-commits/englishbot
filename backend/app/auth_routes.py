"""
auth_routes.py — REST-эндпоинты авторизации (/api/auth/*).

Логика подписи/проверки — в auth.py. Здесь только HTTP-обвязка: вход через
Telegram (initData или Login Widget), /me, привязка/отвязка.

Google/Apple убраны (миграция 0021): иностранный OAuth запрещён в РФ.
Нативная регистрация (email+password) — PR-2 этой серии.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, status
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


class _LinkIn(BaseModel):
    provider: str                          # сейчас только telegram
    init_data: Optional[str] = None
    widget: Optional[dict] = None


class _UnlinkIn(BaseModel):
    provider: str


class _RegisterIn(BaseModel):
    email: str
    password: str
    first_name: Optional[str] = None


class _LoginIn(BaseModel):
    email: str
    password: str


class _SetPasswordIn(BaseModel):
    email: Optional[str] = None    # для TG-юзеров, у которых email ещё не задан
    password: str


class _TgStartIn(BaseModel):
    mode: str                       # 'login' | 'link'


class _YandexStartIn(BaseModel):
    mode: str                       # 'login' | 'link'


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


@router.post("/telegram/start")
async def auth_telegram_start(
    body: _TgStartIn, authorization: Optional[str] = Header(None),
) -> dict:
    """Старт deep-link авторизации/привязки через Telegram-бот.

    Возвращает {token, url}. Сайт сохраняет токен, открывает url (это
    `t.me/<bot>?start=<prefix>_<token>` — telegram приложение откроет бот);
    далее опрашивает /api/auth/poll до status=done и получает JWT.
    """
    _require_db()
    mode = (body.mode or "").strip().lower()
    if mode not in ("login", "link"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_mode")

    user_id: Optional[int] = None
    action_name: str
    prefix: str
    if mode == "login":
        action_name = "login_telegram"
        prefix = "login"
    else:
        # Привязка требует Bearer JWT.
        from .db import Repo
        async with db_session() as session:
            repo = Repo(session)
            user = await auth_lib.resolve_user(repo, authorization=authorization)
            user_id = user.id
        action_name = "link_telegram"
        prefix = "link"

    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        token = await repo.create_auth_action(action_name, user_id=user_id, ttl_sec=600)
        await session.commit()
    return {"token": token, "url": auth_lib.telegram_deeplink(prefix, token)}


@router.get("/poll")
async def auth_poll(token: str = "") -> dict:
    """Состояние действия. status: pending | done | cancelled | failed | expired.

    Для done на login/link выдаёт JWT (поле token). Для unlink_native — без
    токена (юзер уже залогинен, просто покажем сообщение).
    """
    _require_db()
    if not token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "token required")
    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        action = await repo.get_action(token)
        if action is None:
            return {"status": "failed"}
        # Просрочка
        from datetime import datetime as _dt
        now = _dt.utcnow()
        if action.status == "pending" and action.expires_at and action.expires_at <= now:
            return {"status": "expired"}
        out: dict = {"status": action.status, "action": action.action}
        if action.status == "done" and action.resulting_user_id and action.action in (
            "login_telegram", "link_telegram",
        ):
            out["token"] = auth_lib.issue_jwt(action.resulting_user_id)
    return out


@router.post("/yandex/start")
async def auth_yandex_start(
    body: _YandexStartIn, authorization: Optional[str] = Header(None),
) -> dict:
    """Старт OAuth-флоу через Яндекс ID (PR-7).

    Создаёт auth_action, возвращает {token, url}. Сайт делает
    `window.location.href = url` — юзер уходит на oauth.yandex.ru, после
    авторизации Яндекс редиректит на /api/auth/yandex/callback, тот по state
    находит action и завершает вход.
    """
    _require_db()
    if not settings.YANDEX_CLIENT_ID or not settings.YANDEX_CLIENT_SECRET:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "yandex_oauth_not_configured"
        )
    mode = (body.mode or "").strip().lower()
    if mode not in ("login", "link"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_mode")

    user_id: Optional[int] = None
    action_name: str
    if mode == "login":
        action_name = "login_yandex"
    else:
        from .db import Repo
        async with db_session() as session:
            repo = Repo(session)
            user = await auth_lib.resolve_user(repo, authorization=authorization)
            user_id = user.id
        action_name = "link_yandex"

    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        token = await repo.create_auth_action(action_name, user_id=user_id, ttl_sec=600)
        await session.commit()
    return {"token": token, "url": auth_lib.yandex_authorize_url(token)}


def _yandex_redirect_to_frontend(**params: str) -> RedirectResponse:
    """Сборка ответного редиректа на фронт с параметрами в URL fragment.

    Fragment не уходит в access-логи nginx/backend — безопасно отдавать JWT.
    """
    from urllib.parse import urlencode
    base = (settings.MINIAPP_URL or "").rstrip("/") or "/"
    frag = urlencode({k: v for k, v in params.items() if v is not None})
    return RedirectResponse(url=f"{base}/#{frag}", status_code=302)


@router.get("/yandex/callback")
async def auth_yandex_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
) -> RedirectResponse:
    """Колбэк от Яндекса. Обмен code → user info → upsert/link_or_merge → JWT.

    Результат отдаётся фронту через URL fragment (#yandex_jwt=…&mode=…).
    """
    _require_db()
    if error or not code or not state:
        return _yandex_redirect_to_frontend(yandex_error=error or "bad_callback")

    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        action = await repo.get_pending_action(state)
        if action is None or action.action not in ("login_yandex", "link_yandex"):
            return _yandex_redirect_to_frontend(yandex_error="state_invalid")

        token_payload = await auth_lib.exchange_yandex_code(code)
        if not token_payload or "access_token" not in token_payload:
            await repo.mark_action_failed(state)
            await session.commit()
            return _yandex_redirect_to_frontend(yandex_error="exchange_failed")

        userinfo = await auth_lib.fetch_yandex_userinfo(token_payload["access_token"])
        if not userinfo or "id" not in userinfo:
            await repo.mark_action_failed(state)
            await session.commit()
            return _yandex_redirect_to_frontend(yandex_error="userinfo_failed")

        yandex_uid = str(userinfo["id"])
        email = userinfo.get("default_email") or None

        merged = False
        if action.action == "login_yandex":
            existing = await repo.get_user_by_identity("yandex", yandex_uid)
            if existing is not None:
                resulting_user_id = existing.id
            else:
                user = await repo.create_user_with_identity(
                    provider="yandex",
                    provider_uid=yandex_uid,
                    email=email,
                    first_name=userinfo.get("first_name"),
                    last_name=userinfo.get("last_name"),
                )
                resulting_user_id = user.id
        else:
            if action.user_id is None:
                await repo.mark_action_failed(state)
                await session.commit()
                return _yandex_redirect_to_frontend(yandex_error="no_initiator")
            res = await repo.link_or_merge(
                action.user_id, "yandex", yandex_uid, email,
            )
            resulting_user_id = int(res["primary_id"])
            merged = res["kind"] == "merged"

        await repo.mark_action_done(state, resulting_user_id=resulting_user_id)
        await session.commit()

    jwt = auth_lib.issue_jwt(resulting_user_id)
    mode = "login" if action.action == "login_yandex" else "link"
    params: dict = {"yandex_jwt": jwt, "mode": mode}
    if merged:
        params["merged"] = "1"
    return _yandex_redirect_to_frontend(**params)


@router.post("/register")
async def auth_register(body: _RegisterIn) -> dict:
    """Регистрация по email+password. Без верификации email (компромисс)."""
    _require_db()
    email = auth_lib.normalize_email(body.email)
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_email")
    if not body.password or len(body.password) < auth_lib.PASSWORD_MIN_LEN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "weak_password")

    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        # Уже занят native-логин (по identity)?
        existing = await repo.get_user_by_identity("native", email)
        if existing is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "email_taken")
        # Или email лежит на чужом аккаунте без native-identity?
        by_email = await repo.get_user_by_email(email)
        if by_email is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "email_taken")
        password_hash = auth_lib.hash_password(body.password)
        user = await repo.create_native_user(
            email=email,
            password_hash=password_hash,
            first_name=(body.first_name or "").strip() or None,
        )
        identities = await repo.list_identities(user.id)
        await session.commit()
        token = auth_lib.issue_jwt(user.id)
        return {"token": token, "user": _user_summary(user, identities)}


@router.post("/login")
async def auth_login(body: _LoginIn) -> dict:
    """Вход по email+password. На неуспех — 401 без подсказок."""
    _require_db()
    email = auth_lib.normalize_email(body.email)
    if not email or not body.password:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_credentials")

    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await repo.get_user_by_identity("native", email)
        if user is None or not auth_lib.verify_password(body.password, user.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_credentials")
        identities = await repo.list_identities(user.id)
        token = auth_lib.issue_jwt(user.id)
        return {"token": token, "user": _user_summary(user, identities)}


@router.post("/set-password")
async def auth_set_password(
    body: _SetPasswordIn, authorization: Optional[str] = Header(None),
) -> dict:
    """Задать пароль (и опц. email) текущему юзеру — например, TG-юзер хочет
    добавить email-вход. Без проверки старого пароля (нет email-верификации)."""
    _require_db()
    if not body.password or len(body.password) < auth_lib.PASSWORD_MIN_LEN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "weak_password")

    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await auth_lib.resolve_user(repo, authorization=authorization)

        # Если email передали — нормализуем и проверяем уникальность.
        if body.email:
            new_email = auth_lib.normalize_email(body.email)
            if not new_email:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_email")
            res = await repo.set_email(user.id, new_email)
            if res == "taken":
                await session.rollback()
                raise HTTPException(status.HTTP_409_CONFLICT, "email_taken")
        elif not user.email:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "email_required")

        await repo.set_password(user.id, auth_lib.hash_password(body.password))
        # set_password создаёт native-identity по email юзера.
        identities = await repo.list_identities(user.id)
        await session.commit()
    return {"ok": True, "identities": [
        {"provider": i["provider"], "email": i.get("email")} for i in identities
    ]}


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
    """Привязать провайдер к текущему аккаунту (Bearer JWT).

    Пока поддерживается только telegram (Google убран миграцией 0021,
    нативная регистрация — PR-2 этой серии, VK ID — позже).
    """
    _require_db()
    if body.provider != "telegram":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_provider")
    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await auth_lib.resolve_user(repo, authorization=authorization)

        fields = _telegram_fields(body.init_data, body.widget)
        uid, email = str(fields["tg_id"]), None

        # link_or_merge: если provider свободен — просто прилинкуем; если занят
        # другим аккаунтом — сольём (primary = старший по created_at).
        res = await repo.link_or_merge(user.id, "telegram", uid, email)
        primary_id = int(res["primary_id"])
        merged = res["kind"] == "merged"

        # Если в результате слияния primary НЕ имел tg_id, добавим вручную
        # (для linked-ветки тоже — у нативного веб-аккаунта tg_id NULL).
        # Делаем это аккуратно: если занят другим — не трогаем.
        primary = await repo.get_user_by_id(primary_id)
        if primary is not None and primary.tg_id is None:
            from sqlalchemy import update as _upd
            from .db.models import User as _U
            from .db.repo import utcnow as _now
            try:
                await repo.s.execute(
                    _upd(_U).where(_U.id == primary_id).values(
                        tg_id=int(uid), updated_at=_now(),
                    )
                )
            except Exception:
                pass

        identities = await repo.list_identities(primary_id)
        await session.commit()

        # Если был merge и юзер сейчас работает под secondary'ным JWT —
        # ему нужен новый JWT на primary, чтобы дальше всё работало.
        new_token = (
            auth_lib.issue_jwt(primary_id) if primary_id != user.id else None
        )
    return {
        "ok": True,
        "merged": merged,
        "primary_id": primary_id,
        "token": new_token,
        "identities": [
            {"provider": i["provider"], "email": i.get("email")} for i in identities
        ],
    }


@router.post("/unlink")
async def auth_unlink(body: _UnlinkIn, authorization: Optional[str] = Header(None)) -> dict:
    """Отвязать провайдер (нельзя удалить последний способ входа).

    Для native действует особое правило: только через /unlink/request →
    подтверждение в боте (см. ниже). Здесь native запрещён.
    """
    _require_db()
    if body.provider == "native":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "use_unlink_request_for_native"
        )
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


@router.post("/unlink/request")
async def auth_unlink_request(
    body: _UnlinkIn, authorization: Optional[str] = Header(None),
) -> dict:
    """Запросить отвязку native (email/password). Требует Telegram-привязки.

    Backend генерит action-токен, шлёт в Telegram чат сообщение с inline-
    кнопками. Подтверждение происходит в боте, не на сайте.
    """
    _require_db()
    if body.provider != "native":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "only_native_supported")

    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await auth_lib.resolve_user(repo, authorization=authorization)

        # Должен быть native (что отвязывать) и Telegram (что подтверждать).
        identities = await repo.list_identities(user.id)
        providers = {i["provider"] for i in identities}
        if "native" not in providers:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "no_native")
        if "telegram" not in providers or user.tg_id is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "no_telegram")

        token = await repo.create_auth_action(
            "unlink_native", user_id=user.id, ttl_sec=600,
        )
        await session.commit()
        tg_chat_id = int(user.tg_id)

    # Сообщение в TG. Inline-кнопки confirm/cancel. callback_data <64 байт
    # (наш токен — base64url 32 chars).
    text = (
        "🔒 <b>Подтверди отвязку входа по email</b>\n\n"
        "Ты или кто-то от твоего имени просит снять привязку email и "
        "пароля от аккаунта English Tutor. После отвязки войти на сайте по "
        "email/паролю будет нельзя — останется только Telegram.\n\n"
        "Если это не ты — нажми «Отмена»."
    )
    markup = {
        "inline_keyboard": [[
            {"text": "✅ Подтвердить", "callback_data": f"cu:{token}"},
            {"text": "❌ Отмена", "callback_data": f"cn:{token}"},
        ]],
    }
    sent = await auth_lib.send_bot_message(tg_chat_id, text, reply_markup=markup)
    if not sent:
        # Не смогли уведомить — отменяем action, чтобы он не висел.
        from .db import Repo
        async with db_session() as session:
            repo = Repo(session)
            await repo.mark_action_cancelled(token)
            await session.commit()
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "telegram_send_failed",
        )
    return {"ok": True}
