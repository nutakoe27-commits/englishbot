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

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .admin import router as admin_router
from .auth_routes import router as auth_router
from .internal_routes import router as internal_router
from .payment_routes import router as payment_router
from .grammar import router as grammar_router
from .listening import router as listening_router
from .srs import router as srs_router
from .tts import router as tts_router
from .auth import resolve_user
from .config import settings
from .db import db_session, init_db
from .limits import LimitsContext, build_limits_context
from .llm_providers import explain_correction, get_llm_provider, translate_word
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
app.include_router(auth_router)
app.include_router(internal_router)
app.include_router(payment_router)
app.include_router(listening_router)
app.include_router(grammar_router)
app.include_router(srs_router)
app.include_router(tts_router)


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
async def learner_recent_context(
    init_data: str = "", authorization: Optional[str] = Header(None),
) -> dict:
    """Контекст учащегося для Mini App: для post-session summary экрана.

    Возвращает:
      - streak: {current, best, last_practice_date}
      - vocab: [{word, times_used, last_seen_at}, ...] (топ-15 за неделю)
      - mistakes: [{category, bad, good, occurred_at}, ...] (топ-5 за неделю)
      - today_used_seconds: сколько юзер сегодня практиковался

    Аутентификация — Bearer JWT (веб) или Telegram initData (Mini App).
    """
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
        user = await resolve_user(repo, authorization=authorization, init_data=init_data)
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
# вкручивать в разговор. Эти же слова — карточки для SRS-режима «Слова».
# Лимит — Repo.USER_WORDS_LIMIT.

class _AddWordIn(BaseModel):
    init_data: Optional[str] = None    # Mini App; веб шлёт Bearer JWT
    word: str
    translation: Optional[str] = None  # перевод RU для SRS-карточки
    note: Optional[str] = None          # зарезервировано для импорта


@app.get("/api/user-words", tags=["UserWords"])
async def get_user_words(
    init_data: str = "", authorization: Optional[str] = Header(None),
) -> dict:
    """Список user-слов для Mini App / веба."""
    from .db import Repo
    if not settings.DATABASE_URL:
        return {"words": [], "total": 0, "limit": Repo.USER_WORDS_LIMIT}
    async with db_session() as session:
        repo = Repo(session)
        user = await resolve_user(repo, authorization=authorization, init_data=init_data)
        items = await repo.list_user_words(user.id)
    return {
        "words": [
            {
                "word": i["word"],
                "translation": i.get("translation"),
                "note": i.get("note"),
                "last_seen_at": i["last_seen_at"].isoformat() if i["last_seen_at"] else None,
                "srs_box": i.get("srs_box", 0),
                "srs_due_at": i["srs_due_at"].isoformat() if i.get("srs_due_at") else None,
            }
            for i in items
        ],
        "total": len(items),
        "limit": Repo.USER_WORDS_LIMIT,
    }


@app.post("/api/user-words", tags=["UserWords"])
async def post_user_word(
    body: _AddWordIn, authorization: Optional[str] = Header(None),
) -> dict:
    """Добавить user-слово.

    400 с error-кодом если empty/too_long/limit_reached.
    """
    from fastapi import HTTPException, status

    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "DB not configured")

    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await resolve_user(
            repo, authorization=authorization, init_data=body.init_data,
        )
        result = await repo.add_user_word(
            user.id, body.word, translation=body.translation, note=body.note,
        )
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
async def delete_user_word(
    word: str, init_data: str = "", authorization: Optional[str] = Header(None),
) -> dict:
    """Удалить user-слово."""
    from fastapi import HTTPException, status

    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "DB not configured")

    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await resolve_user(repo, authorization=authorization, init_data=init_data)
        removed = await repo.remove_user_word(user.id, word)
        if not removed:
            await session.rollback()
            raise HTTPException(status.HTTP_404_NOT_FOUND, "word_not_found")
        await session.commit()
    return {"ok": True}


# ─── Перевод слова по тапу в чате ─────────────────────────────────────────────

_TRANSLATION_CACHE: dict[tuple[str, str], list[str]] = {}
_TRANSLATION_CACHE_MAX = 2000

