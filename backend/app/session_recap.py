"""Hook после голосовой сессии: LLM-анализ транскрипта → vocabulary + mistakes.

Зовётся из voice.py finally параллельно с _check_quest_after_session. Цель:
- Вытащить из ассистентских реплик 5–7 полезных слов/фраз B1+ (новый словарь
  юзера будет показан в next session prompt и в post-session summary).
- Вытащить до 5 ошибок учащегося, на которые тьютор обратил внимание
  (с категорией: article/tense/preposition/word_choice/phrasal/other).

Записываем в user_vocabulary (UPSERT по user_id+word) и user_mistakes (INSERT).
Если LLM не смог распарсить (не вернул JSON / маленький транскрипт) — тихо
выходим: это best-effort фича, фейл не должен ломать сессию.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .db.models import UserMistake, UserVocabulary
from .db.repo import utcnow
from .llm_providers import get_llm_provider


log = logging.getLogger(__name__)


_RECAP_SYSTEM_PROMPT = """You analyze a short English-learning conversation between a tutor and a learner. Return ONE JSON object and nothing else, in this exact shape:

{
  "new_words": ["word_or_phrase", ...],
  "mistakes": [
    {"category": "article|tense|preposition|word_choice|phrasal|other",
     "bad": "what the learner said",
     "good": "the corrected version"},
    ...
  ]
}

Rules:
- "new_words": up to 7 USEFUL B1+ words or short phrases the TUTOR introduced (not function words like "the", "is"). Lowercased, in their base form. Only meaningful items the learner could realistically reuse — skip generic stuff.
- "mistakes": up to 5 ERRORS the LEARNER made that were worth correcting. Use a SHORT bad/good pair (each ≤ 60 chars). Pick distinct categories. If there are no clear mistakes, use an empty list.
- If the transcript is too short or empty, return {"new_words": [], "mistakes": []}.
- NEVER include anything outside the JSON. No markdown fences, no preamble.
"""

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

_VALID_CATEGORIES = {"article", "tense", "preposition", "word_choice", "phrasal", "other"}


def _parse_recap_json(raw: str) -> Optional[dict]:
    """Парсим JSON, терпимо к ```json ... ``` обёрткам и хвостам."""
    text = (raw or "").strip()
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


def _coerce_words(value) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        word = item.strip().lower()
        if not word or len(word) > 64:
            continue
        if word in seen:
            continue
        seen.add(word)
        out.append(word)
        if len(out) >= 7:
            break
    return out


def _coerce_mistakes(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        cat = str(item.get("category") or "").strip().lower()
        if cat not in _VALID_CATEGORIES:
            cat = "other"
        bad = str(item.get("bad") or "").strip()
        good = str(item.get("good") or "").strip()
        if not bad or not good:
            continue
        if len(bad) > 255 or len(good) > 255:
            bad = bad[:255]
            good = good[:255]
        out.append({"category": cat, "bad": bad, "good": good})
        if len(out) >= 5:
            break
    return out


def _build_transcript(history: list[dict]) -> str:
    """Превращает history (list of {role, text}) в компактную форму для LLM."""
    lines: list[str] = []
    for turn in history:
        role = turn.get("role") or "user"
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        # Тьютор = "assistant" (как в OpenAI-формате). Mapping для LLM понятности:
        speaker = "Tutor" if role == "assistant" else "Learner"
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


async def capture_session_recap(
    s: AsyncSession,
    *,
    user_db_id: int,
    history: list[dict],
) -> dict:
    """Прогнать сессию через LLM, записать словарь и ошибки в БД.

    Возвращает {new_words, mistakes} (для отладки/тестов). Никогда не кидает —
    при любой ошибке логируем и возвращаем пустой dict.
    """
    if not history:
        return {"new_words": [], "mistakes": []}

    transcript = _build_transcript(history)
    if len(transcript) < 30:
        # Слишком короткая сессия — нечего анализировать.
        return {"new_words": [], "mistakes": []}

    try:
        llm = get_llm_provider()
        raw = await llm.complete(
            user_text=transcript,
            history=[],
            system_prompt=_RECAP_SYSTEM_PROMPT,
        )
    except Exception as exc:
        log.warning("[recap] LLM call failed: %s", exc)
        return {"new_words": [], "mistakes": []}

    parsed = _parse_recap_json(raw)
    if parsed is None:
        log.warning("[recap] cannot parse LLM JSON; raw=%r", (raw or "")[:300])
        return {"new_words": [], "mistakes": []}

    new_words = _coerce_words(parsed.get("new_words"))
    mistakes = _coerce_mistakes(parsed.get("mistakes"))

    now = utcnow()

    # Vocabulary: UPSERT (last_seen, times_used). Контекст пишем только при INSERT.
    # ВАЖНО: source НЕ указываем в values() и НЕ затираем в on_duplicate_key_update.
    # При INSERT новой row source примет default 'tutor' (миграция 0006). При
    # UPSERT существующей row source остаётся как был — если юзер уже добавил
    # это слово вручную (source='user'), tutor-капчура не должна откатывать
    # его обратно в 'tutor'.
    for word in new_words:
        try:
            stmt = mysql_insert(UserVocabulary).values(
                user_id=user_db_id,
                word=word,
                first_seen_at=now,
                last_seen_at=now,
                times_used=1,
                context=None,
            )
            stmt = stmt.on_duplicate_key_update(
                last_seen_at=now,
                times_used=UserVocabulary.times_used + 1,
            )
            await s.execute(stmt)
        except Exception as exc:
            log.warning("[recap] vocabulary upsert failed for %r: %s", word, exc)

    # Mistakes: INSERT (никогда не дедуплицируем; история = ценность).
    for m in mistakes:
        try:
            s.add(UserMistake(
                user_id=user_db_id,
                category=m["category"],
                bad_phrase=m["bad"],
                good_phrase=m["good"],
                occurred_at=now,
            ))
        except Exception as exc:
            log.warning("[recap] mistake insert failed %r: %s", m, exc)

    await s.flush()
    log.info(
        "[recap] user_db_id=%s words=%d mistakes=%d",
        user_db_id, len(new_words), len(mistakes),
    )
    return {"new_words": new_words, "mistakes": mistakes}
