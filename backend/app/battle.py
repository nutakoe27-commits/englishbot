"""Battle Mode — логика дуэлей двух юзеров в Telegram-чате.

Жизненный цикл:
  1. open      — юзер А через inline_query бросил вызов в чате, ждём соперника
  2. accepted  — юзер Б нажал «Принять», обоим ушла ссылка в Mini App
  3. recording — хотя бы один уже записал (мы в полуготовом состоянии)
  4. judged    — оба записали, ИИ-судья отработал, результат опубликован
  5. expired   — 24ч прошло без принятия (или без записи), закрыли
  6. canceled  — инициатор отменил (редкий путь)

Функции:
  - create_battle(initiator_tg_id, chat_id) — для inline_query
  - accept_battle(battle_id, opponent_tg_id) — callback «Принять»
  - attach_recording(battle_id, side, audio_path, transcript) — когда Mini
    App загрузил запись
  - judge_if_ready(battle_id) — если оба записали, зовёт LLM-судью,
    помечает judged, возвращает результат для публикации в чат
  - expire_old() — cron-задача: перевести open/accepted старше 24ч в expired
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import battle_topics
from .db.models import Battle
from .db.repo import utcnow
from .llm_providers import get_llm_provider

log = logging.getLogger(__name__)


# ─── Результаты (DTO) ──────────────────────────────────────────────────

@dataclass
class BattleCreated:
    id: int
    topic_key: str
    topic_title_ru: str


@dataclass
class BattleAccepted:
    id: int
    topic_key: str
    initiator_tg_id: int
    opponent_tg_id: int
    side_a_ru: str   # что делать юзеру A
    side_b_ru: str   # что делать юзеру B
    prompt_en: str   # инструкция на английском (одинаковая)


@dataclass
class JudgeResult:
    battle_id: int
    topic_title_ru: str
    a_tg_id: int
    b_tg_id: int
    a_score_total: int
    b_score_total: int
    a_score: dict          # {"grammar": X, "fluency": Y, "argumentation": Z}
    b_score: dict
    winner: str            # "a" | "b" | "tie"
    judge_comment: str


# ─── create / accept / attach ────────────────────────────────────────

async def create_battle(
    s: AsyncSession,
    *,
    initiator_tg_id: int,
    chat_id: Optional[int] = None,
    chat_message_id: Optional[int] = None,
    inline_message_id: Optional[str] = None,
) -> BattleCreated:
    """Создать открытый battle.

    Для inline-вызовов chat_id обычно None, вместо этого приходит inline_message_id.
    Для вызовов через /battle в личке/чате идёт chat_id и chat_message_id.
    """
    topic = battle_topics.pick_random()
    now = utcnow()
    battle = Battle(
        initiator_tg_id=initiator_tg_id,
        opponent_tg_id=None,
        chat_id=chat_id,
        chat_message_id=chat_message_id,
        inline_message_id=inline_message_id,
        topic_key=topic.key,
        status="open",
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=24),
    )
    s.add(battle)
    await s.flush()
    log.info(
        "[battle] created id=%s initiator=%s chat=%s topic=%s",
        battle.id, initiator_tg_id, chat_id, topic.key,
    )
    return BattleCreated(id=battle.id, topic_key=topic.key, topic_title_ru=topic.title_ru)


async def accept_battle(
    s: AsyncSession,
    *,
    battle_id: int,
    opponent_tg_id: int,
) -> Optional[BattleAccepted]:
    """Юзер Б нажал «Принять». Возвращает данные для отправки Mini App-ссылок.

    Отказ (None):
      - battle не найден
      - статус уже не 'open'
      - opponent совпадает с initiator
      - expired
    """
    res = await s.execute(select(Battle).where(Battle.id == battle_id))
    battle = res.scalar_one_or_none()
    if battle is None:
        log.warning("[battle] accept: not found id=%s", battle_id)
        return None
    if battle.status != "open":
        log.info("[battle] accept: wrong status id=%s status=%s", battle_id, battle.status)
        return None
    if battle.initiator_tg_id == opponent_tg_id:
        log.info("[battle] accept: self-accept blocked id=%s", battle_id)
        return None
    if battle.expires_at < utcnow():
        # Помечаем expired, чтобы следующие попытки отвалились быстро.
        battle.status = "expired"
        battle.updated_at = utcnow()
        return None

    topic = battle_topics.get_by_key(battle.topic_key)
    if topic is None:
        log.error("[battle] topic missing key=%s (battle %s)", battle.topic_key, battle_id)
        return None

    battle.opponent_tg_id = opponent_tg_id
    battle.status = "accepted"
    battle.updated_at = utcnow()
    await s.flush()

    log.info(
        "[battle] accepted id=%s initiator=%s opponent=%s topic=%s",
        battle_id, battle.initiator_tg_id, opponent_tg_id, topic.key,
    )
    return BattleAccepted(
        id=battle_id,
        topic_key=topic.key,
        initiator_tg_id=battle.initiator_tg_id,
        opponent_tg_id=opponent_tg_id,
        side_a_ru=topic.side_a_ru,
        side_b_ru=topic.side_b_ru,
        prompt_en=topic.prompt_en,
    )


async def attach_recording(
    s: AsyncSession,
    *,
    battle_id: int,
    tg_id: int,
    audio_path: str,
    transcript: str,
) -> bool:
    """Сохранить запись участника. Вернёт True если это валидный ход
    (юзер — один из участников battle, в корректном статусе).

    Сторона (A/B) определяется по tg_id.
    """
    res = await s.execute(select(Battle).where(Battle.id == battle_id))
    battle = res.scalar_one_or_none()
    if battle is None:
        return False
    if battle.status not in ("accepted", "recording"):
        log.info("[battle] attach: wrong status id=%s status=%s", battle_id, battle.status)
        return False

    if tg_id == battle.initiator_tg_id:
        side = "a"
        upd = (
            update(Battle)
            .where(
                Battle.id == battle_id,
                Battle.a_audio_path.is_(None),
                Battle.status.in_(("accepted", "recording")),
            )
            .values(
                a_audio_path=audio_path,
                a_transcript=transcript,
                status="recording",
                updated_at=utcnow(),
            )
        )
    elif tg_id == battle.opponent_tg_id:
        side = "b"
        upd = (
            update(Battle)
            .where(
                Battle.id == battle_id,
                Battle.b_audio_path.is_(None),
                Battle.status.in_(("accepted", "recording")),
            )
            .values(
                b_audio_path=audio_path,
                b_transcript=transcript,
                status="recording",
                updated_at=utcnow(),
            )
        )
    else:
        log.warning("[battle] attach: tg_id=%s not a participant in battle %s", tg_id, battle_id)
        return False

    res = await s.execute(upd)
    if res.rowcount == 0:
        # Гонка/повтор: либо запись уже была, либо статус ушёл в judged.
        log.info("[battle] attach: side %s skipped (already recorded or wrong status) id=%s", side.upper(), battle_id)
        return False
    await s.flush()
    log.info("[battle] recording attached id=%s side=%s", battle_id, side)
    return True


# ─── Судейство ─────────────────────────────────────────────────────────

_JUDGE_SYSTEM_PROMPT = """You are a brutally fair judge for a 60-second English-speaking duel.