_RATE_WINDOW_SEC = 60
_RATE_LIMIT_PER_USER = 30
_RATE_BUCKETS: dict[int, deque] = defaultdict(deque)


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
    init_data: Optional[str] = None
    word: str = Field(..., min_length=1, max_length=64)
    context: str = Field("", max_length=500)


@app.post("/api/translate", tags=["Translate"])
async def translate(body: _TranslateIn, authorization: Optional[str] = Header(None)) -> dict:
    """Перевод одного английского слова на русский с учётом контекста реплики.

    Возвращает {word, translations: [primary, alt1, alt2]}. На пустой результат
    от LLM — translations=[].
    """
    from .auth import auth_key
    key = auth_key(authorization, body.init_data)
    word = body.word.strip().lower()
    if not word:
        raise HTTPException(status_code=400, detail="empty_word")
    _enforce_rate_limit(key)
    translations = await _get_translation_cached(word, body.context)
    return {"word": word, "translations": translations}


# ─── Объяснение correction'а: «🤔 почему?» в Mini App ─────────────────────────

_EXPLAIN_CACHE: dict[tuple[str, str], str] = {}
_EXPLAIN_CACHE_MAX = 1000


class _ExplainIn(BaseModel):
    init_data: Optional[str] = None
    original: str = Field(..., min_length=1, max_length=300)
    corrected: str = Field(..., min_length=1, max_length=300)


@app.post("/api/explain-correction", tags=["Translate"])
async def explain_correction_endpoint(
    body: _ExplainIn, authorization: Optional[str] = Header(None),
) -> dict:
    """Короткое объяснение по-русски, что не так с user-фразой.

    Возвращает {explanation: str}. На ошибку LLM — пустая строка.
    """
    from .auth import auth_key
    # Переиспользуем существующий rate-limit бакет с translate'ом.
    _enforce_rate_limit(auth_key(authorization, body.init_data))

    original = body.original.strip()[:200]
    corrected = body.corrected.strip()[:200]
    if not original or not corrected:
        raise HTTPException(status_code=400, detail="empty_text")
    key = (original.lower(), corrected.lower())

    cached = _EXPLAIN_CACHE.get(key)
    if cached is not None:
        return {"explanation": cached}

    llm = get_llm_provider()
    text = await explain_correction(llm, original=original, corrected=corrected)

    # FIFO-вытеснение при переполнении (как в translate-кеше).
    if len(_EXPLAIN_CACHE) >= _EXPLAIN_CACHE_MAX:
        try:
            _EXPLAIN_CACHE.pop(next(iter(_EXPLAIN_CACHE)), None)
        except StopIteration:
            pass
    _EXPLAIN_CACHE[key] = text
    return {"explanation": text}


# ─── Retention v1: /api/me/progress + /api/me/achievements ──────────────────

@app.get("/api/me/progress", tags=["Me"])
async def me_progress(
    init_data: str = "", authorization: Optional[str] = Header(None),
) -> dict:
    """Сводка прогресса для экрана «Мой прогресс» в mini-app / на вебе."""
    from fastapi import HTTPException, status
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db not configured")
    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await resolve_user(repo, authorization=authorization, init_data=init_data)
        streak_current, streak_best, last_practice = await repo.get_streak(user.id)
        total_seconds = await repo.user_total_seconds(user.id)
        total_sessions = await repo.user_total_sessions(user.id)
        total_words = await repo.count_user_words(user.id)
        daily = await repo.user_daily_usage_series(user.id, days=30)
        by_mode = await repo.user_total_seconds_by_mode(user.id)
        try:
            grammar_done, grammar_total = await repo.grammar_learn_counters(user.id)
        except Exception:
            grammar_done, grammar_total = 0, 0
        try:
            from .achievements import ACHIEVEMENTS, get_earned_keys
            ach_earned = len(await get_earned_keys(repo, user.id))
            ach_total = len(ACHIEVEMENTS)
        except Exception:
            ach_earned, ach_total = 0, 0
    # voice + chat = «разговор» (оба speaking-режима в мини-апе); listening — отдельно.
    speaking_seconds = int(by_mode.get("voice", 0)) + int(by_mode.get("chat", 0))
    listening_seconds = int(by_mode.get("listening", 0))
    grammar_seconds = int(by_mode.get("grammar", 0))
    return {
        "streak": {
            "current": streak_current,
            "best": streak_best,
            "last_practice_date": last_practice.isoformat() if last_practice else None,
        },
        "total_minutes": total_seconds // 60,
        "total_sessions": total_sessions,
        "total_words": total_words,
        "daily_usage": daily,
        "speaking_minutes": speaking_seconds // 60,
        "listening_minutes": listening_seconds // 60,
        "grammar_minutes": grammar_seconds // 60,
        "grammar_topics_done": grammar_done,
        "grammar_topics_total": grammar_total,
        "achievements_earned": ach_earned,
        "achievements_total": ach_total,
    }


