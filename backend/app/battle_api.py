"""REST API для Battle Mode и Daily Quest.

Эндпоинты:
  POST /api/battles/create            — create_battle (бот: inline_query chosen)
  POST /api/battles/{id}/accept       — юзер Б принял (бот: callback)
  POST /api/battles/{id}/record       — Mini App загрузил запись участника
  GET  /api/battles/{id}              — получить текущее состояние
  GET  /api/battles/my                — список моих активных battle

  GET  /api/quests/active             — активный квест юзера (Mini App)
  POST /api/quests/assign             — принудительно выдать (бот, утренний cron)
  POST /api/quests/verify             — Mini App: проверить после сессии

Аутентификация:
  - Эндпоинты, доступные Mini App, валидируют Telegram initData (тот же
    механизм, что в ws_voice).
  - Эндпоинты, дёргаемые ботом напрямую (accept через callback, assign
    из cron), идут с Bot Secret — общий ключ BACKEND_BOT_SECRET из .env.

Это намеренно разные пути аутентификации: initData годится только когда
юзер открывает Mini App (Telegram подписывает данные), а боту нужен
service-to-service вызов без юзерского initData.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import battle as battle_mod
from . import battle_topics
from . import quests as quests_mod
from .db import db_session
from .db.models import User
from .db.repo import Repo


log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["Battle & Quests"])


# ─── Auth helpers ───────────────────────────────────────────────────────

def _require_bot_secret(x_bot_secret: Optional[str] = Header(None)) -> None:
    """Service-to-service: бот → backend. Секрет из переменной окружения."""
    expected = os.getenv("BACKEND_BOT_SECRET", "").strip()
    if not expected:
        log.error("BACKEND_BOT_SECRET not set — service calls disabled")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "service auth not configured")
    if not x_bot_secret or x_bot_secret != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad bot secret")


# ─── Pydantic модели ────────────────────────────────────────────────────

class CreateBattleIn(BaseModel):
    initiator_tg_id: int
    chat_id: int
    chat_message_id: Optional[int] = None


class CreateBattleOut(BaseModel):
    id: int
    topic_key: str
    topic_title_ru: str


class AcceptBattleIn(BaseModel):
    opponent_tg_id: int


class AcceptBattleOut(BaseModel):
    id: int
    topic_key: str
    topic_title_ru: str
    initiator_tg_id: int
    opponent_tg_id: int
    prompt_en: str
    side_a_ru: str
    side_b_ru: str


class RecordIn(BaseModel):
    tg_id: int
    audio_path: str = Field(..., max_length=500)
    transcript: str


class BattleStateOut(BaseModel):
    id: int
    status: str
    topic_key: str
    topic_title_ru: str
    initiator_tg_id: int
    opponent_tg_id: Optional[int]
    a_recorded: bool
    b_recorded: bool
    winner: Optional[str]
    judge_comment: Optional[str]
    a_score: Optional[dict]
    b_score: Optional[dict]


class AssignQuestIn(BaseModel):
    tg_id: int
    user_level: Optional[str] = None  # "A2" | "B1" | "B2" | "C1"


class QuestOut(BaseModel):
    key: str
    title_ru: str
    description_ru: str
    reward_seconds: int


class VerifyQuestIn(BaseModel):
    tg_id: int
    transcript: str
    role: Optional[str] = None
    duration_sec: int = 0


class VerifyQuestOut(BaseModel):
    completed: bool
    quest_key: Optional[str]
    reward_seconds: int
    badge_key: Optional[str]


# ─── Battle endpoints ───────────────────────────────────────────────────

@router.post(
    "/battles/create",
    response_model=CreateBattleOut,
    dependencies=[Depends(_require_bot_secret)],
)
async def api_create_battle(body: CreateBattleIn) -> CreateBattleOut:
    async with db_session() as s:
        created = await battle_mod.create_battle(
            s,
            initiator_tg_id=body.initiator_tg_id,
            chat_id=body.chat_id,
            chat_message_id=body.chat_message_id,
        )
        await s.commit()
    return CreateBattleOut(
        id=created.id,
        topic_key=created.topic_key,
        topic_title_ru=created.topic_title_ru,
    )


@router.post(
    "/battles/{battle_id}/accept",
    response_model=AcceptBattleOut,
    dependencies=[Depends(_require_bot_secret)],
)
async def api_accept_battle(battle_id: int, body: AcceptBattleIn) -> AcceptBattleOut:
    async with db_session() as s:
        accepted = await battle_mod.accept_battle(
            s, battle_id=battle_id, opponent_tg_id=body.opponent_tg_id,
        )
        await s.commit()
    if accepted is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "battle not acceptable")
    topic = battle_topics.get_by_key(accepted.topic_key)
    return AcceptBattleOut(
        id=accepted.id,
        topic_key=accepted.topic_key,
        topic_title_ru=topic.title_ru if topic else accepted.topic_key,
        initiator_tg_id=accepted.initiator_tg_id,
        opponent_tg_id=accepted.opponent_tg_id,
        prompt_en=accepted.prompt_en,
        side_a_ru=accepted.side_a_ru,
        side_b_ru=accepted.side_b_ru,
    )


@router.post(
    "/battles/{battle_id}/record",
    response_model=BattleStateOut,
    dependencies=[Depends(_require_bot_secret)],
)
async def api_record_battle(battle_id: int, body: RecordIn) -> BattleStateOut:
    """Mini App загрузил запись одного из участников.

    Если это последняя запись (второй участник уже записал) — тут же
    запускаем судью.
    """
    async with db_session() as s:
        ok = await battle_mod.attach_recording(
            s,
            battle_id=battle_id,
            tg_id=body.tg_id,
            audio_path=body.audio_path,
            transcript=body.transcript,
        )
        if not ok:
            await s.rollback()
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot attach recording")
        # Попытка судить если оба готовы
        await battle_mod.judge_if_ready(s, battle_id=battle_id)
        await s.commit()

    return await _load_battle_state(battle_id)


@router.get("/battles/{battle_id}", response_model=BattleStateOut)
async def api_get_battle(battle_id: int) -> BattleStateOut:
    return await _load_battle_state(battle_id)


async def _load_battle_state(battle_id: int) -> BattleStateOut:
    from .db.models import Battle
    async with db_session() as s:
        res = await s.execute(select(Battle).where(Battle.id == battle_id))
        b = res.scalar_one_or_none()
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "battle not found")
    topic = battle_topics.get_by_key(b.topic_key)
    return BattleStateOut(
        id=b.id,
        status=b.status,
        topic_key=b.topic_key,
        topic_title_ru=topic.title_ru if topic else b.topic_key,
        initiator_tg_id=b.initiator_tg_id,
        opponent_tg_id=b.opponent_tg_id,
        a_recorded=bool(b.a_audio_path),
        b_recorded=bool(b.b_audio_path),
        winner=b.winner,
        judge_comment=b.judge_comment,
        a_score=b.a_score,
        b_score=b.b_score,
    )


# ─── Quest endpoints ────────────────────────────────────────────────────

@router.post(
    "/quests/assign",
    response_model=Optional[QuestOut],
    dependencies=[Depends(_require_bot_secret)],
)
async def api_assign_quest(body: AssignQuestIn) -> Optional[QuestOut]:
    """Бот дёргает из утреннего cron: «выдай юзеру квест»."""
    async with db_session() as s:
        repo = Repo(s)
        user = await repo.get_user_by_tg_id(body.tg_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
        active = await quests_mod.assign_daily_quest(
            s, user_id=user.id, user_level=body.user_level,
        )
        await s.commit()
    if active is None:
        return None
    return QuestOut(
        key=active.key,
        title_ru=active.title_ru,
        description_ru=active.description_ru,
        reward_seconds=active.reward_seconds,
    )


@router.get("/quests/active")
async def api_get_active_quest(tg_id: int) -> Optional[QuestOut]:
    """Mini App: показать виджет с активным квестом."""
    async with db_session() as s:
        repo = Repo(s)
        user = await repo.get_user_by_tg_id(tg_id)
        if user is None:
            return None
        active = await quests_mod.get_active_quest(s, user.id)
    if active is None:
        return None
    return QuestOut(
        key=active.key,
        title_ru=active.title_ru,
        description_ru=active.description_ru,
        reward_seconds=active.reward_seconds,
    )


@router.post(
    "/quests/verify",
    response_model=VerifyQuestOut,
    dependencies=[Depends(_require_bot_secret)],
)
async def api_verify_quest(body: VerifyQuestIn) -> VerifyQuestOut:
    """Mini App: проверить квест после окончания сессии.

    Сейчас зовётся из backend voice-сессии через service-to-service.
    Возвращает результат, бот получает уведомление через webhook от backend
    (см. run_voice_session → notify_quest_completed).
    """
    async with db_session() as s:
        repo = Repo(s)
        user = await repo.get_user_by_tg_id(body.tg_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
        result = await quests_mod.verify_session(
            s,
            user_id=user.id,
            transcript=body.transcript,
            role=body.role,
            duration_sec=body.duration_sec,
        )
        await s.commit()
    return VerifyQuestOut(
        completed=result.completed,
        quest_key=result.quest_key,
        reward_seconds=result.reward_seconds,
        badge_key=result.badge_key,
    )
