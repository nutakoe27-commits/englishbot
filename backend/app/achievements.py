"""Каталог медалей (retention v1) + логика проверки/награждения.

Идея: после каждой сессии (в voice.py finally) дёргаем check_and_award.
Идём по статичному каталогу ниже, для каждой невзятой медали проверяем
текущее значение метрики юзера, если оно >= target — INSERT в
user_achievements и (опционально) push в TG.

Метрики читаются батчем в collect_user_metrics — один-два SQL вместо
десяти, по одному на каждую медаль.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .db.models import (
    Battle,
    Session as SessionRow,
    User,
    UserAchievement,
    UserVocabulary,
)
from .db.repo import Repo, utcnow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Achievement:
    key: str
    title_ru: str
    description_ru: str
    icon: str
    # одна из: "sessions" | "streak" | "minutes" | "words" | "battles"
    metric: str
    target: int


# Каталог. Порядок = порядок в UI (от простого к сложному).
ACHIEVEMENTS: list[Achievement] = [
    Achievement("first_session", "Первая сессия",
                "Записал первый разговор", "🎤", "sessions", 1),
    Achievement("streak_3",  "Три дня подряд", "Стрик 3 дня",   "🔥", "streak", 3),
    Achievement("streak_7",  "Неделя огня",    "Стрик 7 дней",  "🔥", "streak", 7),
    Achievement("streak_30", "Месяц практики", "Стрик 30 дней", "🔥", "streak", 30),
    Achievement("minutes_30",  "Полчаса вместе",
                "30 минут разговоров",  "⏱", "minutes", 30),
    Achievement("minutes_120", "2 часа в эфире",
                "2 часа разговоров",    "⏱", "minutes", 120),
    Achievement("minutes_600", "Десятка часов",
                "10 часов разговоров",  "⏱", "minutes", 600),
    Achievement("words_50",  "Полсотни слов", "50 слов в словаре",  "📚", "words", 50),
    Achievement("words_200", "200 слов",      "200 слов в словаре", "📚", "words", 200),
    Achievement("battle_first", "Боец", "Первый battle", "⚔️", "battles", 1),
]

# Быстрый lookup
_BY_KEY: dict[str, Achievement] = {a.key: a for a in ACHIEVEMENTS}


def get_catalog() -> list[Achievement]:
    return ACHIEVEMENTS


async def collect_user_metrics(repo: Repo, user_id: int) -> dict[str, int]:
    """Возвращает {metric_name: current_value} по всем metric'ам из каталога.

    Стараемся уложиться в небольшое количество SQL — у нас лимит сейчас 5
    разных метрик, каждая один select.
    """
    s = repo.s

    # sessions: COUNT(*) FROM sessions WHERE user_id = X.
    sessions_res = await s.execute(
        select(func.count(SessionRow.id)).where(SessionRow.user_id == user_id)
    )
    sessions = int(sessions_res.scalar() or 0)

    # minutes: SUM(used_seconds) FROM sessions / 60.
    minutes_res = await s.execute(
        select(func.coalesce(func.sum(SessionRow.used_seconds), 0))
        .where(SessionRow.user_id == user_id)
    )
    minutes = int(minutes_res.scalar() or 0) // 60

    # streak: уже в User.streak_days
    user = await repo.get_user_by_id(user_id)
    streak = int(user.streak_days) if user else 0

    # words: count по user_vocabulary (любой source).
    words_res = await s.execute(
        select(func.count(UserVocabulary.id))
        .where(UserVocabulary.user_id == user_id)
    )
    words = int(words_res.scalar() or 0)

    # battles: COUNT judged-batt где юзер участвовал (по tg_id).
    # Это slow если battle большой, но юзеров там <100 у нас сейчас.
    battles = 0
    if user is not None:
        battles_res = await s.execute(
            select(func.count(Battle.id)).where(
                Battle.status == "judged",
                ((Battle.initiator_tg_id == user.tg_id)
                 | (Battle.opponent_tg_id == user.tg_id)),
            )
        )
        battles = int(battles_res.scalar() or 0)

    return {
        "sessions": sessions,
        "minutes": minutes,
        "streak": streak,
        "words": words,
        "battles": battles,
    }


async def get_earned_keys(repo: Repo, user_id: int) -> set[str]:
    res = await repo.s.execute(
        select(UserAchievement.achievement_key).where(
            UserAchievement.user_id == user_id
        )
    )
    return {row[0] for row in res.all()}


async def check_and_award(
    repo: Repo,
    user_id: int,
    *,
    notifier: Optional[Callable[[Achievement], Awaitable[None]]] = None,
) -> list[Achievement]:
    """Награждает все невзятые медали, чьи threshold'ы выполнены.

    notifier(achievement) — async callback для push'а в TG (опционально).
    Возвращает список новых медалей.
    """
    metrics = await collect_user_metrics(repo, user_id)
    earned = await get_earned_keys(repo, user_id)
    newly: list[Achievement] = []
    now = utcnow()

    for ach in ACHIEVEMENTS:
        if ach.key in earned:
            continue
        if metrics.get(ach.metric, 0) < ach.target:
            continue
        # Атомарный INSERT через PK; IntegrityError = гонка с другим
        # хуком, молча пропускаем.
        try:
            repo.s.add(UserAchievement(
                user_id=user_id, achievement_key=ach.key, earned_at=now,
            ))
            await repo.s.flush()
            newly.append(ach)
        except IntegrityError:
            await repo.s.rollback()

    if newly and notifier is not None:
        for ach in newly:
            try:
                await notifier(ach)
            except Exception as exc:
                logger.warning("[achievements] notifier failed: %r", exc)

    return newly


async def backfill_existing_users(
    repo: Repo,
    *,
    batch_size: int = 100,
) -> tuple[int, int]:
    """Однократный backfill для уже существующих юзеров — без push'а в TG.

    Иначе при первом деплое юзеры получат burst из 5+ уведомлений
    одновременно.

    Возвращает (users_processed, medals_seeded).
    """
    users_res = await repo.s.execute(select(User.id))
    user_ids = [int(r[0]) for r in users_res.all()]
    medals = 0

    for i in range(0, len(user_ids), batch_size):
        chunk = user_ids[i : i + batch_size]
        for uid in chunk:
            new_medals = await check_and_award(repo, uid, notifier=None)
            medals += len(new_medals)
        # Промежуточный commit между батчами, чтобы при ошибке не
        # потерять прогресс целиком.
        await repo.s.commit()

    return len(user_ids), medals
