"""
presence.py — in-memory реестр «кто сейчас занимается».

Работает только на single-worker uvicorn (см. Dockerfile — без --workers).
Если когда-нибудь добавим воркеры или несколько инстансов backend, это нужно
будет вынести в Redis. Сейчас — простой dict в памяти процесса.

Источники присутствия:
  - voice/chat WS-сессии: mark() при open_session, touch() на каждом
    usage-heartbeat, clear() в finally. TTL продлевается heartbeat'ом.
  - listening-генерация: mark() в начале POST /api/listening/generate,
    clear() в finally. TTL покрывает максимальное время генерации.

Ключ — user_id (db id). У одного юзера может быть лишь одна активная сессия
в норме; при гонке «последняя побеждает» — это приемлемо.
"""

from __future__ import annotations

import time
from typing import Optional

# user_id -> {user_id, mode, level, role, started_at, expires_at}
_ONLINE: dict[int, dict] = {}


def mark(
    user_id: int,
    *,
    mode: str,
    level: Optional[str],
    role: Optional[str],
    ttl: float,
) -> None:
    """Зарегистрировать/обновить присутствие. started_at не сбрасываем при
    обновлении — чтобы длительность сессии считалась от первого mark()."""
    now = time.time()
    existing = _ONLINE.get(user_id)
    started_at = existing["started_at"] if existing else now
    _ONLINE[user_id] = {
        "user_id": user_id,
        "mode": mode,
        "level": level,
        "role": role,
        "started_at": started_at,
        "expires_at": now + ttl,
    }


def touch(user_id: int, ttl: float) -> None:
    """Продлить присутствие (вызывается из usage-heartbeat)."""
    entry = _ONLINE.get(user_id)
    if entry is not None:
        entry["expires_at"] = time.time() + ttl


def clear(user_id: int) -> None:
    _ONLINE.pop(user_id, None)


def snapshot() -> list[dict]:
    """Активные записи с GC просроченных. Добавляет duration_sec."""
    now = time.time()
    dead = [uid for uid, e in _ONLINE.items() if e["expires_at"] <= now]
    for uid in dead:
        _ONLINE.pop(uid, None)
    return [
        {
            "user_id": e["user_id"],
            "mode": e["mode"],
            "level": e["level"],
            "role": e["role"],
            "duration_sec": int(now - e["started_at"]),
        }
        for e in _ONLINE.values()
    ]
