"""
main.py — точка входа FastAPI-приложения.

Эндпоинты:
  GET  /health          — healthcheck для Docker
  GET  /api/v1/ping     — тестовый REST-эндпоинт
  WS   /ws/voice        — голосовой диалог (Whisper STT + vLLM + Kokoro TTS)
"""

import hashlib
import hmac
import json
import asyncio
import logging
import os
import time
import urllib.parse
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .admin import router as admin_router
from .battle_api import router as battle_router
from .config import settings
from .db import db_session, init_db
from .limits import LimitsContext, build_limits_context
from .llm_providers import get_llm_provider, translate_word
from .voice import run_voice_session

# ─── Конфигурация логирования ──────────────────────────────────────────
# Без этого logger.info(...) не виден в stdout docker'а — uvicorn настраивает
# только свои логгеры, а рут-логгер остаётся на WARNING.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ─── Приложение ───────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_: FastAPI):
    # Пытаемся поднять БД. Если DATABASE_URL не задан — работаем без БД
    # (legacy-режим). Интеграция с voice-сессиями и лимитами — в PR C.
    db_ok = init_db()
    logger.info("DB ready=%s", db_ok)
    yield


app = FastAPI(
    title="AI English Tutor — Backend",
    version="0.4.0",
    description="Backend API для Telegram Mini App с AI-репетитором английского.",
    lifespan=lifespan,
)

# ─── CORS ────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.MINIAPP_URL,
        settings.admin_url,
        # Локальная разработка
        "http://localhost:5173",
        "http://localhost:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Утилита: валидация Telegram WebApp initData ─────────────────────────────

def validate_telegram_init_data(init_data_raw: str, bot_token: str) -> Optional[dict]:
    """
    Проверяет подпись Telegram Mini App initData согласно официальной документации.
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

    Возвращает распарсенные данные (dict) если подпись корректна, иначе None.
    """
    try:
        # Разбираем query-строку
        parsed = dict(urllib.parse.parse_qsl(init_data_raw, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            logger.warning("initData не содержит поле hash")
            return None

        # Формируем строку для проверки: отсортированные key=value через \n
        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(parsed.items())
        )

        # Вычисляем HMAC-SHA256
        secret_key = hmac.new(
            b"WebAppData", bot_token.encode(), hashlib.sha256
        ).digest()
        expected_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected_hash, received_hash):
            logger.warning("Подпись initData не совпадает")
            return None

        # Возвращаем данные с восстановленным hash
        parsed["hash"] = received_hash
        return parsed

    except Exception as exc:
        logger.error("Ошибка при валидации initData: %s", exc)
        return None


# ─── Healthcheck ──────────────────────────────────────────────────────────────

app.include_router(admin_router)
app.include_router(battle_router)


@app.get("/health", tags=["System"])
async def health() -> dict:
    """Проверка работоспособности сервиса. Используется Docker healthcheck."""
    return {"status": "ok", "service": "backend"}


# ─── REST API v1 ──────────────────────────────────────────────────────────────

@app.get("/api/v1/ping", tags=["API v1"])
async def ping() -> dict:
    """Тестовый эндпоинт. Проверяет доступность API."""
    return {"pong": True, "version": "0.3.0"}


# ─── Перевод слова по тапу в чате ─────────────────────────────────────────────

_TRANSLATION_CACHE: dict[tuple[str, str], list[str]] = {}
_TRANSLATION_CACHE_MAX = 2000

_RATE_WINDOW_SEC = 60
_RATE_LIMIT_PER_USER = 30
_RATE_BUCKETS: dict[int, deque] = defaultdict(deque)


