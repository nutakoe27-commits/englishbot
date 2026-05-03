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

import asyncio
import json
import logging
import os
import pathlib
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import battle as battle_mod
from . import battle_topics
from . import quests as quests_mod
from .config import settings
from .db import db_session
from .db.models import Battle, User
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


def _validate_init_data_and_get_tg_id(init_data: str) -> int:
    """Проверяет Telegram WebApp initData и возвращает tg_id юзера.

    Кидает HTTPException(401) при любой ошибке.
    """
    # Импорт тут, чтобы не ловить circular import
    from .main import validate_telegram_init_data

    if not init_data:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "init_data required")
    if not settings.BOT_TOKEN:
        log.error("BOT_TOKEN not set — cannot validate initData")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "bot token missing")
    validated = validate_telegram_init_data(init_data, settings.BOT_TOKEN)
    if not validated:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid initData")
    # Anti-replay: Telegram WebApp initData подписан, но не имеет TTL.
    # Принимаем подпись не старше 24 часов — это типовое окно, в котором
    # WebApp реально живёт у юзера в Telegram.
    try:
        auth_date = int(validated.get("auth_date") or 0)
    except (TypeError, ValueError):
        auth_date = 0
    if auth_date == 0 or (time.time() - auth_date) > 24 * 3600:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "init_data expired")
    user_raw = validated.get("user")
    if not user_raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "initData has no user")
    try:
        user_obj = json.loads(user_raw)
        return int(user_obj["id"])
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad user payload in initData")


# ─── Pydantic модели ────────────────────────────────────────────────────

class CreateBattleIn(BaseModel):
    initiator_tg_id: int
    chat_id: Optional[int] = None
    chat_message_id: Optional[int] = None
    inline_message_id: Optional[str] = None


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
    # Человекочитаемые имена участников (для Mini App и любых UI-поверхностей).
    # Формат: "@username" если есть username, иначе "First Last", иначе "Player <tg_id>".
    initiator_name: Optional[str] = None
    opponent_name: Optional[str] = None
    a_recorded: bool
    b_recorded: bool
    winner: Optional[str]
    judge_comment: Optional[str]
    a_score: Optional[dict]
    b_score: Optional[dict]
    # Поля, локальные для текущего юзера (для Mini App). Заполняются
    # только в state-miniapp/record-miniapp (там есть идентичность через initData).
    my_side: Optional[str] = None  # "a" | "b" | None
    prompt_en: Optional[str] = None
    side_ru: Optional[str] = None
    # Удобство для Mini App: с точки зрения текущего участника.
    my_recorded: bool = False
    other_recorded: bool = False
    side_a_ru: Optional[str] = None
    side_b_ru: Optional[str] = None
    # Мета для бота: куда отправить результат после судьи.
    chat_id: Optional[int] = None
    chat_message_id: Optional[int] = None
    inline_message_id: Optional[str] = None


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
            inline_message_id=body.inline_message_id,
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
    "/battles/{battle_id}/revert-accept",
    dependencies=[Depends(_require_bot_secret)],
)
async def api_revert_accept(battle_id: int, body: AcceptBattleIn) -> dict:
    """Бот зовёт сюда, если не смог доставить ЛС оппоненту: возвращаем
    battle в статус 'open', чтобы оппонент мог нажать «Принять» снова после /start.
    """
    async with db_session() as s:
        ok = await battle_mod.revert_accept(
            s, battle_id=battle_id, opponent_tg_id=body.opponent_tg_id,
        )
        await s.commit()
    if not ok:
        raise HTTPException(status.HTTP_409_CONFLICT, "cannot revert accept")
    return {"ok": True}


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


def _format_user_display_name(user: Optional[User], tg_id: Optional[int]) -> Optional[str]:
    """Красивое имя пользователя для UI: @username > First Last > Player <tg_id>."""
    if user is None:
        if tg_id:
            return f"Player {tg_id}"
        return None
    if user.username:
        return f"@{user.username}"
    parts = [p for p in (user.first_name, user.last_name) if p]
    if parts:
        return " ".join(parts)
    return f"Player {user.tg_id}"