Two people argued about the same topic. You must compare them ONLY against each other — not against some abstract ideal. Score each on 3 axes, 1–10:
- grammar: accuracy of structures, tense use, agreement
- fluency: naturalness, flow, use of everyday phrasing
- argumentation: how convincing/creative/specific their point was

Your answer must be a single valid JSON object and nothing else:
{
  "a": {"grammar": N, "fluency": N, "argumentation": N},
  "b": {"grammar": N, "fluency": N, "argumentation": N},
  "winner": "a" | "b" | "tie",
  "comment_ru": "ONE SHORT sarcastic/funny Russian line summarising the result (<= 140 chars). No pep-talk. No 'both did great'. Pick a side."
}

Rules:
- If totals are within 2 points, you may choose "tie", but try to pick a winner.
- comment_ru must be in Russian, one line, witty, sometimes a mild roast of the loser.
- NEVER include anything outside the JSON. No markdown fences, no preamble.
"""

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


async def judge_if_ready(
    s: AsyncSession,
    *,
    battle_id: int,
) -> Optional[JudgeResult]:
    """Если оба участника записали — зовём LLM-судью. Иначе None.

    При успешном судействе: помечаем status='judged', сохраняем скоры
    и comment, возвращаем результат для публикации в чат.
    """
    res = await s.execute(select(Battle).where(Battle.id == battle_id))
    battle = res.scalar_one_or_none()
    if battle is None:
        return None
    if battle.status == "judged":
        # Идемпотентность — уже отсудили, возвращаем cached
        return _battle_to_judge_result(battle)
    if battle.status != "recording":
        return None
    if not (battle.a_transcript and battle.b_transcript):
        return None

    topic = battle_topics.get_by_key(battle.topic_key)
    topic_title = topic.title_ru if topic else battle.topic_key
    judging_hint = topic.judging_hint if topic else ""

    user_text = (
        f"TOPIC: {topic_title}\n"
        f"JUDGING HINT: {judging_hint}\n\n"
        f"PLAYER A (tg_id={battle.initiator_tg_id}):\n{battle.a_transcript}\n\n"
        f"PLAYER B (tg_id={battle.opponent_tg_id}):\n{battle.b_transcript}\n\n"
        f"Return the JSON now. No preamble."
    )

    try:
        llm = get_llm_provider()
        raw = await llm.complete(
            user_text=user_text,
            history=[],
            system_prompt=_JUDGE_SYSTEM_PROMPT,
        )
    except Exception as exc:
        log.error("[battle] judge LLM failed for id=%s: %s", battle_id, exc)
        return None

    parsed = _parse_judge_json(raw)
    if parsed is None:
        log.error("[battle] judge JSON parse failed id=%s raw=%r", battle_id, raw[:500])
        # Fallback: считаем длину как ничью, чтобы не блокировать battle навсегда
        parsed = _fallback_judge()

    a_score = _clean_score(parsed.get("a", {}))
    b_score = _clean_score(parsed.get("b", {}))
    winner = parsed.get("winner", "tie")
    if winner not in ("a", "b", "tie"):
        winner = "tie"
    comment = (parsed.get("comment_ru") or "").strip()[:400] or "Судья затрудняется выбрать."

    battle.a_score = a_score
    battle.b_score = b_score
    battle.winner = winner
    battle.judge_comment = comment
    battle.status = "judged"
    battle.updated_at = utcnow()
    await s.flush()

    log.info(
        "[battle] judged id=%s winner=%s a=%s b=%s",
        battle_id, winner, a_score, b_score,
    )
    return _battle_to_judge_result(battle)


def _clean_score(d: dict) -> dict:
    """Нормализуем дикт: убеждаемся, что 3 ключа с int 1..10."""
    out = {}
    for key in ("grammar", "fluency", "argumentation"):
        try:
            v = int(d.get(key, 5))
        except (TypeError, ValueError):
            v = 5
        v = max(1, min(10, v))
        out[key] = v
    return out


def _fallback_judge() -> dict:
    return {
        "a": {"grammar": 5, "fluency": 5, "argumentation": 5},
        "b": {"grammar": 5, "fluency": 5, "argumentation": 5},
        "winner": "tie",
        "comment_ru": "Техническая ничья — судья слегка завис.",
    }


def _parse_judge_json(raw: str) -> Optional[dict]:
    """Пытаемся распарсить JSON. Если LLM обернул в кодовый блок — чистим."""
    text = raw.strip()
    # Snip ```json ... ```
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _battle_to_judge_result(b: Battle) -> JudgeResult:
    topic = battle_topics.get_by_key(b.topic_key)
    a = b.a_score or {}
    bb = b.b_score or {}
    return JudgeResult(
        battle_id=b.id,
        topic_title_ru=topic.title_ru if topic else b.topic_key,
        a_tg_id=b.initiator_tg_id,
        b_tg_id=b.opponent_tg_id or 0,
        a_score=a,
        b_score=bb,
        a_score_total=sum(a.values()) if a else 0,
        b_score_total=sum(bb.values()) if bb else 0,
        winner=b.winner or "tie",
        judge_comment=b.judge_comment or "",
    )


# ─── Expire cron ────────────────────────────────────────────────────────

async def expire_old(s: AsyncSession) -> int:
    """Закрыть battle'ы, которые просрочили 24ч. Возвращает число закрытых."""
    now = utcnow()
    res = await s.execute(
        update(Battle)
        .where(
            Battle.status.in_(("open", "accepted", "recording")),
            Battle.expires_at < now,
        )
        .values(status="expired", updated_at=now)
    )
    count = res.rowcount or 0
    if count:
        log.info("[battle] expired %d old battles", count)
    return count


