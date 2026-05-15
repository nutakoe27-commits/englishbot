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
import urllib.parse
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .admin import router as admin_router
from .battle_api import router as battle_router
from .config import settings
from .db import db_session, init_db
from .limits import LimitsContext, build_limits_context
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


def _tg_id_from_init_data(init_data: str) -> int:
    """Валидирует Telegram WebApp initData (подпись + 24-часовой TTL) и
    возвращает tg_id юзера. Кидает HTTPException(401) при любой ошибке.

    Используется во всех Mini-App endpoint'ах. Identical в логике с
    battle_api._validate_init_data_and_get_tg_id — но тут без зависимости
    от того файла, чтобы main.py остался самодостаточным.
    """
    from fastapi import HTTPException, status
    import time as _time

    if not init_data:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "init_data required")
    if not settings.BOT_TOKEN:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "bot token missing")
    validated = validate_telegram_init_data(init_data, settings.BOT_TOKEN)
    if not validated:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid initData")
    try:
        auth_date = int(validated.get("auth_date") or 0)
    except (TypeError, ValueError):
        auth_date = 0
    if auth_date == 0 or (_time.time() - auth_date) > 24 * 3600:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "init_data expired")
    user_raw = validated.get("user")
    if not user_raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "initData has no user")
    try:
        user_obj = json.loads(user_raw)
        return int(user_obj["id"])
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad user payload in initData")


@app.get("/api/learner/recent-context", tags=["Learner"])
async def learner_recent_context(init_data: str = "") -> dict:
    """Контекст учащегося для Mini App: для post-session summary экрана.

    Возвращает:
      - streak: {current, best, last_practice_date}
      - vocab: [{word, times_used, last_seen_at}, ...] (топ-15 за неделю)
      - mistakes: [{category, bad, good, occurred_at}, ...] (топ-5 за неделю)
      - today_used_seconds: сколько юзер сегодня практиковался

    Аутентификация — Telegram WebApp initData.
    """
    tg_id = _tg_id_from_init_data(init_data)

    if not settings.DATABASE_URL:
        return {
            "streak": {"current": 0, "best": 0, "last_practice_date": None},
            "vocab": [],
            "mistakes": [],
            "today_used_seconds": 0,
        }

    from .db import Repo

    async with db_session() as session:
        repo = Repo(session)
        user = await repo.get_user_by_tg_id(tg_id)
        if user is None:
            return {
                "streak": {"current": 0, "best": 0, "last_practice_date": None},
                "vocab": [],
                "mistakes": [],
                "today_used_seconds": 0,
            }
        ctx = await repo.get_learner_context(user.id)
        used_today = await repo.get_used_seconds_today(user.id)

    return {
        "streak": {
            "current": int(user.streak_days or 0),
            "best": int(user.best_streak_days or 0),
            "last_practice_date": (
                user.last_practice_date.isoformat()
                if user.last_practice_date else None
            ),
        },
        "vocab": [
            {
                "word": v["word"],
                "times_used": v["times_used"],
                "last_seen_at": v["last_seen_at"].isoformat() if v["last_seen_at"] else None,
            }
            for v in ctx["recent_vocab"]
        ],
        "mistakes": [
            {
                "category": m["category"],
                "bad": m["bad"],
                "good": m["good"],
                "occurred_at": m["occurred_at"].isoformat() if m["occurred_at"] else None,
            }
            for m in ctx["recent_mistakes"]
        ],
        "today_used_seconds": int(used_today or 0),
    }


# ─── Мои слова (пользовательский словарь) ─────────────────────────────────────
# Юзер может через Mini App добавлять слова, которые сейчас учит. Тьютор в
# system_prompt получает их с пометкой «ACTIVELY WANTS to practice» и должен
# вкручивать в разговор. Лимит — Repo.USER_WORDS_LIMIT (100).

class _AddWordIn(BaseModel):
    init_data: str
    word: str
    note: Optional[str] = None  # зарезервировано для импорта; UI пока не шлёт


@app.get("/api/user-words", tags=["UserWords"])
async def get_user_words(init_data: str = "") -> dict:
    """Список user-слов для Mini App."""
    tg_id = _tg_id_from_init_data(init_data)
    if not settings.DATABASE_URL:
        return {"words": [], "total": 0, "limit": 100}
    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await repo.get_user_by_tg_id(tg_id)
        if user is None:
            return {"words": [], "total": 0, "limit": Repo.USER_WORDS_LIMIT}
        items = await repo.list_user_words(user.id)
    return {
        "words": [
            {
                "word": i["word"],
                "note": i["note"],
                "last_seen_at": i["last_seen_at"].isoformat() if i["last_seen_at"] else None,
            }
            for i in items
        ],
        "total": len(items),
        "limit": Repo.USER_WORDS_LIMIT,
    }


@app.post("/api/user-words", tags=["UserWords"])
async def post_user_word(body: _AddWordIn) -> dict:
    """Добавить user-слово.

    400 с error-кодом если empty/too_long/limit_reached/user-not-found.
    """
    from fastapi import HTTPException, status

    tg_id = _tg_id_from_init_data(body.init_data)
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "DB not configured")

    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await repo.get_user_by_tg_id(tg_id)
        if user is None:
            # Юзер ещё не upsert'нут — попросим открыть Mini App обычным
            # способом сначала (там в WS-preflight идёт upsert_user).
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "user_not_found"
            )
        result = await repo.add_user_word(user.id, body.word, note=body.note)
        if result == "ok":
            await session.commit()
            return {"ok": True}
        # «duplicate» считаем success-вариантом — слово уже есть, UI просто
        # перерисует список.
        if result == "duplicate":
            return {"ok": True, "duplicate": True}
        await session.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, result)


@app.delete("/api/user-words/{word}", tags=["UserWords"])
async def delete_user_word(word: str, init_data: str = "") -> dict:
    """Удалить user-слово."""
    from fastapi import HTTPException, status

    tg_id = _tg_id_from_init_data(init_data)
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "DB not configured")

    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await repo.get_user_by_tg_id(tg_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user_not_found")
        removed = await repo.remove_user_word(user.id, word)
        if not removed:
            await session.rollback()
            raise HTTPException(status.HTTP_404_NOT_FOUND, "word_not_found")
        await session.commit()
    return {"ok": True}


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