def _tg_id_from_init_data(init_data_raw: str) -> int:
    """Парсит и валидирует Telegram initData, возвращает tg_id.
    В dev-режиме (если BOT_TOKEN не задан) — берёт user_id без проверки подписи.
    """
    if not init_data_raw:
        raise HTTPException(status_code=401, detail="missing_init_data")

    if settings.BOT_TOKEN:
        validated = validate_telegram_init_data(init_data_raw, settings.BOT_TOKEN)
        if not validated and not settings.is_development:
            raise HTTPException(status_code=401, detail="invalid_init_data")
        parsed = validated or dict(
            urllib.parse.parse_qsl(init_data_raw, keep_blank_values=True)
        )
    else:
        if not settings.is_development:
            raise HTTPException(status_code=500, detail="bot_token_not_configured")
        parsed = dict(urllib.parse.parse_qsl(init_data_raw, keep_blank_values=True))

    user_raw = parsed.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="missing_user")
    try:
        user_obj = json.loads(user_raw)
        return int(user_obj["id"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="bad_user") from exc


def _enforce_rate_limit(tg_id: int) -> None:
    now = time.time()
    bucket = _RATE_BUCKETS[tg_id]
    while bucket and bucket[0] < now - _RATE_WINDOW_SEC:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT_PER_USER:
        raise HTTPException(status_code=429, detail="rate_limited")
    bucket.append(now)


async def _get_translation_cached(word: str, context: str) -> list[str]:
    # Контекст-ключ: хеш первых 80 символов нормализованной строки.
    # Тот же word с похожим контекстом → один кеш-хит.
    ctx_norm = (context or "").lower().strip()[:80]
    ctx_key = hashlib.md5(ctx_norm.encode("utf-8")).hexdigest()[:8]
    key = (word, ctx_key)

    cached = _TRANSLATION_CACHE.get(key)
    if cached is not None:
        return cached

    llm = get_llm_provider()
    translations = await translate_word(llm, word=word, context=context)

    # FIFO-вытеснение при переполнении (грубая аппроксимация LRU — для
    # in-memory кеша на 2000 entries этого достаточно).
    if len(_TRANSLATION_CACHE) >= _TRANSLATION_CACHE_MAX:
        try:
            first_key = next(iter(_TRANSLATION_CACHE))
            _TRANSLATION_CACHE.pop(first_key, None)
        except StopIteration:
            pass
    _TRANSLATION_CACHE[key] = translations
    return translations


class _TranslateIn(BaseModel):
    init_data: str
    word: str = Field(..., min_length=1, max_length=64)
    context: str = Field("", max_length=500)


@app.post("/api/translate", tags=["Translate"])
async def translate(body: _TranslateIn) -> dict:
    """Перевод одного английского слова на русский с учётом контекста реплики.

    Возвращает {word, translations: [primary, alt1, alt2]}. На пустой результат
    от LLM — translations=[].
    """
    tg_id = _tg_id_from_init_data(body.init_data)
    word = body.word.strip().lower()
    if not word:
        raise HTTPException(status_code=400, detail="empty_word")
    _enforce_rate_limit(tg_id)
    translations = await _get_translation_cached(word, body.context)
    return {"word": word, "translations": translations}


# ─── WebSocket — голосовой диалог ─────────────────────────────────────────────

@app.websocket("/ws/voice")
async def ws_voice(websocket: WebSocket, init_data: str = ""):
    """
    WebSocket-эндпоинт для голосового диалога с AI-репетитором.

    Query params:
        init_data — URL-encoded Telegram WebApp initData строка.
                    Обязательна в production-режиме.

    Протокол:
        Входящие бинарные сообщения → PCM 16-bit 16kHz mono (от браузера).
        Исходящие бинарные сообщения → PCM 16-bit 24kHz mono (от TTS).
        Исходящие JSON-сообщения → {"type": "text", "role": "user"|"tutor", "text": "..."}
    """
    # ── Валидация Telegram initData ────────────────────────────────────────
    user_info: Optional[dict] = None

    if init_data:
        if settings.BOT_TOKEN:
            validated = validate_telegram_init_data(init_data, settings.BOT_TOKEN)
            if validated:
                # Извлекаем user-объект для логирования
                user_raw = validated.get("user")
                if user_raw:
                    try:
                        user_info = json.loads(user_raw)
                    except (json.JSONDecodeError, TypeError):
                        pass
                logger.info(
                    "initData валиден, user_id=%s",
                    user_info.get("id") if user_info else "unknown",
                )
            else:
                # В production невалидная подпись — отклоняем
                if not settings.is_development:
                    logger.warning("Невалидная подпись initData — отклоняем соединение")
                    await websocket.close(code=4001, reason="Unauthorized: invalid initData")
                    return
                else:
                    logger.warning("DEV: невалидная подпись initData — пропускаем проверку")
        else:
            logger.warning("BOT_TOKEN не задан — пропускаем валидацию initData")
    else:
        # init_data отсутствует
        if not settings.is_development:
            logger.warning("init_data отсутствует в production — отклоняем соединение")
            await websocket.close(code=4001, reason="Unauthorized: missing initData")
            return
        else:
            logger.info("DEV: init_data отсутствует — пропускаем проверку")

    # ── Принимаем WebSocket-соединение ────────────────────────────────────
    await websocket.accept()
    logger.warning(
        "[WS] /ws/voice принят: client=%s user_id=%s",
        websocket.client,
        user_info.get("id") if user_info else "anonymous",
    )

    # ── Проверка maintenance + лимиты (только если БД поднята) ──────────
    # Важно: весь блок под таймаутом — если MySQL висит/пул исчерпан,
    # лучше пропустить человека без лимитов, чем вечно держать сокет молча.
    limits_ctx: Optional[LimitsContext] = None
    if user_info and settings.DATABASE_URL:
        logger.info("[WS] начинаю проверку maintenance/лимитов")

        async def _check_db_preflight() -> Optional[LimitsContext]:
            async with db_session() as session:
                from .db import Repo
                repo = Repo(session)
                # 1. Maintenance — всех отправляем лесом, кроме админов
                if await repo.get_kv_bool("maintenance_mode", False):
                    if user_info.get("id") not in settings.admin_ids_list:
                        msg = await repo.get_kv(
                            "maintenance_message",
                            "Бот временно недоступен — ведутся технические работы.",
                        )
                        await websocket.send_json(
                            {"type": "maintenance", "message": msg}
                        )
                        await websocket.close(code=4002, reason="Maintenance mode")
                        return None
                # 2. Upsert + лимиты
                return await build_limits_context(
                    repo=repo,
                    repo_factory=db_session,
                    tg_id=int(user_info["id"]),
                    username=user_info.get("username"),
                    first_name=user_info.get("first_name"),
                    last_name=user_info.get("last_name"),
                    language_code=user_info.get("language_code"),
                )

        try:
            # 5 сек на весь preflight — больше не ждём.
            limits_ctx = await asyncio.wait_for(_check_db_preflight(), timeout=5.0)
            if limits_ctx is None and websocket.client_state.name != "CONNECTED":
                # Попали в ветку maintenance и сокет уже закрыт — выходим.
                return
            logger.info(
                "[WS] preflight готов: has_sub=%s blocked=%s remaining=%s",
                getattr(limits_ctx, "has_subscription", None),
                getattr(limits_ctx, "is_blocked", None),
                getattr(limits_ctx, "remaining_seconds", None),
            )
        except asyncio.TimeoutError:
            logger.error(
                "[WS] preflight БД превысил 5 сек — продолжаем без лимитов для user_id=%s",
                user_info.get("id"),
            )
            limits_ctx = None
        except Exception as exc:
            logger.error("Ошибка БД на connect: %s", exc, exc_info=True)
            limits_ctx = None  # fallback: лучше работать без лимитов, чем падать

    if limits_ctx is not None:
        if limits_ctx.is_blocked:
            await websocket.send_json(
                {"type": "blocked", "message": "Ваш аккаунт заблокирован."}
            )
            await websocket.close(code=4003, reason="Account blocked")
            return
        if limits_ctx.is_exceeded():
            snap = limits_ctx.snapshot().to_dict()
            await websocket.send_json({"type": "limit_reached", **snap})
            await websocket.close(code=4004, reason="Daily limit reached")
            return
        # Сообщаем клиенту лимиты — он отрисует таймер
        await websocket.send_json(
            {"type": "limits", **limits_ctx.snapshot().to_dict()}
        )

    # ── Запуск голосовой сессии ─────────────────────────────────────────
    try:
        await run_voice_session(websocket, limits_ctx=limits_ctx)
    except WebSocketDisconnect:
        logger.info("Клиент отключился: %s", websocket.client)
    except Exception as exc:
        logger.error(
            "Необработанное исключение в /ws/voice: %s",
            exc,
            exc_info=True,
        )
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except Exception:
            pass
    finally:
        logger.info("Сессия /ws/voice завершена: client=%s", websocket.client)
