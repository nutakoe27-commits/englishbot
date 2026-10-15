"""Prepared exercises are ephemeral; only actual practice is a DB session."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import time
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_, select

from .config import settings
from .db import Repo, db_session
from .db.models import Session, User
from . import presence

log = logging.getLogger(__name__)
PREPARATION_TTL = 30 * 60
ACTIVITY_TTL = 180
MAX_DURATION = 30 * 60


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


@dataclass
class Prepared:
    user_id: Optional[int]
    level: str
    role: str
    topic_key: Optional[str] = None
    learning_lesson_id: Optional[str] = None
    exercises: list[dict] = field(default_factory=list)
    expires_at: float = field(default_factory=lambda: time.monotonic() + PREPARATION_TTL)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cancelled: bool = False


PREPARED: dict[str, Prepared] = {}


def prepare(*, user_id: Optional[int], level: str, role: str, topic_key: Optional[str] = None, learning_lesson_id: Optional[str] = None, exercises: Optional[list[dict]] = None) -> str:
    key = f"prepared-{uuid4()}"
    PREPARED[key] = Prepared(user_id=user_id, level=level, role=role, topic_key=topic_key, learning_lesson_id=learning_lesson_id, exercises=exercises or [])
    return key


async def user_id(repo: Repo, authorization: Optional[str], init_data: Optional[str]) -> int:
    from .auth import resolve_user
    if not settings.DATABASE_URL:
        raise HTTPException(503, "db_not_configured")
    user = await resolve_user(repo, authorization=authorization, init_data=init_data)
    return int(user.id)


async def row_for_user(session, key: str, uid: int) -> Session:
    try:
        sid = int(key)
    except ValueError:
        raise HTTPException(404, "grammar_session_not_found") from None
    row = await session.scalar(select(Session).where(
        Session.id == sid, Session.user_id == uid, Session.mode == "grammar",
    ).with_for_update())
    if row is None:
        raise HTTPException(404, "grammar_session_not_found")
    return row


async def activate(key: str, authorization: Optional[str], init_data: Optional[str], *, allow_completed: bool = False) -> str:
    if not settings.DATABASE_URL:
        raise HTTPException(503, "db_not_configured")
    async with db_session() as session:
        uid = await user_id(Repo(session), authorization, init_data)
        existing = await session.scalar(select(Session).where(
            Session.grammar_preparation_id == key, Session.user_id == uid,
        ).with_for_update())
        if existing is not None:
            if existing.grammar_status == "completed" and not allow_completed:
                raise HTTPException(409, "grammar_session_completed")
            if existing.grammar_status == "abandoned" and not allow_completed:
                raise HTTPException(410, "grammar_session_abandoned")
            if existing.grammar_status == "active":
                existing.grammar_last_seen_at = now()
            return str(existing.id)
    prepared = PREPARED.get(key)
    if prepared is None or prepared.expires_at <= time.monotonic():
        raise HTTPException(410, "grammar_preparation_expired")
    if prepared.user_id != uid:
        raise HTTPException(403, "grammar_preparation_forbidden")
    async with prepared.lock:
        if prepared.cancelled:
            raise HTTPException(410, "grammar_preparation_expired")
        async with db_session() as session:
            # Serialize quota checks and activations for this account.
            user = await session.get(User, uid, with_for_update=True)
            existing = await session.scalar(select(Session).where(
                Session.grammar_preparation_id == key, Session.user_id == uid,
            ))
            if existing is not None:
                if existing.grammar_status == "completed" and not allow_completed:
                    raise HTTPException(409, "grammar_session_completed")
                if existing.grammar_status == "abandoned" and not allow_completed:
                    raise HTTPException(410, "grammar_session_abandoned")
                return str(existing.id)
            from .limits import is_section_limit_reached
            if user is None:
                raise HTTPException(401, "user_not_found")
            from . import learning_modules as course
            stage='mistakes' if prepared.role=='weak_points' else 'grammar'
            guided=await course.require(session,uid,prepared.learning_lesson_id,stage)
            if not guided and await is_section_limit_reached(Repo(session), user, section="grammar"):
                raise HTTPException(402, "limit_reached")
            row = await Repo(session).open_session(
                user_id=uid, mode="grammar", level=prepared.level, role=prepared.role,
            )
            row.grammar_status = "active"
            row.grammar_last_seen_at = now()
            row.grammar_preparation_id = key
            if guided is not None:
                row.grammar_result={"_exercises":prepared.exercises,"_learning_lesson_id":prepared.learning_lesson_id}
            sid = str(row.id)
            course.save(guided,stage,session_id=sid,completed=False)
        presence.mark(uid, mode="grammar", level=prepared.level, role=prepared.role, ttl=ACTIVITY_TTL)
        return sid


async def active_key(key: str, authorization: Optional[str], init_data: Optional[str], *, allow_completed: bool = False) -> str:
    # Also supports the previous frontend, which starts with heartbeat/finish.
    return await activate(key, authorization, init_data, allow_completed=allow_completed) if key.startswith("prepared-") else key


async def heartbeat(key: str, authorization: Optional[str], init_data: Optional[str], duration: int) -> bool:
    key = await active_key(key, authorization, init_data)
    async with db_session() as session:
        uid = await user_id(Repo(session), authorization, init_data)
        row = await row_for_user(session, key, uid)
        if row.grammar_status == "completed":
            return False
        row.grammar_status = "active"
        row.ended_at = None
        row.grammar_last_seen_at = now()
        row.used_seconds = max(row.used_seconds or 0, min(MAX_DURATION, max(0, duration)))
        level, role = row.level, row.role
    presence.mark(uid, mode="grammar", level=level, role=role, ttl=ACTIVITY_TTL)
    return True


async def abandon(key: str, authorization: Optional[str], init_data: Optional[str], duration: int) -> None:
    async with db_session() as session:
        uid = await user_id(Repo(session), authorization, init_data)
    prepared = PREPARED.get(key)
    if prepared is not None:
        if prepared.user_id != uid:
            raise HTTPException(403, "grammar_preparation_forbidden")
        async with prepared.lock:
            prepared.cancelled = True
            PREPARED.pop(key, None)
    async with db_session() as session:
        if key.startswith("prepared-"):
            row = await session.scalar(select(Session).where(
                Session.grammar_preparation_id == key, Session.user_id == uid,
            ).with_for_update())
        else:
            row = await row_for_user(session, key, uid)
        if row is not None and row.grammar_status != "completed":
            row.grammar_status = "abandoned"
            row.used_seconds = max(row.used_seconds or 0, min(MAX_DURATION, max(0, duration)))
            row.grammar_last_seen_at = now()
            row.ended_at = row.grammar_last_seen_at
    presence.clear(uid)


async def close_inactive() -> int:
    expired = [key for key, p in PREPARED.items() if p.expires_at <= time.monotonic()]
    for key in expired:
        PREPARED.pop(key, None)
    cutoff = now() - timedelta(seconds=ACTIVITY_TTL)
    async with db_session() as session:
        rows = list((await session.scalars(select(Session).where(
            Session.mode == "grammar", Session.ended_at.is_(None),
            or_(Session.grammar_status.is_(None),
                (Session.grammar_status == "active") & (Session.grammar_last_seen_at < cutoff)),
        ).with_for_update())).all())
        for row in rows:
            row.grammar_status = "abandoned"
            row.ended_at = row.grammar_last_seen_at or row.started_at
            # No fabricated duration or completion credit for old orphan rows.
    return len(rows)


async def cleanup_loop() -> None:
    while True:
        try:
            await close_inactive()
        except Exception as exc:
            log.warning("[grammar] session cleanup failed=%s; check migration 0042", type(exc).__name__)
        await asyncio.sleep(20)