# ─── Рендер результата для публикации в чат ───────────────────────────

def render_judge_message(r: JudgeResult, *, a_name: str, b_name: str) -> str:
    """Отформатированное сообщение для публикации в исходный чат.

    HTML-режим. Без эмодзи-мусора — просто красиво.
    """
    def _fmt_score(s: dict) -> str:
        if not s:
            return "—"
        return (
            f"gram {s.get('grammar','?')}/10 · "
            f"flu {s.get('fluency','?')}/10 · "
            f"arg {s.get('argumentation','?')}/10"
        )

    a_total = r.a_score_total
    b_total = r.b_score_total

    if r.winner == "a":
        headline = f"🏆 Победил {a_name}"
    elif r.winner == "b":
        headline = f"🏆 Победил {b_name}"
    else:
        headline = "🤝 Ничья"

    return (
        f"<b>⚔️ Battle #{r.battle_id} — {r.topic_title_ru}</b>\n"
        f"{headline}\n\n"
        f"<b>{a_name}</b> — {a_total}/30\n"
        f"<i>{_fmt_score(r.a_score)}</i>\n\n"
        f"<b>{b_name}</b> — {b_total}/30\n"
        f"<i>{_fmt_score(r.b_score)}</i>\n\n"
        f"💬 <i>{r.judge_comment}</i>"
    )