async def _load_battle_state(battle_id: int, *, viewer_tg_id: Optional[int] = None) -> BattleStateOut:
    async with db_session() as s:
        res = await s.execute(select(Battle).where(Battle.id == battle_id))
        b = res.scalar_one_or_none()
        if b is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "battle not found")
        # Тянем профили участников одним запросом (может вернуть 1 или 2 строки).
        tg_ids = [b.initiator_tg_id]
        if b.opponent_tg_id:
            tg_ids.append(b.opponent_tg_id)
        users_res = await s.execute(select(User).where(User.tg_id.in_(tg_ids)))
        users_by_tg = {u.tg_id: u for u in users_res.scalars().all()}
    topic = battle_topics.get_by_key(b.topic_key)
    initiator_name = _format_user_display_name(
        users_by_tg.get(b.initiator_tg_id), b.initiator_tg_id,
    )
    opponent_name = _format_user_display_name(
        users_by_tg.get(b.opponent_tg_id) if b.opponent_tg_id else None,
        b.opponent_tg_id,
    )

    my_side: Optional[str] = None
    side_ru: Optional[str] = None
    prompt_en: Optional[str] = None
    if viewer_tg_id is not None:
        if viewer_tg_id == b.initiator_tg_id:
            my_side = "a"
            if topic:
                side_ru = topic.side_a_ru
                prompt_en = topic.prompt_en
        elif viewer_tg_id == b.opponent_tg_id:
            my_side = "b"
            if topic:
                side_ru = topic.side_b_ru
                prompt_en = topic.prompt_en

    a_rec = bool(b.a_audio_path)
    b_rec = bool(b.b_audio_path)
    my_rec = (my_side == "a" and a_rec) or (my_side == "b" and b_rec)
    other_rec = (my_side == "a" and b_rec) or (my_side == "b" and a_rec)

    return BattleStateOut(
        id=b.id,
        status=b.status,
        topic_key=b.topic_key,
        topic_title_ru=topic.title_ru if topic else b.topic_key,
        initiator_tg_id=b.initiator_tg_id,
        opponent_tg_id=b.opponent_tg_id,
        initiator_name=initiator_name,
        opponent_name=opponent_name,
        a_recorded=a_rec,
        b_recorded=b_rec,
        winner=b.winner,
        judge_comment=b.judge_comment,
        a_score=b.a_score,
        b_score=b.b_score,
        my_side=my_side,
        side_ru=side_ru,
        prompt_en=prompt_en,
        my_recorded=my_rec,
        other_recorded=other_rec,
        side_a_ru=topic.side_a_ru if topic else None,
        side_b_ru=topic.side_b_ru if topic else None,
        chat_id=b.chat_id,
        chat_message_id=b.chat_message_id,
        inline_message_id=b.inline_message_id,
    )


# ─── Mini App endpoints (валидация через initData) ────────────────────

_BATTLE_AUDIO_DIR = pathlib.Path("/app/data/battles")


def _looks_like_english(text: str) -> bool:
    """Эвристика: True если текст похож на английский.

    - Нет кириллицы / иврита / арабского / CJK / hangul.
    - Доля латинских букв среди всех букв ≥ 0.6.
    - Есть хотя бы одно "слово" (2+ latin-букв подряд) — отсекает "1, 2, 3".
    """
    if not text:
        return False
    for ch in text:
        cp = ord(ch)
        if (
            0x0400 <= cp <= 0x052F or   # Cyrillic + supplement
            0x0590 <= cp <= 0x05FF or   # Hebrew
            0x0600 <= cp <= 0x06FF or   # Arabic
            0x4E00 <= cp <= 0x9FFF or   # CJK Unified Ideographs
            0x3040 <= cp <= 0x30FF or   # Hiragana + Katakana
            0xAC00 <= cp <= 0xD7AF      # Hangul
        ):
            return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    latin = sum(1 for c in letters if "a" <= c.lower() <= "z")
    if latin / len(letters) < 0.6:
        return False
    import re as _re
    if not _re.search(r"[a-zA-Z]{2,}", text):
        return False
    return True


@router.get("/battles/{battle_id}/state-miniapp", response_model=BattleStateOut)
async def api_get_battle_state_miniapp(
    battle_id: int,
    init_data: str = "",
) -> BattleStateOut:
    """Mini App читает состояние battle с точки зрения текущего юзера.

    Возвращает my_side/side_ru/prompt_en + общее состояние (счёт, судья).
    Поле my_side = None если юзер не участник этого battle.
    """
    tg_id = _validate_init_data_and_get_tg_id(init_data)
    return await _load_battle_state(battle_id, viewer_tg_id=tg_id)


