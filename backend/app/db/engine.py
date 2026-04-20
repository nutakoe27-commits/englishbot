"""SQLAlchemy async engine + session factory.

Подключение задаётся через DATABASE_URL в .env, формат:
    mysql+aiomysql://user:password@host:port/dbname?charset=utf8mb4

Если DATABASE_URL не задан — engine не создаётся, вся работа с БД
выкидывает RuntimeError. Это позволяет постепенно включать БД-
функционал, не ломая существующий voice-сервис на dev-окружении.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..config import settings

log = logging.getLogger(__name__)

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def init_db() -> bool:
    """Инициализировать engine. Возвращает True, если БД доступна."""
    global _engine, _session_factory
    url = getattr(settings, "DATABASE_URL", None)
    if not url:
        log.warning("DATABASE_URL не задан — БД отключена")
        return False
    if _engine is not None:
        return True
    log.info("Инициализация БД, URL=%s", _mask_url(url))
    _engine = create_async_engine(
        url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,    # автоматически переоткрывает мертвые соединения
        pool_recycle=3600,     # MySQL по умолчанию закрывает простаивающие через 8ч
        echo=False,
    )
    _session_factory = async_sessionmaker(
        _engine, expire_on_commit=False, class_=AsyncSession
    )
    return True


def db_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("DB не инициализирована — вызови init_db() при старте")
    return _engine


@asynccontextmanager
async def db_session() -> AsyncIterator[AsyncSession]:
    """Контекст-менеджер для сессии. Автокоммит при успехе, rollback при ошибке."""
    if _session_factory is None:
        raise RuntimeError("DB не инициализирована")
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _mask_url(url: str) -> str:
    """Скрыть пароль в логах."""
    try:
        before, _, rest = url.partition("://")
        if "@" not in rest:
            return url
        creds, _, host = rest.partition("@")
        if ":" not in creds:
            return url
        user, _, _pwd = creds.partition(":")
        return f"{before}://{user}:***@{host}"
    except Exception:
        return "***"
