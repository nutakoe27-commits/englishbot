"""
auth.py — веб-авторизация (миграция 0020).

Содержит:
  - JWT-сессии (HS256, секрет AUTH_JWT_SECRET): issue_jwt / verify_jwt.
  - Валидация Telegram Login Widget (отличается от Mini App initData!).
  - Проверка Google ID-token (через Google JWKS, aud = GOOGLE_CLIENT_ID).
  - resolve_user — единый резолвер: принимает Bearer JWT ИЛИ legacy initData.

Mini App продолжает слать initData (back-compat), веб — Bearer JWT.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from typing import Optional

import jwt  # PyJWT
from fastapi import HTTPException, status

from .config import settings

logger = logging.getLogger(__name__)

_GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}

# Login Widget / initData считаем устаревшими через сутки (анти-replay).
_AUTH_TTL_SEC = 24 * 3600

# Кешируемый клиент JWKS для Google (сам кеширует ключи между вызовами).
_google_jwks_client: Optional["jwt.PyJWKClient"] = None


# ─── JWT-сессии ──────────────────────────────────────────────────────────────

def issue_jwt(user_id: int) -> str:
    """Подписать JWT сессии для users.id. None, если секрет не задан."""
    if not settings.AUTH_JWT_SECRET:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "auth not configured"
        )
    now = int(time.time())
    payload = {
        "uid": int(user_id),
        "iat": now,
        "exp": now + settings.AUTH_JWT_TTL_DAYS * 24 * 3600,
    }
    return jwt.encode(payload, settings.AUTH_JWT_SECRET, algorithm="HS256")


def verify_jwt(token: str) -> Optional[int]:
    """Вернуть users.id из валидного JWT, иначе None."""
    if not token or not settings.AUTH_JWT_SECRET:
        return None
    try:
        payload = jwt.decode(token, settings.AUTH_JWT_SECRET, algorithms=["HS256"])
        return int(payload["uid"])
    except Exception:
        return None


def _bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


# ─── Telegram Login Widget ────────────────────────────────────────────────────

def validate_telegram_login_widget(data: dict) -> Optional[dict]:
    """Проверка подписи Telegram Login Widget.

    Отличие от Mini App initData: secret = SHA256(bot_token) (а не
    HMAC(key="WebAppData")). data — плоский dict полей виджета (id, first_name,
    username, photo_url, auth_date, hash).
    Возвращает data при успехе, иначе None.
    """
    if not settings.BOT_TOKEN:
        return None
    try:
        received_hash = data.get("hash")
        if not received_hash:
            return None
        pairs = sorted(
            f"{k}={v}" for k, v in data.items() if k != "hash"
        )
        check_string = "\n".join(pairs)
        secret_key = hashlib.sha256(settings.BOT_TOKEN.encode()).digest()
        expected = hmac.new(
            secret_key, check_string.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, str(received_hash)):
            return None
        try:
            auth_date = int(data.get("auth_date") or 0)
        except (TypeError, ValueError):
            auth_date = 0
        if auth_date == 0 or (time.time() - auth_date) > _AUTH_TTL_SEC:
            return None
        return data
    except Exception as exc:
        logger.warning("[auth] login-widget validation error: %s", exc)
        return None


# ─── Google ID-token ──────────────────────────────────────────────────────────

def _verify_google_sync(id_token: str) -> Optional[dict]:
    global _google_jwks_client
    if not settings.GOOGLE_CLIENT_ID:
        return None
    try:
        if _google_jwks_client is None:
            _google_jwks_client = jwt.PyJWKClient(_GOOGLE_CERTS_URL)
        signing_key = _google_jwks_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.GOOGLE_CLIENT_ID,
        )
        if claims.get("iss") not in _GOOGLE_ISSUERS:
            return None
        sub = claims.get("sub")
        if not sub:
            return None
        return {
            "sub": str(sub),
            "email": claims.get("email"),
            "email_verified": bool(claims.get("email_verified")),
            "name": claims.get("name") or claims.get("given_name"),
        }
    except Exception as exc:
        logger.warning("[auth] google id-token verify failed: %s", exc)
        return None


async def verify_google_id_token(id_token: str) -> Optional[dict]:
    """Проверить Google ID-token. Возвращает {sub,email,email_verified,name}."""
    if not id_token:
        return None
    # PyJWKClient делает сетевой запрос — уводим в threadpool.
    return await asyncio.to_thread(_verify_google_sync, id_token)


def auth_key(
    authorization: Optional[str] = None,
    init_data: Optional[str] = None,
    token: Optional[str] = None,
) -> int:
    """Лёгкий идентификатор для rate-limit/проверки авторизации без обращения
    к БД: uid из JWT либо tg_id из initData. 401, если ничего."""
    jwt_token = token or _bearer_token(authorization)
    if jwt_token:
        uid = verify_jwt(jwt_token)
        if uid is not None:
            return uid
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    if init_data:
        from .main import _tg_id_from_init_data
        return _tg_id_from_init_data(init_data)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "auth required")


# ─── Единый резолвер пользователя ─────────────────────────────────────────────

async def resolve_user(
    repo,
    *,
    authorization: Optional[str] = None,
    init_data: Optional[str] = None,
    token: Optional[str] = None,
):
    """Вернуть User по Bearer JWT (веб) ИЛИ по Telegram initData (Mini App).

    Порядок: JWT (header или явный token) → users.id; иначе initData → tg_id
    (find-or-create). Кидает HTTPException(401), если ничего не подошло.
    """
    jwt_token = token or _bearer_token(authorization)
    if jwt_token:
        uid = verify_jwt(jwt_token)
        if uid is not None:
            user = await repo.get_user_by_id(uid)
            if user is not None:
                return user
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")

    if init_data:
        # Переиспользуем initData-валидатор из main (Mini App-подпись).
        from .main import _tg_id_from_init_data
        tg_id = _tg_id_from_init_data(init_data)
        user = await repo.get_user_by_tg_id(tg_id)
        if user is None:
            user = await repo.upsert_user(tg_id=tg_id)
        return user

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "auth required")
