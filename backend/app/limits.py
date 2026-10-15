"""Учёт времени использования для бесплатных пользователей.

Логика:
- Подписчики (active subscription_until > now) — без лимитов.
- Бесплатные — лимит N секунд в день (free_seconds_per_day из settings_kv,
  по умолчанию 300 = 5 мин). Сброс в 00:00 МСК.
- Разговор в уроке имеет собственный бюджет на урок и день.
- Учёт ведётся в daily_usage. heartbeat() прибавляет очередную порцию
  и возвращает остаток. Когда остаток <= 0 — клиента нужно отключить.

Использование в WS-handler:
    ctx = await LimitsContext.create(repo, user_info)
    if ctx.blocked: ...
    if ctx.limit_reached: ...
    # ... передаём ctx в voice_session, та периодически зовёт ctx.heartbeat(seconds=5)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .config import settings
from .db import Repo

log = logging.getLogger(__name__)

DEFAULT_FREE_SECONDS_PER_DAY = 300  # 5 минут


@dataclass
class LimitsSnapshot:
    """То, что отправляем клиенту в самом начале сессии."""
    has_subscription: bool
    free_seconds_per_day: int
    used_seconds_today: int
    bonus_seconds_today: int = 0  # квест-бонус, сбрасывается в полночь МСК
    # Welcome-триал (миграция 0033): первые FREE_TRIAL_DAYS дней полный
    # доступ. has_subscription при этом True — клиенту нужно знать, что
    # это триал, чтобы показать «полный доступ ещё N дней».
    trial_active: bool = False
    trial_until: Optional[str] = None
    scope: str = 'standalone'
    resets_at: Optional[str] = None

    @property
    def remaining_seconds(self) -> int:
        if self.has_subscription:
            return -1  # -1 = unlimited
        cap = self.free_seconds_per_day + self.bonus_seconds_today
        return max(0, cap - self.used_seconds_today)

    def to_dict(self) -> dict:
        return {
            "has_subscription": self.has_subscription,
            "free_seconds_per_day": self.free_seconds_per_day,
            "used_seconds_today": self.used_seconds_today,
            "bonus_seconds_today": self.bonus_seconds_today,
            "remaining_seconds": self.remaining_seconds,
            "trial_active": self.trial_active,
            "trial_until": self.trial_until,
            "scope": self.scope,
            "resets_at": self.resets_at,
        }


class LimitsContext:
    """Контекст лимитов для одной WS-сессии.

    Инкапсулирует ID юзера в БД, флаг подписки, текущий счётчик.
    Методы:
      - heartbeat(seconds): прибавить N секунд в daily_usage, вернуть remaining
      - is_exceeded(): True, если остаток <= 0 для бесплатного юзера
    """

    def __init__(
        self,
        *,
        user_db_id: int,
        tg_id: int,
        has_subscription: bool,
        free_seconds_per_day: int,
        used_seconds_today: int,
        bonus_seconds_today: int,
        is_blocked: bool,
        repo_factory,  # () -> async-context-manager c Repo
        trial_active: bool = False,
        trial_until: Optional[str] = None,
        lesson_id: Optional[str] = None,
        resets_at: Optional[str] = None,
    ) -> None:
        self.user_db_id = user_db_id
        self.tg_id = tg_id
        self.trial_active = trial_active
        self.trial_until = trial_until
        self.has_subscription = has_subscription
        self.free_seconds_per_day = free_seconds_per_day
        self.used_seconds_today = used_seconds_today
        self.bonus_seconds_today = bonus_seconds_today
        self.is_blocked = is_blocked
        self._repo_factory = repo_factory
        self.lesson_id = lesson_id
        self.resets_at = resets_at
        self.activity_id = None

    @property
    def remaining_seconds(self) -> int:
        if self.has_subscription:
            return -1
        cap = self.free_seconds_per_day + self.bonus_seconds_today
        return max(0, cap - self.used_seconds_today)

    def is_exceeded(self) -> bool:
        return not self.has_subscription and self.remaining_seconds <= 0

    def snapshot(self) -> LimitsSnapshot:
        return LimitsSnapshot(
            has_subscription=self.has_subscription,
            free_seconds_per_day=self.free_seconds_per_day,
            used_seconds_today=self.used_seconds_today,
            bonus_seconds_today=self.bonus_seconds_today,
            trial_active=self.trial_active,
            trial_until=self.trial_until,
            scope='lesson' if self.lesson_id else 'standalone',
            resets_at=self.resets_at,
        )

    async def heartbeat(self, seconds: int, *, finishing: bool = False) -> int:
        """Списать N секунд. Возвращает обновлённое used_seconds_today.

        repo_factory — это db_session (выдаёт AsyncSession), поэтому внутри
        оборачиваем в Repo(session).
        """
        if self.lesson_id:
            from fastapi import HTTPException
            from . import learning_modules as course
            from .learning_access import conversation_budget
            from .db.models import User, Session
            from .learning_models import LearningLesson
            async with self._repo_factory() as session:
                if finishing:
                    # A new socket may already own the stage. The old socket
                    # must still pay its final seconds without replacing progress.
                    account = await session.get(User,self.user_db_id,with_for_update=True,populate_existing=True)
                    row = await session.get(LearningLesson,self.lesson_id,with_for_update=True,populate_existing=True)
                    activity = await session.get(Session,self.activity_id,with_for_update=True)
                    if (row is None or row.user_id != self.user_db_id or activity is None
                        or activity.user_id != self.user_db_id or activity.learning_lesson_id != self.lesson_id):
                        raise HTTPException(409, 'conversation_unavailable')
                    seconds = max(0, seconds - (activity.used_seconds or 0))
                else:
                    row = await course.require(session, self.user_db_id, self.lesson_id, 'conversation', finishing=True)
                    if str(course.result(row, 'conversation').get('session_id')) != str(self.activity_id):
                        raise HTTPException(409, 'conversation_replaced')
                    account = await session.get(User, self.user_db_id)
                    activity = await session.get(Session,self.activity_id,with_for_update=True)
                    if activity is None or activity.user_id != self.user_db_id or activity.learning_lesson_id != self.lesson_id:
                        raise HTTPException(409, 'conversation_unavailable')
                full, cap, used, reset = await conversation_budget(Repo(session), account, row, seconds)
                if seconds:
                    activity.used_seconds = (activity.used_seconds or 0) + seconds
                    await Repo(session).add_used_seconds(user_id=self.user_db_id, seconds=seconds)
                self.trial_active = Repo.in_free_trial(account)
            self.has_subscription, self.free_seconds_per_day = full, cap
            self.used_seconds_today, self.resets_at = used, reset
            return used
        if self.has_subscription:
            # Подписчикам всё равно записываем для аналитики, но без проверок.
            # Говорение пишем и в used_seconds (аналитика), и в speaking_seconds.
            try:
                async with self._repo_factory() as session:
                    repo = Repo(session)
                    await repo.add_used_seconds(
                        user_id=self.user_db_id, seconds=seconds
                    )
                    await repo.add_speaking_seconds(
                        user_id=self.user_db_id, seconds=seconds
                    )
            except Exception as exc:
                log.warning("[limits] heartbeat (subscriber) ошибка: %s", exc)
            return self.used_seconds_today
        try:
            async with self._repo_factory() as session:
                repo = Repo(session)
                # used_seconds — общий счётчик (аналитика).
                await repo.add_used_seconds(
                    user_id=self.user_db_id, seconds=seconds
                )
                # speaking_seconds — то, на чём строится гейт говорения.
                self.used_seconds_today = await repo.add_speaking_seconds(
                    user_id=self.user_db_id, seconds=seconds
                )
        except Exception as exc:
            log.warning("[limits] heartbeat ошибка: %s", exc)
        return self.used_seconds_today

    async def finish(self, duration_seconds: int) -> None:
        # A disconnect before the five-second watchdog tick still consumes time.
        if self.lesson_id and self.activity_id is not None:
            await self.heartbeat(duration_seconds, finishing=True)


async def context_for_user(repo: Repo, repo_factory, user, *, lesson_id: Optional[str] = None) -> LimitsContext:
    """Собрать LimitsContext для уже резолвнутого User (веб/JWT-путь)."""
    # Welcome-триал (0033): первые FREE_TRIAL_DAYS дней — те же права, что
    # у подписчика. Флаг держим отдельно, чтобы клиент показал «полный
    # доступ ещё N дней», а не «подписка активна».
    trial_until_dt = repo.free_trial_until(user)
    trial_active = repo.in_free_trial(user)
    has_sub = trial_active or await repo.has_active_subscription(user)
    if settings.FREE_PERIOD:
        has_sub = True
    free_seconds = await repo.get_kv_int(
        "free_seconds_per_day", DEFAULT_FREE_SECONDS_PER_DAY
    )
    used = await repo.get_speaking_seconds_today(user.id)
    bonus = await repo.get_bonus_seconds_today(user.id)
    reset = None
    if lesson_id:
        from .learning_modules import require
        from .learning_access import conversation_budget
        lesson = await require(repo.s, user.id, lesson_id, 'conversation')
        has_sub, free_seconds, used, reset = await conversation_budget(repo, user, lesson)
        bonus = 0
    return LimitsContext(
        user_db_id=user.id,
        tg_id=int(user.tg_id) if user.tg_id is not None else 0,
        trial_active=trial_active,
        trial_until=trial_until_dt.isoformat() if trial_until_dt else None,
        has_subscription=has_sub,
        free_seconds_per_day=free_seconds,
        used_seconds_today=used,
        bonus_seconds_today=bonus,
        is_blocked=user.is_blocked,
        repo_factory=repo_factory,
        lesson_id=lesson_id,
        resets_at=reset,
    )


async def build_limits_context(
    *,
    repo: Repo,
    repo_factory,
    tg_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
    language_code: Optional[str],
    lesson_id: Optional[str] = None,
) -> LimitsContext:
    """Upsert Telegram-юзера и собрать LimitsContext (Mini App / initData-путь)."""
    user = await repo.upsert_user(
        tg_id=tg_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        language_code=language_code,
    )
    return await context_for_user(repo, repo_factory, user, lesson_id=lesson_id)


# ─── Посекционные лимиты (listening / grammar) ───────────────────────────
# Считаются по числу сессий за сегодня (sessions.mode). Слова (srs) — без лимита.

# section → (mode в sessions, ключ квоты в settings_kv, дефолт)
_SECTION_QUOTA = {
    "listening": ("listening", "free_listening_per_day", 1),
    "grammar": ("grammar", "free_grammar_per_day", 1),
}


async def is_section_limit_reached(repo: Repo, user, *, section: str) -> bool:
    """True, если free-юзер исчерпал дневную квоту секции.

    Подписчики и FREE_PERIOD — всегда False (безлимит). `user` — ORM User.
    """
    if settings.FREE_PERIOD:
        return False
    # Welcome-триал снимает и посекционные квоты — человек должен успеть
    # попробовать подкасты и грамматику, а не только разговор.
    if await repo.has_full_access(user):
        return False
    cfg = _SECTION_QUOTA.get(section)
    if cfg is None:
        return False
    mode, kv_key, default = cfg
    quota = await repo.get_kv_int(kv_key, default)
    if quota <= 0:
        return False
    used = await repo.count_sessions_today(user.id, mode, standalone_only=True)
    return used >= quota
