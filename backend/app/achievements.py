"""Каталог медалей (retention v1) + логика проверки/награждения.

Идея: после каждой сессии (в voice.py finally) дёргаем check_and_award.
Идём по статичному каталогу ниже, для каждой невзятой медали проверяем
текущее значение метрики юзера, если оно >= target — INSERT в
user_achievements и (опционально) push в TG.

Метрики читаются батчем в collect_user_metrics — один-два SQL вместо
десяти, по одному на каждую медаль.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .db.models import (
    Session as SessionRow,
    User,
    UserAchievement,
    UserGrammarProgress,
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
    # одна из: "sessions" | "streak" | "minutes" | "words"
    #        | "listening_sessions" | "grammar_lessons" | "modes_count"
    metric: str
    target: int


# Каталог. Порядок = порядок в UI (от простого к сложному, по темам).
ACHIEVEMENTS: list[Achievement] = [
    # ── Старт + регулярность ────────────────────────────────────
    Achievement("first_session", "Первая сессия",
                "Стартовал практику — любой режим", "🎤", "sessions", 1),
    Achievement("streak_3",  "Три дня подряд", "Стрик 3 дня",   "🔥", "streak", 3),
    Achievement("streak_7",  "Неделя огня",    "Стрик 7 дней",  "🔥", "streak", 7),
    Achievement("streak_30", "Месяц практики", "Стрик 30 дней", "🔥", "streak", 30),

    # ── Время в эфире (любого режима) ───────────────────────────
    Achievement("minutes_30",  "Полчаса вместе",
                "30 минут практики",  "⏱", "minutes", 30),
    Achievement("minutes_120", "2 часа в эфире",
                "2 часа практики",    "⏱", "minutes", 120),
    Achievement("minutes_600", "Десятка часов",
                "10 часов практики",  "⏱", "minutes", 600),

    # ── Личный словарь ──────────────────────────────────────────
    Achievement("words_50",  "Полсотни слов", "50 слов в словаре",  "📚", "words", 50),
    Achievement("words_200", "200 слов",      "200 слов в словаре", "📚", "words", 200),

    # ── Listening (🎧) ──────────────────────────────────────────
    Achievement("listening_first", "Первый подкаст",
                "Послушал первый сгенерированный подкаст", "🎧",
                "listening_sessions", 1),
    Achievement("listening_10", "Подкаст-марафон",
                "Послушал 10 подкастов", "🎧", "listening_sessions", 10),

    # ── Grammar Learn (📝) ──────────────────────────────────────
    Achievement("grammar_first", "Первое правило",
                "Прошёл первую тему грамматики", "📝", "grammar_lessons", 1),
    Achievement("grammar_10", "Знаток основ",
                "Прошёл 10 тем грамматики", "📝", "grammar_lessons", 10),
    Achievement("grammar_25", "Половина пути",
                "Прошёл 25 тем грамматики", "🎓", "grammar_lessons", 25),
    Achievement("grammar_all", "Магистр грамматики",
                "Прошёл все темы грамматики", "🏆", "grammar_lessons", 50),

    # ── SRS (📚 интервальное повторение) ─────────────────────────
    Achievement("srs_first", "Первая карточка",
                "Повторил первое слово в SRS", "🔁", "srs_reviews", 1),
    Achievement("srs_100", "Сотня повторов",
                "100 ответов в SRS", "🧠", "srs_reviews", 100),

    # ── Универсал ───────────────────────────────────────────────
    Achievement("polyglot", "Универсал",
                "Попробовал все три режима: разговор, слушание, грамматика",
                "🌟", "modes_count", 3),
]

# Быстрый lookup
_BY_KEY: dict[str, Achievement] = {a.key: a for a in ACHIEVEMENTS}


def get_catalog() -> list[Achievement]:
    return ACHIEVEMENTS


async def collect_user_metrics(repo: Repo, user_id: int) -> dict[str, int]:
    """Возвращает {metric_name: current_value} по всем metric'ам из каталога."""
    s = repo.s

    # sessions: COUNT(*) FROM sessions WHERE user_id = X.
    sessions_res = await s.execute(
        select(func.count(SessionRow.id)).where(SessionRow.user_id == user_id)
    )
    sessions = int(sessions_res.scalar() or 0)

    # minutes: SUM(used_seconds) / 60 по всем сессиям (любой mode).
    minutes_res = await s.execute(
        select(func.coalesce(func.sum(SessionRow.used_seconds), 0))
        .where(SessionRow.user_id == user_id)
    )
    minutes = int(minutes_res.scalar() or 0) // 60

    # streak — уже в User.streak_days.
    user = await repo.get_user_by_id(user_id)
    streak = int(user.streak_days) if user else 0

    # words: count по user_vocabulary (любой source).
    words_res = await s.execute(
        select(func.count(UserVocabulary.id))
        .where(UserVocabulary.user_id == user_id)
    )
    words = int(words_res.scalar() or 0)

    # listening_sessions: count sessions where mode='listening'.
    listening_res = await s.execute(
        select(func.count(SessionRow.id)).where(
            SessionRow.user_id == user_id,
            SessionRow.mode == "listening",
        )
    )
    listening_sessions = int(listening_res.scalar() or 0)

    # grammar_lessons: пройдено тем (completed_at IS NOT NULL).
    grammar_res = await s.execute(
        select(func.count(UserGrammarProgress.topic_key)).where(
            UserGrammarProgress.user_id == user_id,
            UserGrammarProgress.completed_at.is_not(None),
        )
    )
    grammar_lessons = int(grammar_res.scalar() or 0)

    # srs_reviews: суммарно ответов в SRS по всем user-словам.
    srs_res = await s.execute(
        select(func.coalesce(func.sum(UserVocabulary.srs_total_attempts), 0))
        .where(
            UserVocabulary.user_id == user_id,
            UserVocabulary.source == "user",
        )
    )
    srs_reviews = int(srs_res.scalar() or 0)

    # modes_count: сколько разных режимов попробовал (distinct mode).
    # voice + chat считаем как один «разговор», чтобы юзер не получал
    # «универсал» за один speaking-сеанс через два UI-варианта.
    modes_res = await s.execute(
        select(SessionRow.mode).where(SessionRow.user_id == user_id).distinct()
    )
    distinct_modes = {row[0] for row in modes_res.all()}
    canon_modes: set[str] = set()
    for m in distinct_modes:
        if m in ("voice", "chat"):
            canon_modes.add("speaking")
        else:
            canon_modes.add(m)
    modes_count = len(canon_modes)

    return {
        "sessions": sessions,
        "minutes": minutes,
        "streak": streak,
        "words": words,
        "listening_sessions": listening_sessions,
        "grammar_lessons": grammar_lessons,
        "srs_reviews": srs_reviews,
        "modes_count": modes_count,
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
