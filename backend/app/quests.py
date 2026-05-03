"""Daily Quest — ежедневные задания с проверкой по транскрипту сессии.

Основные операции:
  - assign_daily_quest(tg_id) — выдать юзеру новый квест, если активного нет
  - get_active_quest(user_id) — текущий невыполненный квест
  - verify_session(user_id, transcript, role, duration_sec) — после каждой
    сессии Mini App зовёт эту функцию. Если активный квест удовлетворён —
    помечаем completed + начисляем bonus_seconds в daily_usage.

Правила проверки описаны JSON-ом в quests_catalog.verification_rule:
  lexical:  {"kind": "word_count",     "word": "...",        "min": N}
  grammar:  {"kind": "grammar_pattern","pattern": "past_perfect", "min": N}
            {"kind": "phrasal_verbs",  "min": N}
            {"kind": "question_count", "min": N}
  role:     {"kind": "role_time",      "role": "barista",    "min_seconds": 300}

Эвристики грамматики намеренно простые — мы не претендуем на лингвистическую
точность, а ловим 80%+ случаев. Если юзер чуть «не попал», завтра будет
другой квест.
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .db.models import DailyUsage, QuestCatalog, User, UserQuest
from .db.repo import msk_today, utcnow

log = logging.getLogger(__name__)


# ─── Эвристики проверки по транскрипту ────────────────────────────────

# Частотные фразовые глаголы. Проверяем целиком (word boundary).
_PHRASAL_VERBS = [
    "look up", "look for", "look after", "look into", "look forward",
    "give up", "give back", "give in", "give away",
    "run into", "run out", "run away",
    "pick up", "pick out",
    "put on", "put off", "put up with", "put down",
    "take off", "take on", "take over", "take out",
    "get up", "get over", "get along", "get by", "get rid of",
    "come up", "come across", "come back", "come down",
    "go on", "go off", "go through", "go over",
    "turn on", "turn off", "turn down", "turn up", "turn out",
    "break down", "break up", "break into",
    "bring up", "bring back", "bring about",
    "figure out", "find out", "hang out", "hang up",
    "set up", "show up", "wake up", "work out",
]


def _count_word(text: str, word: str) -> int:
    """Считаем вхождения слова (word boundary, case-insensitive)."""
    pattern = r"\b" + re.escape(word) + r"\b"
    return len(re.findall(pattern, text, flags=re.IGNORECASE))


def _count_past_perfect(text: str) -> int:
    """'had' + глагол на -ed / V3 (had been, had gone, had worked...).
    Ложных срабатываний терпим (had + любой глагол через пробел).
    """
    # (?:hadn't|had not|had) + пробел + слово из >=2 букв
    pattern = r"\bhad(?:n't| not)?\s+[a-z]{2,}\b"
    return len(re.findall(pattern, text, flags=re.IGNORECASE))


def _count_present_perfect(text: str) -> int:
    """have/has/haven't/hasn't + глагол."""
    pattern = r"\b(?:have|has|haven't|hasn't)\s+[a-z]{2,}\b"
    # минус "have to" / "has to" — это не Present Perfect
    raw = re.findall(pattern, text, flags=re.IGNORECASE)
    return sum(1 for m in raw if not m.lower().endswith(" to"))


def _count_conditionals(text: str) -> int:
    """If ... would/could/might | If I were | If I had..."""
    patterns = [
        r"\bif\s+\w+\s+(?:would|could|might)\b",
        r"\bif\s+i\s+were\b",
        r"\bif\s+\w+\s+had\s+\w+",
    ]
    total = 0
    for p in patterns:
        total += len(re.findall(p, text, flags=re.IGNORECASE))
    return total


def _count_used_to(text: str) -> int:
    """'used to' как маркер прошлой привычки."""
    return len(re.findall(r"\bused\s+to\s+[a-z]{2,}\b", text, flags=re.IGNORECASE))


def _count_phrasal_verbs(text: str) -> int:
    """Сколько РАЗНЫХ фразовых глаголов нашли (не общее число)."""
    found: set[str] = set()
    lower = text.lower()
    for pv in _PHRASAL_VERBS:
        pattern = r"\b" + re.escape(pv) + r"\b"
        if re.search(pattern, lower):
            found.add(pv)
    return len(found)


def _count_questions(text: str) -> int:
    """Считаем вопросительные знаки (упрощённо)."""
    return text.count("?")


# ─── Публичный API ─────────────────────────────────────────────────────

@dataclass
class ActiveQuest:
    """Снимок активного квеста для отображения юзеру."""
    key: str
    title_ru: str
    description_ru: str
    reward_seconds: int
    assigned_at: datetime

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "title_ru": self.title_ru,
            "description_ru": self.description_ru,
            "reward_seconds": self.reward_seconds,
            "assigned_at": self.assigned_at.isoformat(),
        }


@dataclass
class QuestVerificationResult:
    """Что вернула verify_session."""
    completed: bool
    quest_key: Optional[str]  # какой квест проверяли (если был активный)
    reward_seconds: int       # сколько бонуса начислили (0 если не выполнен)
    badge_key: Optional[str]  # какой значок заработал
    debug: dict               # для логов

    def to_dict(self) -> dict:
        return {
            "completed": self.completed,
            "quest_key": self.quest_key,
            "reward_seconds": self.reward_seconds,
            "badge_key": self.badge_key,
        }


async def get_active_quest(s: AsyncSession, user_id: int) -> Optional[ActiveQuest]:
    """Текущий невыполненный и не истёкший квест юзера."""
    # Берём самый свежий user_quest без completed_at и без expired_at
    res = await s.execute(
        select(UserQuest, QuestCatalog)
        .join(QuestCatalog, QuestCatalog.key == UserQuest.quest_key)
        .where(
            UserQuest.user_id == user_id,
            UserQuest.completed_at.is_(None),
            UserQuest.expired_at.is_(None),
        )
        .order_by(UserQuest.assigned_at.desc())
        .limit(1)
    )
    row = res.first()
    if row is None:
        return None
    uq, qc = row
    return ActiveQuest(
        key=qc.key,
        title_ru=qc.title_ru,
        description_ru=qc.description_ru,
        reward_seconds=qc.reward_seconds,
        assigned_at=uq.assigned_at,
    )


async def assign_daily_quest(
    s: AsyncSession,
    *,
    user_id: int,
    user_level: Optional[str] = None,
) -> Optional[ActiveQuest]:
    """Выдать юзеру новый квест, если нет активного.

    Логика подбора:
      1. Сначала expire'им любые висящие квесты старше 24ч (completed_at IS NULL,
         expired_at IS NULL, assigned_at < now-24h). Без этого шаг 2 видел бы
         старый зависший квест и навсегда возвращал его.
      2. Если уже есть свежий активный (не completed, не expired) — вернуть его.
      3. Исключить квесты, которые юзер уже выполнил.
      4. Фильтр по уровню: совпадающий или any.
      5. Случайный из оставшихся.

    Возвращает ActiveQuest, либо None если пул исчерпан.
    """
    # 1) Просрочиваем висящие старше 24ч.
    cutoff = utcnow() - timedelta(hours=24)
    await s.execute(
        update(UserQuest)
        .where(
            UserQuest.user_id == user_id,
            UserQuest.completed_at.is_(None),
            UserQuest.expired_at.is_(None),
            UserQuest.assigned_at < cutoff,
        )
        .values(expired_at=utcnow())
    )

    # 2) Свежий активный — отдаём как есть.
    existing = await get_active_quest(s, user_id)
    if existing is not None:
        return existing

    # 3) Список ключей, которые юзер УЖЕ выполнил
    done_res = await s.execute(
        select(UserQuest.quest_key).where(
            UserQuest.user_id == user_id,
            UserQuest.completed_at.is_not(None),
        )
    )
    done_keys = {row[0] for row in done_res.all()}

    # 4) Кандидаты из каталога
    level_filter = [QuestCatalog.target_level == "any"]
    if user_level in ("A2", "B1", "B2", "C1"):
        level_filter.append(QuestCatalog.target_level == user_level)

    from sqlalchemy import or_

    cand_res = await s.execute(
        select(QuestCatalog).where(
            QuestCatalog.is_active.is_(True),
            or_(*level_filter),
        )
    )
    candidates = [c for c in cand_res.scalars().all() if c.key not in done_keys]

    if not candidates:
        log.info("[quests] user_id=%s — пул квестов исчерпан", user_id)
        return None

    chosen = random.choice(candidates)
    now = utcnow()
    stmt = mysql_insert(UserQuest).values(
        user_id=user_id,
        quest_key=chosen.key,
        assigned_at=now,
    )
    # UNIQUE(user_id, quest_key): если эту пару уже выдавали (и она была expired),
    # «оживляем» row — иначе у неё остаётся expired_at и get_active_quest её не
    # увидит, юзер останется без активного квеста.
    stmt = stmt.on_duplicate_key_update(
        assigned_at=now,
        expired_at=None,
        verification_data=None,
    )
    await s.execute(stmt)
    await s.flush()

    log.info("[quests] assigned user_id=%s quest=%s", user_id, chosen.key)
    return ActiveQuest(
        key=chosen.key,
        title_ru=chosen.title_ru,
        description_ru=chosen.description_ru,
        reward_seconds=chosen.reward_seconds,
        assigned_at=now,
    )


def _check_rule(
    rule: dict,
    *,
    transcript: str,
    role: Optional[str],
    duration_sec: int,
) -> tuple[bool, dict]:
    """Чистая проверка правила по данным сессии. Возвращает (passed, debug)."""
    kind = rule.get("kind")

    if kind == "word_count":
        word = rule["word"]
        needed = int(rule["min"])
        found = _count_word(transcript, word)
        return found >= needed, {"kind": kind, "word": word, "needed": needed, "found": found}

    if kind == "grammar_pattern":
        pattern = rule["pattern"]
        needed = int(rule["min"])
        fn = {
            "past_perfect":    _count_past_perfect,
            "present_perfect": _count_present_perfect,
            "conditionals":    _count_conditionals,
            "used_to":         _count_used_to,
        }.get(pattern)
        if fn is None:
            return False, {"kind": kind, "error": f"unknown pattern {pattern}"}
        found = fn(transcript)
        return found >= needed, {"kind": kind, "pattern": pattern, "needed": needed, "found": found}

    if kind == "phrasal_verbs":
        needed = int(rule["min"])
        found = _count_phrasal_verbs(transcript)
        return found >= needed, {"kind": kind, "needed": needed, "found": found}

    if kind == "question_count":
        needed = int(rule["min"])
        found = _count_questions(transcript)
        return found >= needed, {"kind": kind, "needed": needed, "found": found}

    if kind == "role_time":
        needed_role = rule["role"]
        needed_sec = int(rule["min_seconds"])
        matches_role = role == needed_role
        long_enough = duration_sec >= needed_sec
        passed = matches_role and long_enough
        return passed, {
            "kind": kind, "needed_role": needed_role, "role": role,
            "needed_seconds": needed_sec, "duration": duration_sec,
        }

    return False, {"kind": kind, "error": "unknown kind"}


async def verify_session(
    s: AsyncSession,
    *,
    user_id: int,
    transcript: str,
    role: Optional[str],
    duration_sec: int,
) -> QuestVerificationResult:
    """Вызвать после завершения Mini App сессии.

    Если активный квест выполнен — помечаем completed, начисляем bonus_seconds,
    возвращаем reward+badge. Иначе — просто обновляем verification_data (чтобы
    юзер видел прогресс если Mini App это показывает).
    """
    active = await get_active_quest(s, user_id)
    if active is None:
        return QuestVerificationResult(False, None, 0, None, {"reason": "no_active_quest"})

    # Тянем правило
    res = await s.execute(
        select(QuestCatalog).where(QuestCatalog.key == active.key)
    )
    quest = res.scalar_one_or_none()
    if quest is None:
        log.error("[quests] catalog miss for key=%s", active.key)
        return QuestVerificationResult(False, active.key, 0, None, {"reason": "catalog_miss"})

    passed, debug = _check_rule(
        quest.verification_rule,
        transcript=transcript or "",
        role=role,
        duration_sec=duration_sec,
    )

    now = utcnow()

    if not passed:
        # Обновляем только verification_data (для UI-прогресса)
        await s.execute(
            update(UserQuest)
            .where(
                UserQuest.user_id == user_id,
                UserQuest.quest_key == active.key,
            )
            .values(verification_data=debug)
        )
        return QuestVerificationResult(False, active.key, 0, None, debug)

    # Выполнено. Помечаем completed + начисляем bonus_seconds
    await s.execute(
        update(UserQuest)
        .where(
            UserQuest.user_id == user_id,
            UserQuest.quest_key == active.key,
        )
        .values(completed_at=now, verification_data=debug)
    )

    # Добавляем bonus_seconds в daily_usage (сегодняшний день МСК)
    today = msk_today()
    reward = quest.reward_seconds
    stmt = mysql_insert(DailyUsage).values(
        user_id=user_id,
        usage_date=today,
        used_seconds=0,
        bonus_seconds=reward,
        updated_at=now,
    )
    stmt = stmt.on_duplicate_key_update(
        bonus_seconds=DailyUsage.bonus_seconds + reward,
        updated_at=now,
    )
    await s.execute(stmt)

    log.info(
        "[quests] COMPLETED user_id=%s quest=%s reward=%ds badge=%s",
        user_id, active.key, reward, quest.badge_key,
    )

    return QuestVerificationResult(
        completed=True,
        quest_key=active.key,
        reward_seconds=reward,
        badge_key=quest.badge_key,
        debug=debug,
    )