@app.get("/api/me/achievements", tags=["Me"])
async def me_achievements(
    init_data: str = "", authorization: Optional[str] = Header(None),
) -> dict:
    """Каталог медалей с пометками earned/locked и current_value по каждой."""
    from fastapi import HTTPException, status
    from .achievements import ACHIEVEMENTS, collect_user_metrics, get_earned_keys
    from .db import Repo

    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db not configured")
    async with db_session() as session:
        repo = Repo(session)
        user = await resolve_user(repo, authorization=authorization, init_data=init_data)
        metrics = await collect_user_metrics(repo, user.id)
        earned_keys = await get_earned_keys(repo, user.id)
    return {
        "achievements": [
            {
                "key": ach.key,
                "title_ru": ach.title_ru,
                "description_ru": ach.description_ru,
                "icon": ach.icon,
                "metric": ach.metric,
                "target": ach.target,
                "current_value": int(metrics.get(ach.metric, 0)),
                "earned": ach.key in earned_keys,
            }
            for ach in ACHIEVEMENTS
        ],
    }


# ─── Геймификация: уровень + лидерборд ──────────────────────────────────────
@app.get("/api/me/level", tags=["Me"])
async def me_level(
    init_data: str = "", authorization: Optional[str] = Header(None),
) -> dict:
    """Уровень юзера по lifetime-очкам + прогресс до следующего уровня."""
    from fastapi import HTTPException, status
    from .db import Repo
    from .points import level_info
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db not configured")
    async with db_session() as session:
        repo = Repo(session)
        user = await resolve_user(repo, authorization=authorization, init_data=init_data)
        lifetime = await repo.user_points(user.id, month_only=False)
    return level_info(lifetime)


@app.get("/api/leaderboard", tags=["Me"])
async def leaderboard(
    init_data: str = "", authorization: Optional[str] = Header(None),
) -> dict:
    """Топ-5 за текущий месяц + строка самого юзера (место + очки).
    Ученик школы (B2B) видит лидерборд только своей школы."""
    from datetime import date as _date
    from fastapi import HTTPException, status
    from .db import Repo
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db not configured")

    def _name(fn: Optional[str]) -> str:
        return (fn or "").strip() or "Студент"

    async with db_session() as session:
        repo = Repo(session)
        user = await resolve_user(repo, authorization=authorization, init_data=init_data)
        org = await repo.user_active_org(user.id)
        org_id = org.id if org is not None else None
        org_name = org.name if org is not None else None
        top_rows = await repo.leaderboard_month(limit=5, org_id=org_id)
        my_rank, my_points = await repo.user_month_rank(user.id, org_id=org_id)

    top = [
        {
            "rank": i + 1,
            "name": _name(r["first_name"]),
            "points": r["points"],
            "is_me": r["user_id"] == user.id,
        }
        for i, r in enumerate(top_rows)
    ]
    # Имя текущего юзера для me-блока.
    me_name = next((t["name"] for t in top if t["is_me"]), None)
    if me_name is None:
        me_name = _name(getattr(user, "first_name", None))
    today = _date.today()
    return {
        "month": f"{today.year:04d}-{today.month:02d}",
        "top": top,
        "me": {"rank": my_rank, "points": my_points, "name": me_name},
        "total": len(top_rows),
        # Школьный лидерборд (B2B): фронт показывает «Лидерборд школы N».
        "org_name": org_name,
    }


