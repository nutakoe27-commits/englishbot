"""
auth.py — веб-авторизация.

Содержит:
  - JWT-сессии (HS256, секрет AUTH_JWT_SECRET): issue_jwt / verify_jwt.
  - Валидация Telegram Login Widget (отличается от Mini App initData!).
  - resolve_user — единый резолвер: принимает Bearer JWT ИЛИ legacy initData.

Mini App продолжает слать initData (back-compat), веб — Bearer JWT.

Google/Apple убраны (миграция 0021): иностранный OAuth запрещён в РФ.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from typing import Optional

import jwt  # PyJWT
from argon2 import PasswordHasher
from argon2 import exceptions as _argon2_exc
from fastapi import HTTPException, status

from .config import settings

logger = logging.getLogger(__name__)

# Один экземпляр argon2id-хешера на процесс (потокобезопасный).
_PWD_HASHER = PasswordHasher()

# Простая email-валидация (полная RFC 5322 не нужна).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Минимальная длина пароля. Без верификации email это компромисс — но 8
# символов лучше чем 6 (рекомендация OWASP для непрофилактических сервисов).
PASSWORD_MIN_LEN = 8


def normalize_email(raw: str) -> Optional[str]:
    """Тримим/lowercase email; None если формат неверный."""
    if not raw:
        return None
    e = raw.strip().lower()
    return e if _EMAIL_RE.match(e) else None


def hash_password(plain: str) -> str:
    return _PWD_HASHER.hash(plain)


def verify_password(plain: str, hashed: Optional[str]) -> bool:
    """Сравнивает пароль с хешем. False на любую ошибку (тайминг важен меньше,
    чем простота)."""
    if not hashed:
        return False
    try:
        _PWD_HASHER.verify(hashed, plain)
        return True
    except (_argon2_exc.VerifyMismatchError, _argon2_exc.InvalidHash, _argon2_exc.VerificationError):
        return False
    except Exception:
        return False

# Login Widget / initData считаем устаревшими через сутки (анти-replay).
_AUTH_TTL_SEC = 24 * 3600


# ─── JWT-сессии ──────────────────────────────────────────────────────────────

def issue_jwt(user_id: int) -> str:
    """Подписать JWT сессии для users.id."""
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


def telegram_deeplink(prefix: str, token: str) -> str:
    """Сборка t.me/<bot>?start=<prefix>_<token>.

    `prefix` — короткий маркер для бот-хендлера: 'login' | 'link' | 'auth'.
    BOT_USERNAME без '@'.
    """
    bot = (settings.BOT_USERNAME or "").lstrip("@")
    return f"https://t.me/{bot}?start={prefix}_{token}"


async def send_bot_message(
    chat_id: int, text: str, reply_markup: Optional[dict] = None,
) -> bool:
    """Отправить сообщение в Telegram чат от имени нашего бота.

    Используется для уведомлений (PR-6: подтверждение unlink). Возвращает
    True/False — успех. Логирует ошибку без исключений.
    """
    if not settings.BOT_TOKEN:
        return False
    import httpx
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    url = f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            logger.warning(
                "[auth] sendMessage failed %s: %s", resp.status_code, resp.text[:300]
            )
            return False
        return True
    except Exception as exc:
        logger.warning("[auth] sendMessage exception: %s", exc)
        return False


# ─── Яндекс ID (PR-7) ────────────────────────────────────────────────────
def _yandex_redirect_uri() -> str:
    """Redirect URI для Яндекс OAuth. Из env YANDEX_REDIRECT_URI или
    из API_PUBLIC_URL + '/api/auth/yandex/callback'."""
    if settings.YANDEX_REDIRECT_URI:
        return settings.YANDEX_REDIRECT_URI
    base = (settings.API_PUBLIC_URL or "").rstrip("/")
    if not base:
        raise RuntimeError("API_PUBLIC_URL or YANDEX_REDIRECT_URI must be set")
    return f"{base}/api/auth/yandex/callback"


def yandex_authorize_url(state: str) -> str:
    """Собирает URL для редиректа юзера на oauth.yandex.ru/authorize."""
    from urllib.parse import urlencode
    params = {
        "response_type": "code",
        "client_id": settings.YANDEX_CLIENT_ID or "",
        "redirect_uri": _yandex_redirect_uri(),
        "state": state,
        "force_confirm": "yes",
    }
    return "https://oauth.yandex.ru/authorize?" + urlencode(params)


async def exchange_yandex_code(code: str) -> Optional[dict]:
    """Обмен authorization_code на access_token. Возвращает payload или None."""
    if not settings.YANDEX_CLIENT_ID or not settings.YANDEX_CLIENT_SECRET:
        logger.warning("[auth] yandex code-exchange skipped: client_id/secret not set")
        return None
    import httpx
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings.YANDEX_CLIENT_ID,
        "client_secret": settings.YANDEX_CLIENT_SECRET,
        "redirect_uri": _yandex_redirect_uri(),
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://oauth.yandex.ru/token",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code != 200:
            logger.warning(
                "[auth] yandex token exchange failed %s: %s",
                resp.status_code, resp.text[:300],
            )
            return None
        return resp.json()
    except Exception as exc:
        logger.warning("[auth] yandex token exchange exception: %s", exc)
        return None


async def fetch_yandex_userinfo(access_token: str) -> Optional[dict]:
    """Запрос user info: id, default_email, real_name, login."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://login.yandex.ru/info",
                params={"format": "json"},
                headers={"Authorization": f"OAuth {access_token}"},
            )
        if resp.status_code != 200:
            logger.warning(
                "[auth] yandex userinfo failed %s: %s",
                resp.status_code, resp.text[:300],
            )
            return None
        return resp.json()
    except Exception as exc:
        logger.warning("[auth] yandex userinfo exception: %s", exc)
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