@router.post("/battles/{battle_id}/record-miniapp", response_model=BattleStateOut)
async def api_record_battle_miniapp(
    battle_id: int,
    init_data: str = Form(...),
    audio: UploadFile = File(...),
) -> BattleStateOut:
    """Mini App заливает файл записи (обычно audio/webm;opus).

    1) Валидируем initData, получаем tg_id.
    2) Сохраняем файл в /app/data/battles/<id>_<side>.<ext>.
    3) Транскрибируем через STT.
    4) attach_recording + judge_if_ready.
    5) Если судья отработал — просим бота опубликовать результат.
    """
    from .stt_file import transcribe_file

    tg_id = _validate_init_data_and_get_tg_id(init_data)

    # Определяем сторону из battle.
    async with db_session() as s:
        res = await s.execute(select(Battle).where(Battle.id == battle_id))
        b = res.scalar_one_or_none()
        if b is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "battle not found")
        if tg_id == b.initiator_tg_id:
            side = "a"
        elif tg_id == b.opponent_tg_id:
            side = "b"
        else:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not a participant")

    # Сохраняем файл. Расширение — из content_type (.webm/.ogg/.mp3).
    _BATTLE_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    ext = "webm"
    if audio.content_type:
        if "ogg" in audio.content_type:
            ext = "ogg"
        elif "mpeg" in audio.content_type or "mp3" in audio.content_type:
            ext = "mp3"
        elif "mp4" in audio.content_type or "m4a" in audio.content_type:
            ext = "m4a"
    audio_path = _BATTLE_AUDIO_DIR / f"{battle_id}_{side}_{uuid.uuid4().hex[:8]}.{ext}"
    content = await audio.read()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty audio")
    audio_path.write_bytes(content)
    log.info("[battle_api] saved recording battle=%s side=%s bytes=%d path=%s",
             battle_id, side, len(content), audio_path)

    # Транскрипция.
    try:
        transcript = await transcribe_file(str(audio_path), language="en")
    except Exception as exc:
        log.error("[battle_api] STT failed battle=%s: %s", battle_id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"STT failed: {exc}")
    if not transcript.strip():
        # Тишина / не распозналось: отклоняем с понятной ошибкой (аудио не сохраняем в battle)
        log.warning("[battle_api] empty transcript battle=%s side=%s", battle_id, side)
        try:
            audio_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "no_speech: не удалось распознать речь. Говори громче и чётче, попробуй ещё раз.",
        )

    # Проверка языка: Battle Mode — только английский. Отклоняем если в тексте
    # есть кириллица или доля латинских букв < 0.6.
    if not _looks_like_english(transcript):
        log.warning(
            "[battle_api] non-English transcript battle=%s side=%s text=%r",
            battle_id, side, transcript[:200],
        )
        try:
            audio_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "non_english: в Battle Mode ответ принимается только на английском. Попробуй записать заново.",
        )

    # attach + judge.
    async with db_session() as s:
        ok = await battle_mod.attach_recording(
            s,
            battle_id=battle_id,
            tg_id=tg_id,
            audio_path=str(audio_path),
            transcript=transcript,
        )
        if not ok:
            await s.rollback()
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot attach recording")
        judge_result = await battle_mod.judge_if_ready(s, battle_id=battle_id)
        await s.commit()

    # Если судья отработал — уведомляем бот (fire-and-forget).
    if judge_result is not None:
        asyncio.create_task(_notify_bot_battle_judged(battle_id))

    return await _load_battle_state(battle_id, viewer_tg_id=tg_id)


async def _notify_bot_battle_judged(battle_id: int) -> None:
    """Дёргаем внутренний endpoint бота, чтобы он опубликовал результат.

    Бот сам дотянет имена и сделает edit_message_text.
    """
    import httpx

    bot_url = os.getenv("BOT_INTERNAL_URL", "http://bot:8080").rstrip("/")
    secret = os.getenv("BACKEND_BOT_SECRET", "").strip()
    if not secret:
        log.error("BACKEND_BOT_SECRET not set — cannot notify bot")
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(
                f"{bot_url}/internal/battle-judged",
                json={"battle_id": battle_id},
                headers={"X-Bot-Secret": secret},
            )
            if r.status_code >= 400:
                log.warning("[battle_api] bot notify returned %s: %s", r.status_code, r.text[:200])
    except Exception as exc:
        log.error("[battle_api] bot notify failed: %s", exc)


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