class _OrgJoinIn(BaseModel):
    init_data: str = ""
    invite_code: str


def notify_admins_org_no_seats(org, joiner) -> None:
    """B2B: сообщить владельцу (ADMIN_IDS) в TG, что ученик не смог
    подключиться — места кончились. Сигнал продать расширение пакета.
    Fire-and-forget: ошибки только логируются."""
    from .auth import send_bot_message
    if org is None:
        return
    who = (getattr(joiner, "first_name", None) or "").strip() or "Ученик"
    username = getattr(joiner, "username", None)
    if username:
        who += f" (@{username})"
    text = (
        f"⚠️ Школа «{org.name}»: свободных мест нет "
        f"({org.seats_total}/{org.seats_total}).\n"
        f"{who} не смог подключиться. Возможно, школе пора расширить пакет."
    )
    for admin_id in settings.admin_ids_list:
        asyncio.create_task(send_bot_message(admin_id, text))


@app.post("/api/org/join", tags=["Me"])
async def org_join(
    body: _OrgJoinIn, authorization: Optional[str] = Header(None),
) -> dict:
    """B2B: подключение к школе по инвайт-коду из мини-аппа (?school=CODE).
    {status: ok|already|no_seats|invalid, org_name}."""
    from fastapi import HTTPException, status
    from .db import Repo
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db not configured")
    async with db_session() as session:
        repo = Repo(session)
        user = await resolve_user(
            repo, authorization=authorization, init_data=body.init_data,
        )
        status_str, org = await repo.join_org(body.invite_code, user.id)
        await session.commit()
    if status_str == "no_seats":
        notify_admins_org_no_seats(org, user)
    return {"status": status_str, "org_name": getattr(org, "name", None)}


class _OrgLeaveIn(BaseModel):
    init_data: str = ""


@app.post("/api/org/leave", tags=["Me"])
async def org_leave(
    body: _OrgLeaveIn, authorization: Optional[str] = Header(None),
) -> dict:
    """B2B: выход из школы по инициативе самого юзера (любая роль).
    Место освобождается; повторный переход по инвайт-ссылке вернёт."""
    from fastapi import HTTPException, status
    from .db import Repo
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db not configured")
    async with db_session() as session:
        repo = Repo(session)
        user = await resolve_user(
            repo, authorization=authorization, init_data=body.init_data,
        )
        mem = await repo.user_org_membership(user.id)
        if mem is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_org_member")
        org = mem[0]
        await repo.set_org_member_active(org.id, user.id, False)
        await session.commit()
    return {"ok": True, "org_name": org.name}


# ─── B2B фаза 2: кабинет школы (учитель/админ школы) ─────────────────────────

