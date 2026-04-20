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
import logging
import os
import urllib.parse
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
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

@app.get("/health", tags=["System"])
async def health() -> dict:
    """Проверка работоспособности сервиса. Используется Docker healthcheck."""
    return {"status": "ok", "service": "backend"}


# ─── REST API v1 ──────────────────────────────────────────────────────────────

@app.get("/api/v1/ping", tags=["API v1"])
async def ping() -> dict:
    """Тестовый эндпоинт. Проверяет доступность API."""
    return {"pong": True, "version": "0.3.0"}


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

    # ── Запуск голосовой сессии ─────────────────────────────────────────
    try:
        await run_voice_session(websocket)
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
