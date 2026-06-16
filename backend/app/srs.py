"""
srs.py — 4-й режим тренировки: интервальное повторение слов словаря (Leitner).

Поток:
  1) GET  /api/srs/stats     — сколько карточек готово к повтору + лимит.
                                Для бейджа на главном экране режима «Слова».
  2) POST /api/srs/session/start — открывает Session(mode='srs') в БД, кладёт
                                в in-memory store, регистрирует presence.
  3) GET  /api/srs/session   — отдаёт до N (≤20) due-карточек, отсортированных
                                по самой старой due_at (нагоняем долг).
  4) POST /api/srs/review    — body {word, correct}. Применяет Leitner к
                                карточке (box+1 / box=0) и пересчитывает due_at.
  5) POST /api/srs/heartbeat — продлевает presence; ничего в БД не пишет.
  6) POST /api/srs/session/finish — закрывает Session, инкрементит DailyUsage,
                                поднимает streak (если ≥30с). Зеркалит
                                паттерн grammar.finish / listening.finalize.

SRS-интервалы (хардкод в Repo.SRS_INTERVALS_DAYS):
  box 0 → +0 дней   (только что провалена — повторить в этой же сессии)
  box 1 → +1 день
  box 2 → +3 дня
  box 3 → +7 дней
  box 4 → +14 дней
  box 5 → +30 дней  (выучено, max)
"""

from __future__ import annotations

import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from . import presence
from .auth import auth_key, resolve_user
from .config import settings
from .db import db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/srs", tags=["SRS"])


# Лимит карточек, отдаваемых на одну review-сессию. Сессия завершается
# раньше, если due-карточек меньше. Подобрано так, чтобы сессия укладывалась
# в ~5-10 минут — сопоставимо с grammar / listening.
SESSION_CARDS_LIMIT = 20

# Heartbeat TTL: фронт шлёт каждые 20с — берём 60с с запасом, как в grammar.
PRESENCE_TTL = 60

# In-memory: session_id → {user_id, started_at_unix}. Аналог _SESSION_STORE
# в grammar.py — finish удаляет запись.
_SESSION_STORE: dict[str, dict] = {}


# ─── Schemas ─────────────────────────────────────────────────────────────────


class _StartIn(BaseModel):
    init_data: Optional[str] = None


class _StartOut(BaseModel):
    session_id: str


class _Card(BaseModel):
    word: str
    translation: Optional[str] = None
    box: int


class _SessionOut(BaseModel):
    cards: list[_Card]


class _StatsOut(BaseModel):
    due_count: int
    total_count: int
    limit: int


class _HeartbeatIn(BaseModel):
    init_data: Optional[str] = None
    session_id: str


class _ReviewIn(BaseModel):
    init_data: Optional[str] = None
    word: str
    correct: bool


class _ReviewOut(BaseModel):
    ok: bool = True
    new_box: int = 0
    next_due_at: Optional[str] = None


class _FinishIn(BaseModel):
    init_data: Optional[str] = None
    session_id: str
    reviewed: int = 0
    correct: int = 0
    duration_sec: int = 0


class _FinishOut(BaseModel):
    ok: bool = True
    streak_current: int = 0
    streak_best: int = 0


# ─── Auth helper ─────────────────────────────────────────────────────────────


def _tg_id_from_init_data(init_data: str) -> int:
    """Обёртка над main._tg_id_from_init_data — внутри функции, чтобы избежать
    циклических импортов на module-evaluation (main импортит этот router)."""
    from .main import _tg_id_from_init_data as _impl
    return _impl(init_data)


# ─── Endpoints ───────────────────────────────────────────────────────────────


@router.get("/stats", response_model=_StatsOut)
async def stats(
    init_data: str = "", authorization: Optional[str] = Header(None),
) -> _StatsOut:
    """Сколько карточек готово к повторению + общий счётчик слов."""
    from .db.repo import Repo
    if not settings.DATABASE_URL:
        return _StatsOut(due_count=0, total_count=0, limit=Repo.USER_WORDS_LIMIT)
    async with db_session() as session:
        repo = Repo(session)
        user = await resolve_user(repo, authorization=authorization, init_data=init_data)
        due = await repo.count_srs_due(user.id)
        total = await repo.count_user_words(user.id)
    return _StatsOut(due_count=due, total_count=total, limit=Repo.USER_WORDS_LIMIT)


@router.get("/session", response_model=_SessionOut)
async def get_session(
    init_data: str = "", limit: int = SESSION_CARDS_LIMIT,
    authorization: Optional[str] = Header(None),
) -> _SessionOut:
    """Топ-N due-карточек для review. Если 0 — пустой массив."""
    n = max(1, min(int(limit or 0), SESSION_CARDS_LIMIT))
    if not settings.DATABASE_URL:
        return _SessionOut(cards=[])

    from .db.repo import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await resolve_user(repo, authorization=authorization, init_data=init_data)
        rows = await repo.list_srs_due(user.id, limit=n)
    return _SessionOut(
        cards=[
            _Card(word=r["word"], translation=r.get("translation"), box=r["srs_box"])
            for r in rows
        ]
    )