async def _resolve_org_staff(repo, *, authorization: Optional[str], init_data: str):
    """resolve_user + гейт кабинета: активный участник активной школы с
    ролью teacher/admin. Возвращает (user, org). 403 иначе."""
    from fastapi import HTTPException, status
    user = await resolve_user(repo, authorization=authorization, init_data=init_data)
    mem = await repo.user_org_membership(user.id)
    if mem is None or mem[1] not in ("teacher", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not_org_staff")
    return user, mem[0]


def _cabinet_students(rows: list[dict]) -> list[dict]:
    return [
        {
            **r,
            "joined_at": r["joined_at"].isoformat() if r["joined_at"] else None,
            "last_practice_date": (
                r["last_practice_date"].isoformat() if r["last_practice_date"] else None
            ),
        }
        for r in rows
    ]


@app.get("/api/org/cabinet", tags=["Org"])
async def org_cabinet(
    init_data: str = "", authorization: Optional[str] = Header(None),
) -> dict:
    """Кабинет школы: сводка + статистика учеников за текущий месяц."""
    from fastapi import HTTPException, status
    from .db import Repo
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db not configured")
    # Инвайт-ссылки — чтобы школа рассылала приглашения сама, без владельца.
    from .admin import _org_invite_link, _org_invite_link_web
    async with db_session() as session:
        repo = Repo(session)
        _user, org = await _resolve_org_staff(
            repo, authorization=authorization, init_data=init_data,
        )
        seats_used = await repo.org_seats_used(org.id)
        students = await repo.org_students_stats(org.id)
    return {
        "org": {
            "name": org.name,
            "seats_total": org.seats_total,
            "seats_used": seats_used,
            "valid_until": org.valid_until.isoformat() if org.valid_until else None,
            "invite_link": _org_invite_link(org.invite_code),
            "invite_link_web": _org_invite_link_web(org.invite_code),
        },
        "students": _cabinet_students(students),
    }


@app.get("/api/org/cabinet/student/{student_id}", tags=["Org"])
async def org_cabinet_student(
    student_id: int,
    init_data: str = "",
    authorization: Optional[str] = Header(None),
) -> dict:
    """Детали ученика для учителя: топ частых ошибок + уровень."""
    from fastapi import HTTPException, status
    from .db import Repo
    from .points import level_info
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db not configured")
    async with db_session() as session:
        repo = Repo(session)
        _user, org = await _resolve_org_staff(
            repo, authorization=authorization, init_data=init_data,
        )
        # Ученик должен быть участником ЭТОЙ школы — чужих не показываем.
        rows = await repo.org_students_stats(org.id)
        base = next((r for r in rows if int(r["user_id"]) == int(student_id)), None)
        if base is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "student_not_found")
        mistakes = await repo.get_recent_mistakes(student_id, limit=10, days=30)
        lifetime = await repo.user_points(student_id, month_only=False)
    return {
        "student": _cabinet_students([base])[0],
        "level": level_info(lifetime),
        "mistakes": [
            {
                "category": m.get("category"),
                "bad": m.get("bad"),
                "good": m.get("good"),
            }
            for m in mistakes
        ],
    }


class _CabinetStudentActiveIn(BaseModel):
    init_data: str = ""
    active: bool


