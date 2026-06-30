"""Учёт времени использования для бесплатных пользователей.

Логика:
- Подписчики (active subscription_until > now) — без лимитов.
- Бесплатные — лимит N секунд в день (free_seconds_per_day из settings_kv,
  по умолчанию 600 = 10 мин). Сброс в 00:00 МСК (см. Repo.msk_today).
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
    ) -> None:
        self.user_db_id = user_db_id
        self.tg_id = tg_id
        self.has_subscription = has_subscription
        self.free_seconds_per_day = free_seconds_per_day
        self.used_seconds_today = used_seconds_today
        self.bonus_seconds_today = bonus_seconds_today
        self.is_blocked = is_blocked
        self._repo_factory = repo_factory

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
        )

    async def heartbeat(self, seconds: int) -> int:
        """Списать N секунд. Возвращает обновлённое used_seconds_today.

        repo_factory — это db_session (выдаёт AsyncSession), поэтому внутри
        оборачиваем в Repo(session).
        """
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


async def context_for_user(repo: Repo, repo_factory, user) -> LimitsContext:
    """Собрать LimitsContext для уже резолвнутого User (веб/JWT-путь)."""
    has_sub = await repo.has_active_subscription(user)
    if settings.FREE_PERIOD:
        has_sub = True
    free_seconds = await repo.get_kv_int(
        "free_seconds_per_day", DEFAULT_FREE_SECONDS_PER_DAY
    )
    used = await repo.get_speaking_seconds_today(user.id)
    bonus = await repo.get_bonus_seconds_today(user.id)
    return LimitsContext(
        user_db_id=user.id,
        tg_id=int(user.tg_id) if user.tg_id is not None else 0,
        has_subscription=has_sub,
        free_seconds_per_day=free_seconds,
        used_seconds_today=used,
        bonus_seconds_today=bonus,
        is_blocked=user.is_blocked,
        repo_factory=repo_factory,
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
) -> LimitsContext:
    """Upsert Telegram-юзера и собрать LimitsContext (Mini App / initData-путь)."""
    user = await repo.upsert_user(
        tg_id=tg_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        language_code=language_code,
    )
    return await context_for_user(repo, repo_factory, user)


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
    if await repo.has_active_subscription(user):
        return False
    cfg = _SECTION_QUOTA.get(section)
    if cfg is None:
        return False
    mode, kv_key, default = cfg
    quota = await repo.get_kv_int(kv_key, default)
    if quota <= 0:
        return False
    used = await repo.count_sessions_today(user.id, mode)
    return used >= quota
