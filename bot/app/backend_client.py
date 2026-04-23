"""HTTP-клиент бота для вызова REST API backend'а.

Сейчас покрывает:
  - Battle: create / accept / get_state
  - Daily Quest: assign

Все вызовы идут с заголовком X-Bot-Secret (переменная BACKEND_BOT_SECRET
в .env, такая же на бэкенде). Это service-to-service, без юзерского
initData — бот дёргает backend от своего имени.

Если BACKEND_URL или BACKEND_BOT_SECRET не заданы — клиент возвращает
None вместо исключений и логирует ошибку. Это позволяет боту работать
"вхолостую" до деплоя новой версии backend'а.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import httpx

log = logging.getLogger(__name__)

BACKEND_URL: str = os.getenv("BACKEND_URL", "").rstrip("/")
BACKEND_BOT_SECRET: str = os.getenv("BACKEND_BOT_SECRET", "").strip()

_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)


def _client() -> Optional[httpx.AsyncClient]:
    if not BACKEND_URL or not BACKEND_BOT_SECRET:
        log.error(
            "[backend_client] BACKEND_URL (%r) или BACKEND_BOT_SECRET (пустой=%s) не заданы",
            BACKEND_URL, not bool(BACKEND_BOT_SECRET),
        )
        return None
    return httpx.AsyncClient(
        base_url=BACKEND_URL,
        timeout=_TIMEOUT,
        headers={"X-Bot-Secret": BACKEND_BOT_SECRET},
    )


# ─── DTO ────────────────────────────────────────────────────────────────

@dataclass
class BattleCreateResult:
    id: int
    topic_key: str
    topic_title_ru: str


@dataclass
class BattleAcceptResult:
    id: int
    topic_key: str
    topic_title_ru: str
    initiator_tg_id: int
    opponent_tg_id: int
    prompt_en: str
    side_a_ru: str
    side_b_ru: str


@dataclass
class DailyQuestResult:
    key: str
    title_ru: str
    description_ru: str
    reward_seconds: int


# ─── Battle ─────────────────────────────────────────────────────────────

async def battle_create(
    *,
    initiator_tg_id: int,
    chat_id: Optional[int] = None,
    chat_message_id: Optional[int] = None,
    inline_message_id: Optional[str] = None,
) -> Optional[BattleCreateResult]:
    c = _client()
    if c is None:
        return None
    try:
        async with c as client:
            r = await client.post(
                "/api/battles/create",
                json={
                    "initiator_tg_id": initiator_tg_id,
                    "chat_id": chat_id,
                    "chat_message_id": chat_message_id,
                    "inline_message_id": inline_message_id,
                },
            )
            if r.status_code >= 400:
                log.error(
                    "[backend_client] battle_create HTTP %s: %s",
                    r.status_code, r.text[:500],
                )
            r.raise_for_status()
            d = r.json()
            return BattleCreateResult(
                id=d["id"], topic_key=d["topic_key"], topic_title_ru=d["topic_title_ru"],
            )
    except Exception as exc:
        log.error("[backend_client] battle_create failed: %s", exc)
        return None


async def battle_accept(
    *, battle_id: int, opponent_tg_id: int,
) -> Optional[BattleAcceptResult]:
    c = _client()
    if c is None:
        return None
    try:
        async with c as client:
            r = await client.post(
                f"/api/battles/{battle_id}/accept",
                json={"opponent_tg_id": opponent_tg_id},
            )
            if r.status_code == 400:
                return None
            if r.status_code >= 400:
                log.error(
                    "[backend_client] battle_accept HTTP %s: %s",
                    r.status_code, r.text[:500],
                )
            r.raise_for_status()
            d = r.json()
            return BattleAcceptResult(
                id=d["id"],
                topic_key=d["topic_key"],
                topic_title_ru=d["topic_title_ru"],
                initiator_tg_id=d["initiator_tg_id"],
                opponent_tg_id=d["opponent_tg_id"],
                prompt_en=d["prompt_en"],
                side_a_ru=d["side_a_ru"],
                side_b_ru=d["side_b_ru"],
            )
    except Exception as exc:
        log.error("[backend_client] battle_accept failed: %s", exc)
        return None


async def battle_state(battle_id: int) -> Optional[dict]:
    """Полное состояние battle — для кнопки «Показать результат»."""
    c = _client()
    if c is None:
        return None
    try:
        async with c as client:
            r = await client.get(f"/api/battles/{battle_id}")
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        log.error("[backend_client] battle_state failed: %s", exc)
        return None


# ─── Quests ─────────────────────────────────────────────────────────────

async def quest_assign(
    *, tg_id: int, user_level: Optional[str] = None,
) -> Optional[DailyQuestResult]:
    c = _client()
    if c is None:
        return None
    try:
        async with c as client:
            r = await client.post(
                "/api/quests/assign",
                json={"tg_id": tg_id, "user_level": user_level},
            )
            r.raise_for_status()
            d = r.json()
            if d is None:
                return None
            return DailyQuestResult(
                key=d["key"],
                title_ru=d["title_ru"],
                description_ru=d["description_ru"],
                reward_seconds=d["reward_seconds"],
            )
    except Exception as exc:
        log.error("[backend_client] quest_assign failed: %s", exc)
        return None