@app.post("/api/org/cabinet/student/{student_id}/active", tags=["Org"])
async def org_cabinet_student_active(
    student_id: int,
    body: _CabinetStudentActiveIn,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Учитель исключает/возвращает ученика своей школы. Исключение
    освобождает место. Только учеников (teacher/admin трогать нельзя)."""
    from fastapi import HTTPException, status
    from .db import Repo
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db not configured")
    async with db_session() as session:
        repo = Repo(session)
        _user, org = await _resolve_org_staff(
            repo, authorization=authorization, init_data=body.init_data,
        )
        # Менять можно только учеников СВОЕЙ школы.
        rows = await repo.org_students_stats(org.id)
        if not any(int(r["user_id"]) == int(student_id) for r in rows):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "student_not_found")
        await repo.set_org_member_active(org.id, int(student_id), bool(body.active))
        await session.commit()
    return {"ok": True, "active": bool(body.active)}


@app.get("/api/org/cabinet/report.csv", tags=["Org"])
async def org_cabinet_report(
    init_data: str = "", authorization: Optional[str] = Header(None),
):
    """CSV-отчёт по ученикам за текущий месяц (UTF-8 BOM — для Excel)."""
    import csv
    import io
    from datetime import date as _date
    from fastapi import HTTPException, status
    from fastapi.responses import Response as FastResponse
    from .db import Repo
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db not configured")
    async with db_session() as session:
        repo = Repo(session)
        _user, org = await _resolve_org_staff(
            repo, authorization=authorization, init_data=init_data,
        )
        students = await repo.org_students_stats(org.id)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([
        "Ученик", "Username", "Разговор (мин)", "Подкасты (мин)",
        "Уроки грамматики", "Очки за месяц", "Стрик (дней)",
        "Последняя активность", "Подключён", "Статус",
    ])
    for r in students:
        w.writerow([
            r["first_name"] or "—",
            f"@{r['username']}" if r["username"] else "—",
            r["speaking_min"], r["listening_min"], r["grammar_lessons"],
            r["points_month"], r["streak_days"],
            r["last_practice_date"].isoformat() if r["last_practice_date"] else "—",
            r["joined_at"].date().isoformat() if r["joined_at"] else "—",
            "активен" if r["active"] else "отключён",
        ])
    today = _date.today()
    fname = f"report-{today.year:04d}-{today.month:02d}.csv"
    # BOM — чтобы Excel корректно открыл кириллицу в UTF-8.
    return FastResponse(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# Backfill достижений для существующих юзеров — единоразово при старте.
# Иначе при первой же сессии каждый активный юзер получит burst из 5+
# push'ей. Маркер хранится в settings_kv['achievements_backfilled'].
@app.on_event("startup")
async def _backfill_achievements_on_startup() -> None:
    if not settings.DATABASE_URL:
        return
    try:
        from .achievements import backfill_existing_users
        from .db import Repo
        async with db_session() as session:
            repo = Repo(session)
            done = await repo.get_kv("achievements_backfilled")
            if done == "1":
                logging.getLogger(__name__).warning(
                    "[achievements] backfill: already done"
                )
                return
            users_n, medals_n = await backfill_existing_users(repo)
            await repo.set_kv("achievements_backfilled", "1")
            await session.commit()
        logging.getLogger(__name__).warning(
            "[achievements] backfill: %d users processed, %d medals seeded",
            users_n, medals_n,
        )
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "[achievements] backfill failed: %r", exc,
        )


# Гарантируем существование промокода скидки (для рассылки не-подписчикам).
@app.on_event("startup")
async def _ensure_discount_promo_on_startup() -> None:
    if not settings.DATABASE_URL:
        return
    try:
        from .db import Repo
        async with db_session() as session:
            repo = Repo(session)
            await repo.ensure_promo(
                settings.DISCOUNT_PROMO_CODE, settings.DISCOUNT_PROMO_PERCENT,
            )
            await session.commit()
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "[discount] ensure promo failed: %r", exc,
        )


# ─── WebSocket — голосовой диалог ─────────────────────────────────────────────

@app.websocket("/ws/voice")
async def ws_voice(websocket: WebSocket, init_data: str = "", token: str = ""):
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
    # ── Авторизация: Bearer JWT (веб) ИЛИ Telegram initData (Mini App) ─────
    user_info: Optional[dict] = None
    web_uid: Optional[int] = None  # users.id для веб-сессии (JWT)

    if token:
        from .auth import verify_jwt
        web_uid = verify_jwt(token)
        if web_uid is None and not settings.is_development:
            await websocket.close(code=4001, reason="Unauthorized: invalid token")
            return

    if not web_uid and init_data:
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
    elif not web_uid:
        # Ни JWT, ни init_data
        if not settings.is_development:
            logger.warning("Нет токена/init_data в production — отклоняем соединение")
            await websocket.close(code=4001, reason="Unauthorized: missing credentials")
            return
        else:
            logger.info("DEV: нет токена/init_data — пропускаем проверку")

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
    if (user_info or web_uid) and settings.DATABASE_URL:
        logger.info("[WS] начинаю проверку maintenance/лимитов")

        async def _check_db_preflight() -> Optional[LimitsContext]:
            async with db_session() as session:
                from .db import Repo
                from .limits import context_for_user
                repo = Repo(session)
                tg_for_admin = int(user_info["id"]) if user_info else None
                # 1. Maintenance — всех отправляем лесом, кроме админов
                if await repo.get_kv_bool("maintenance_mode", False):
                    if tg_for_admin not in settings.admin_ids_list:
                        msg = await repo.get_kv(
                            "maintenance_message",
                            "Бот временно недоступен — ведутся технические работы.",
                        )
                        await websocket.send_json(
                            {"type": "maintenance", "message": msg}
                        )
                        await websocket.close(code=4002, reason="Maintenance mode")
                        return None
                # 2. Лимиты: веб (JWT) → по users.id; Mini App → upsert по tg_id
                if web_uid:
                    user = await repo.get_user_by_id(web_uid)
                    if user is None:
                        await websocket.close(code=4001, reason="Unauthorized")
                        return None
                    return await context_for_user(repo, db_session, user)
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