@router.post("/session/start", response_model=_StartOut)
async def start_session(
    body: _StartIn, authorization: Optional[str] = Header(None),
) -> _StartOut:
    """Открывает Session(mode='srs') в БД и регистрирует presence."""
    user_id: Optional[int] = None
    session_id_str = ""

    if settings.DATABASE_URL:
        from .db.repo import Repo
        async with db_session() as session:
            repo = Repo(session)
            user = await resolve_user(
                repo, authorization=authorization, init_data=body.init_data,
            )
            user_id = user.id
            row = await repo.open_session(
                user_id=user_id, mode="srs", level=None, role=None,
            )
            session_id_str = str(row.id)
            await session.commit()
    else:
        # Dev-режим без БД — синтетический id.
        session_id_str = "dev-" + secrets.token_urlsafe(8)

    if user_id is not None:
        presence.mark(user_id, mode="srs", level=None, role=None, ttl=PRESENCE_TTL)

    _SESSION_STORE[session_id_str] = {"user_id": user_id}
    return _StartOut(session_id=session_id_str)


@router.post("/heartbeat")
async def heartbeat(body: _HeartbeatIn, authorization: Optional[str] = Header(None)) -> dict:
    """Продлевает presence. Без обращений к БД."""
    auth_key(authorization, body.init_data)  # проверка авторизации
    entry = _SESSION_STORE.get(body.session_id)
    if entry is None:
        return {"ok": True, "known": False}
    user_id = entry.get("user_id")
    if user_id is not None:
        presence.touch(user_id, PRESENCE_TTL)
    return {"ok": True, "known": True}


@router.post("/review", response_model=_ReviewOut)
async def review(body: _ReviewIn, authorization: Optional[str] = Header(None)) -> _ReviewOut:
    """Применить Leitner к карточке. Один UPDATE на карточку, без открытия
    отдельной Session — review дёргается на каждый ответ юзера, гранулярность
    Session тут не нужна (общая обёртка — POST /session/start)."""
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "DB not configured")

    from .db.repo import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await resolve_user(repo, authorization=authorization, init_data=body.init_data)
        result = await repo.record_srs_review(user.id, body.word, correct=body.correct)
        if result is None:
            await session.rollback()
            raise HTTPException(status.HTTP_404_NOT_FOUND, "card_not_found")
        await session.commit()
    return _ReviewOut(
        ok=True,
        new_box=int(result["new_box"]),
        next_due_at=result["next_due_at"].isoformat() if result["next_due_at"] else None,
    )


@router.post("/session/finish", response_model=_FinishOut)
async def finish_session(body: _FinishIn, authorization: Optional[str] = Header(None)) -> _FinishOut:
    """Закрывает Session, инкрементит DailyUsage, поднимает streak.

    Зеркалит финализацию grammar.finish / listening.
    """
    entry = _SESSION_STORE.pop(body.session_id, None)
    user_id = entry.get("user_id") if entry else None

    # Если store потерял запись (рестарт backend) — резолвим юзера заново.
    if user_id is None and settings.DATABASE_URL:
        from .db.repo import Repo
        async with db_session() as session:
            repo = Repo(session)
            user = await resolve_user(
                repo, authorization=authorization, init_data=body.init_data,
            )
            if user is not None:
                user_id = user.id

    # Длительность: clip снизу/сверху, как в grammar.finish.
    duration_sec = max(0, min(int(body.duration_sec or 0), 30 * 60))

    streak_current = 0
    streak_best = 0

    if user_id is not None and settings.DATABASE_URL:
        from .db.repo import Repo
        from .voice import STREAK_MIN_DURATION_SEC
        try:
            async with db_session() as session:
                repo = Repo(session)
                try:
                    sess_id_int = int(body.session_id)
                except ValueError:
                    sess_id_int = 0
                if sess_id_int > 0:
                    try:
                        await repo.close_session(
                            session_id=sess_id_int, used_seconds=duration_sec,
                        )
                    except Exception as exc:
                        logger.warning("[srs] close_session failed: %s", exc)
                if duration_sec > 0:
                    await repo.add_used_seconds(user_id=user_id, seconds=duration_sec)
                if duration_sec >= STREAK_MIN_DURATION_SEC:
                    try:
                        streak_current, streak_best = await repo.bump_streak(
                            user_id, role="srs",
                        )
                    except Exception as exc:
                        logger.warning("[srs] bump_streak failed: %s", exc)
                await session.commit()
        except Exception as exc:
            logger.warning("[srs] finish DB error: %s", exc)

    if user_id is not None:
        presence.clear(user_id)

    return _FinishOut(
        ok=True,
        streak_current=streak_current,
        streak_best=streak_best,
    )
