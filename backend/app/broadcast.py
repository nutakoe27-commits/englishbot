"""Фоновая рассылка сообщений всем пользователям через Telegram Bot API.

Дизайн: один глобальный in-memory job (синглтон). Стартуется через
`start_broadcast(text)`. Пока крутится — `status()` возвращает прогресс,
`cancel()` ставит флаг отмены (job проверяет его на каждой итерации).

Rate limit: 25 msg/sec (ниже telegram-лимита 30/сек). При 429 спим retry_after.
При 403 помечаем юзера заблокированным (bot blocked by user).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import httpx

from .config import settings
from .db import db_session
from .db.repo import Repo

log = logging.getLogger(__name__)

TG_API = "https://api.telegram.org"
RATE_LIMIT_PER_SEC = 25
SLEEP_BETWEEN = 1.0 / RATE_LIMIT_PER_SEC  # 0.04 сек


@dataclass
class BroadcastJob:
    job_id: str
    text: str
    total: int = 0
    sent: int = 0
    failed: int = 0
    blocked: int = 0
    is_running: bool = False
    cancelled: bool = False
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    _task: Optional[asyncio.Task] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "text_preview": self.text[:200],
            "total": self.total,
            "sent": self.sent,
            "failed": self.failed,
            "blocked": self.blocked,
            "is_running": self.is_running,
            "cancelled": self.cancelled,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


# Глобальный синглтон (одна рассылка за раз)
_current: Optional[BroadcastJob] = None
_lock = asyncio.Lock()


def current_job() -> Optional[BroadcastJob]:
    return _current


async def send_message_to_tg(
    client: httpx.AsyncClient, chat_id: int, text: str
) -> tuple[bool, Optional[int], Optional[int]]:
    """Отправить одно сообщение. Возвращает (ok, status_code, retry_after).

    retry_after != None только при 429.
    """
    if not settings.BOT_TOKEN:
        return False, None, None
    url = f"{TG_API}/bot{settings.BOT_TOKEN}/sendMessage"
    try:
        r = await client.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10.0,
        )
    except Exception as e:
        log.warning("send_message_to_tg: exception %s", e)
        return False, None, None

    if r.status_code == 200:
        return True, 200, None
    if r.status_code == 429:
        try:
            retry = int(r.json().get("parameters", {}).get("retry_after", 1))
        except Exception:
            retry = 1
        return False, 429, retry
    return False, r.status_code, None


async def _run_job(job: BroadcastJob) -> None:
    """Основной цикл рассылки."""
    job.is_running = True
    job.started_at = time.time()
    try:
        # 1) Собираем получателей
        async with db_session() as s:
            repo = Repo(s)
            recipients = await repo.get_broadcast_recipients()
        job.total = len(recipients)

        if not settings.BOT_TOKEN:
            job.error = "BOT_TOKEN не задан в .env"
            return

        async with httpx.AsyncClient() as client:
            for u in recipients:
                if job.cancelled:
                    log.info("broadcast %s cancelled at %d/%d", job.job_id, job.sent, job.total)
                    break

                ok, code, retry_after = await send_message_to_tg(
                    client, u.tg_id, job.text
                )
                if ok:
                    job.sent += 1
                elif code == 429 and retry_after:
                    # Спим и повторяем без счётчика failed
                    await asyncio.sleep(retry_after + 0.1)
                    ok2, code2, _ = await send_message_to_tg(client, u.tg_id, job.text)
                    if ok2:
                        job.sent += 1
                    else:
                        job.failed += 1
                elif code == 403:
                    # Бот заблокирован пользователем
                    job.blocked += 1
                    try:
                        async with db_session() as s:
                            repo = Repo(s)
                            u2 = await repo.get_user_by_id(u.id)
                            if u2:
                                await repo.set_blocked(u2, True)
                    except Exception as e:
                        log.warning("failed to mark blocked user %s: %s", u.id, e)
                else:
                    job.failed += 1

                await asyncio.sleep(SLEEP_BETWEEN)
    except Exception as e:
        log.exception("broadcast job %s failed", job.job_id)
        job.error = str(e)
    finally:
        job.is_running = False
        job.finished_at = time.time()


async def start_broadcast(text: str) -> BroadcastJob:
    """Запустить рассылку. Если уже крутится другая — ошибка."""
    global _current
    async with _lock:
        if _current and _current.is_running:
            raise RuntimeError("Уже идёт рассылка. Дождитесь окончания или отмените её.")
        job = BroadcastJob(job_id=uuid.uuid4().hex[:12], text=text)
        _current = job
        job._task = asyncio.create_task(_run_job(job))
        return job


async def cancel_broadcast() -> bool:
    """Поставить флаг отмены. Возвращает True если было что отменять."""
    job = _current
    if not job or not job.is_running:
        return False
    job.cancelled = True
    return True
