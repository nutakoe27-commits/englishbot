"""Бизнес-репозиторий: всё, что нужно из БД, выражено как методы Repo.

Дизайн: Repo — тонкая обёртка вокруг AsyncSession. Создаётся внутри
db_session() и не переживает её. Никакой кешированной валидации.
"""

from __future__ import annotations

from datetime import datetime, date, time, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    DailyUsage,
    GrammarLessonCache,
    GrammarTopic,
    Payment,
    SettingKV,
    Session as SessionRow,
    User,
    UserGrammarProgress,
    AuthAction,
    UserIdentity,
    UserMistake,
    UserVocabulary,
)


# Europe/Moscow без зависимости от системного tz — фикс UTC+3.
# (МСК круглый год +3, без перехода на летнее время с 2014.)
MSK = timezone(timedelta(hours=3))


def msk_today() -> date:
    return datetime.now(MSK).date()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Repo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    # ─── users ──────────────────────────────────────────────────────────
    async def upsert_user(
        self,
        *,
        tg_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        language_code: Optional[str] = None,
    ) -> User:
        """Создать юзера, если его нет; иначе обновить профиль."""
        now = utcnow()
        stmt = mysql_insert(User).values(
            tg_id=tg_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            language_code=language_code,
            reminder_time=time(19, 0),
            reminder_enabled=True,
            is_blocked=False,
            created_at=now,
            updated_at=now,
        )
        stmt = stmt.on_duplicate_key_update(
            username=stmt.inserted.username,
            first_name=stmt.inserted.first_name,
            last_name=stmt.inserted.last_name,
            language_code=stmt.inserted.language_code,
            updated_at=now,
        )
        await self.s.execute(stmt)
        user = await self.get_user_by_tg_id(tg_id)
        # Гарантируем telegram-identity (для юзеров, созданных до миграции 0020
        # backfill или вне неё). Идемпотентно через UNIQUE(provider, uid).
        if user is not None:
            await self._ensure_identity(user.id, "telegram", str(tg_id), None)
        return user

    async def get_user_by_tg_id(self, tg_id: int) -> Optional[User]:
        res = await self.s.execute(select(User).where(User.tg_id == tg_id))
        return res.scalar_one_or_none()

    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        res = await self.s.execute(select(User).where(User.id == user_id))
        return res.scalar_one_or_none()

    # ─── auth identities (миграция 0020) ────────────────────────────────
    async def get_user_by_identity(
        self, provider: str, provider_uid: str,
    ) -> Optional[User]:
        res = await self.s.execute(
            select(User)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(
                UserIdentity.provider == provider,
                UserIdentity.provider_uid == provider_uid,
            )
        )
        return res.scalar_one_or_none()

    async def get_user_by_email(self, email: str) -> Optional[User]:
        if not email:
            return None
        res = await self.s.execute(
            select(User).where(User.email == email).limit(1)
        )
        return res.scalar_one_or_none()

    async def list_identities(self, user_id: int) -> list[dict]:
        res = await self.s.execute(
            select(UserIdentity.provider, UserIdentity.email, UserIdentity.created_at)
            .where(UserIdentity.user_id == user_id)
            .order_by(UserIdentity.created_at.asc())
        )
        return [
            {"provider": p, "email": e, "created_at": c}
            for p, e, c in res.all()
        ]

    async def _ensure_identity(
        self, user_id: int, provider: str, provider_uid: str, email: Optional[str],
    ) -> None:
        """INSERT IGNORE identity (идемпотентно по UNIQUE(provider, uid))."""
        stmt = mysql_insert(UserIdentity).values(
            user_id=user_id,
            provider=provider,
            provider_uid=provider_uid,
            email=email,
            created_at=utcnow(),
        ).prefix_with("IGNORE")
        await self.s.execute(stmt)

    async def create_user_with_identity(
        self,
        *,
        provider: str,
        provider_uid: str,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        tg_id: Optional[int] = None,
        username: Optional[str] = None,
        language_code: Optional[str] = None,
    ) -> User:
        """Создать новый аккаунт + identity (для регистрации через провайдера)."""
        now = utcnow()
        user = User(
            tg_id=tg_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            language_code=language_code,
            email=email,
            reminder_time=time(19, 0),
            reminder_enabled=True,
            is_blocked=False,
            created_at=now,
            updated_at=now,
        )
        self.s.add(user)
        await self.s.flush()  # получить user.id
        await self._ensure_identity(user.id, provider, provider_uid, email)
        return user

    async def create_native_user(
        self, *, email: str, password_hash: str, first_name: Optional[str] = None,
    ) -> User:
        """Создать аккаунт через нативную (email+password) регистрацию.

        provider_uid в user_identities = lower(email) — он же логин.
        """
        now = utcnow()
        user = User(
            email=email,
            password_hash=password_hash,
            first_name=first_name,
            reminder_time=time(19, 0),
            reminder_enabled=True,
            is_blocked=False,
            created_at=now,
            updated_at=now,
        )
        self.s.add(user)
        await self.s.flush()
        await self._ensure_identity(user.id, "native", email, email)
        return user

    async def set_password(self, user_id: int, password_hash: str) -> None:
        """Задать/обновить пароль и создать native-identity, если её нет.

        provider_uid берём из users.email (он уже должен быть выставлен).
        """
        await self.s.execute(
            update(User).where(User.id == user_id).values(
                password_hash=password_hash, updated_at=utcnow(),
            )
        )
        user = await self.get_user_by_id(user_id)
        if user and user.email:
            await self._ensure_identity(user_id, "native", user.email, user.email)

    async def set_email(self, user_id: int, email: str) -> str:
        """Выставить email юзеру. 'ok' | 'taken' (email уже у другого).
        Идентичность native не трогаем — её создаст set_password."""
        existing = await self.get_user_by_email(email)
        if existing and existing.id != user_id:
            return "taken"
        await self.s.execute(
            update(User).where(User.id == user_id).values(
                email=email, updated_at=utcnow(),
            )
        )
        return "ok"

    async def link_identity(
        self, user_id: int, provider: str, provider_uid: str,
        email: Optional[str] = None,
    ) -> str:
        """Привязать провайдер к аккаунту. 'ok' | 'taken' (уже у другого)."""
        existing = await self.get_user_by_identity(provider, provider_uid)
        if existing is not None:
            return "ok" if existing.id == user_id else "taken"
        await self._ensure_identity(user_id, provider, provider_uid, email)
        # Если у аккаунта ещё нет email — проставим из провайдера.
        if email:
            await self.s.execute(
                update(User).where(User.id == user_id, User.email.is_(None))
                .values(email=email, updated_at=utcnow())
            )
        return "ok"

    # ─── auth_actions (миграция 0022) ──────────────────────────────────
    async def create_auth_action(
        self,
        action: str,
        *,
        user_id: Optional[int] = None,
        ttl_sec: int = 600,
    ) -> str:
        """Создать новый pending-токен. Возвращает token (32 base64url-символа)."""
        import secrets
        token = secrets.token_urlsafe(24)
        now = utcnow()
        row = AuthAction(
            token=token,
            action=action,
            user_id=user_id,
            status="pending",
            expires_at=now + timedelta(seconds=ttl_sec),
            created_at=now,
        )
        self.s.add(row)
        await self.s.flush()
        return token

    async def get_action(self, token: str) -> Optional[AuthAction]:
        if not token:
            return None
        res = await self.s.execute(
            select(AuthAction).where(AuthAction.token == token)
        )
        return res.scalar_one_or_none()

    async def get_pending_action(self, token: str) -> Optional[AuthAction]:
        """Действие, готовое к применению: status='pending' и не просрочено."""
        action = await self.get_action(token)
        if action is None:
            return None
        if action.status != "pending":
            return None
        if action.expires_at and action.expires_at <= utcnow():
            return None
        return action

    async def mark_action_done(
        self, token: str, resulting_user_id: Optional[int] = None,
    ) -> None:
        await self.s.execute(
            update(AuthAction).where(AuthAction.token == token).values(
                status="done",
                resulting_user_id=resulting_user_id,
                consumed_at=utcnow(),
            )
        )

    async def mark_action_cancelled(self, token: str) -> None:
        await self.s.execute(
            update(AuthAction).where(AuthAction.token == token).values(
                status="cancelled", consumed_at=utcnow(),
            )
        )

    async def mark_action_failed(self, token: str) -> None:
        await self.s.execute(
            update(AuthAction).where(AuthAction.token == token).values(
                status="failed", consumed_at=utcnow(),
            )
        )

    async def delete_native_identity(self, user_id: int) -> bool:
        """Снять привязку email/password.

        Возвращает False, если у юзера нет Telegram (нельзя оставить аккаунт
        совсем без способа входа). Иначе удаляет native-identity и
        обнуляет users.password_hash. users.email НЕ трогаем — можно потом
        снова «Задать пароль», логика возьмёт users.email.
        """
        # Должен быть Telegram, иначе отказываем.
        has_tg_res = await self.s.execute(
            select(func.count(UserIdentity.id)).where(
                UserIdentity.user_id == user_id,
                UserIdentity.provider == "telegram",
            )
        )
        if int(has_tg_res.scalar() or 0) == 0:
            return False
        from sqlalchemy import delete as _del
        await self.s.execute(_del(UserIdentity).where(
            UserIdentity.user_id == user_id,
            UserIdentity.provider == "native",
        ))
        await self.s.execute(
            update(User).where(User.id == user_id).values(
                password_hash=None, updated_at=utcnow(),
            )
        )
        return True

    async def link_or_merge(
        self, user_id: int, provider: str, provider_uid: str,
        email: Optional[str] = None,
    ) -> dict:
        """Привязать провайдер. Если он уже у ДРУГОГО аккаунта — слить.

        Правило слияния: primary = старший по created_at (его identifier-поля,
        subscription_until, streak_days сохраняются). Числовые накопления в
        таблицах с составным ключом суммируются/MAX.

        Возвращает {kind: 'linked' | 'noop' | 'merged', primary_id: int}.
        """
        existing = await self.get_user_by_identity(provider, provider_uid)
        if existing is None:
            await self._ensure_identity(user_id, provider, provider_uid, email)
            if email:
                await self.s.execute(
                    update(User).where(User.id == user_id, User.email.is_(None))
                    .values(email=email, updated_at=utcnow())
                )
            return {"kind": "linked", "primary_id": user_id}
        if existing.id == user_id:
            return {"kind": "noop", "primary_id": user_id}

        # Слияние. Старший по created_at — primary.
        current = await self.get_user_by_id(user_id)
        if current is None:
            return {"kind": "noop", "primary_id": user_id}
        if (current.created_at or utcnow()) <= (existing.created_at or utcnow()):
            primary, secondary = current, existing
        else:
            primary, secondary = existing, current
        await self._merge_accounts(primary.id, secondary.id)
        return {"kind": "merged", "primary_id": primary.id}

    async def _merge_accounts(self, primary_id: int, secondary_id: int) -> None:
        """Переносит данные secondary → primary, удаляет secondary.

        Все операции в одной транзакции (внешний вызывающий коммитит). Порядок
        важен: сначала таблицы с составными ключами через INSERT…ON DUPLICATE…,
        потом простые UPDATE, в конце DELETE FROM users.
        """
        from sqlalchemy import text as _text
        if primary_id == secondary_id:
            return
        params = {"primary": primary_id, "secondary": secondary_id}

        # 1) user_identities — простой UPDATE, конфликта нет
        # (UNIQUE(provider, provider_uid) — у разных юзеров разные UID).
        await self.s.execute(_text(
            "UPDATE user_identities SET user_id = :primary WHERE user_id = :secondary"
        ), params)

        # 2) sessions, user_mistakes, payments — простые UPDATE.
        for tbl in ("sessions", "user_mistakes", "payments"):
            await self.s.execute(_text(
                f"UPDATE {tbl} SET user_id = :primary WHERE user_id = :secondary"
            ), params)

        # 3) daily_usage (PK user_id, usage_date) — суммируем по дате.
        # SELF-INSERT (source и target — одна таблица) → в ON DUPLICATE
        # неоднозначность по «голому» имени колонки; квалифицируем target
        # как `daily_usage.col`, source — через alias `src`.
        await self.s.execute(_text("""
            INSERT INTO daily_usage (user_id, usage_date, used_seconds, bonus_seconds, speaking_seconds, updated_at)
            SELECT :primary, src.usage_date, src.used_seconds, src.bonus_seconds, src.speaking_seconds, NOW()
              FROM daily_usage AS src WHERE src.user_id = :secondary
            ON DUPLICATE KEY UPDATE
              used_seconds     = daily_usage.used_seconds     + VALUES(used_seconds),
              bonus_seconds    = daily_usage.bonus_seconds    + VALUES(bonus_seconds),
              speaking_seconds = daily_usage.speaking_seconds + VALUES(speaking_seconds),
              updated_at       = NOW()
        """), params)
        await self.s.execute(_text(
            "DELETE FROM daily_usage WHERE user_id = :secondary"
        ), params)

        # 4) user_vocabulary (UNIQUE user_id, word) — мержим общие слова в
        # primary (MAX/SUM/GREATEST), остальное переносим, secondary удалим.
        await self.s.execute(_text("""
            UPDATE user_vocabulary p
              JOIN user_vocabulary s
                ON p.word = s.word AND p.user_id = :primary AND s.user_id = :secondary
            SET p.times_used = p.times_used + s.times_used,
                p.first_seen_at = LEAST(p.first_seen_at, s.first_seen_at),
                p.last_seen_at = GREATEST(p.last_seen_at, s.last_seen_at),
                p.srs_box = GREATEST(p.srs_box, s.srs_box),
                p.srs_correct_streak = GREATEST(p.srs_correct_streak, s.srs_correct_streak),
                p.srs_total_attempts = p.srs_total_attempts + s.srs_total_attempts,
                p.srs_last_reviewed_at = GREATEST(
                  COALESCE(p.srs_last_reviewed_at, s.srs_last_reviewed_at),
                  COALESCE(s.srs_last_reviewed_at, p.srs_last_reviewed_at)
                ),
                p.translation = COALESCE(p.translation, s.translation),
                p.source = IF(p.source='user' OR s.source='user', 'user', p.source)
        """), params)
        await self.s.execute(_text(
            "DELETE FROM user_vocabulary WHERE user_id = :secondary "
            "AND word IN (SELECT word FROM (SELECT word FROM user_vocabulary "
            "  WHERE user_id = :primary) t)"
        ), params)
        await self.s.execute(_text(
            "UPDATE user_vocabulary SET user_id = :primary WHERE user_id = :secondary"
        ), params)

        # 5) user_achievements (PK user_id, achievement_key) — INSERT IGNORE.
        await self.s.execute(_text("""
            INSERT IGNORE INTO user_achievements (user_id, achievement_key, earned_at)
            SELECT :primary, achievement_key, earned_at
              FROM user_achievements WHERE user_id = :secondary
        """), params)
        await self.s.execute(_text(
            "DELETE FROM user_achievements WHERE user_id = :secondary"
        ), params)

        # 6) user_grammar_progress (PK user_id, topic_key) — мерж score/attempts.
        # SELF-INSERT, см. комментарий выше про ambiguous.
        await self.s.execute(_text("""
            INSERT INTO user_grammar_progress
              (user_id, topic_key, completed_at, best_score, attempts, updated_at)
            SELECT :primary, src.topic_key, src.completed_at, src.best_score, src.attempts, NOW()
              FROM user_grammar_progress AS src WHERE src.user_id = :secondary
            ON DUPLICATE KEY UPDATE
              best_score   = GREATEST(user_grammar_progress.best_score, VALUES(best_score)),
              attempts     = user_grammar_progress.attempts + VALUES(attempts),
              completed_at = COALESCE(user_grammar_progress.completed_at, VALUES(completed_at)),
              updated_at   = NOW()
        """), params)
        await self.s.execute(_text(
            "DELETE FROM user_grammar_progress WHERE user_id = :secondary"
        ), params)

        # 7) user_quests (UNIQUE user_id, quest_key) — INSERT IGNORE (живёт
        # таблица из миграции 0002; если battle/quest у тебя выпилен — query
        # просто отработает 0 rows).
        try:
            await self.s.execute(_text("""
                INSERT IGNORE INTO user_quests
                  (user_id, quest_key, assigned_at, completed_at, expired_at)
                SELECT :primary, quest_key, assigned_at, completed_at, expired_at
                  FROM user_quests WHERE user_id = :secondary
            """), params)
            await self.s.execute(_text(
                "DELETE FROM user_quests WHERE user_id = :secondary"
            ), params)
        except Exception:
            pass  # таблицы могло не быть

        # 8) users (primary): дополняем null-поля из secondary;
        # subscription_until / streak_days / best_streak_days НЕ ТРОГАЕМ —
        # строго у старшего.
        await self.s.execute(_text("""
            UPDATE users p JOIN users s ON s.id = :secondary
            SET p.tg_id = COALESCE(p.tg_id, s.tg_id),
                p.email = COALESCE(p.email, s.email),
                p.password_hash = COALESCE(p.password_hash, s.password_hash),
                p.username = COALESCE(p.username, s.username),
                p.first_name = COALESCE(p.first_name, s.first_name),
                p.last_name = COALESCE(p.last_name, s.last_name),
                p.language_code = COALESCE(p.language_code, s.language_code),
                p.bot_activated_at = LEAST(
                  COALESCE(p.bot_activated_at, s.bot_activated_at),
                  COALESCE(s.bot_activated_at, p.bot_activated_at)
                ),
                p.last_practice_date = GREATEST(
                  COALESCE(p.last_practice_date, s.last_practice_date),
                  COALESCE(s.last_practice_date, p.last_practice_date)
                ),
                p.updated_at = NOW()
            WHERE p.id = :primary
        """), params)

        # 9) Снять FK-конфликт по tg_id (UNIQUE): у secondary tg_id уже не нужен,
        # перед удалением сбросим, чтобы DELETE точно прошёл.
        await self.s.execute(_text(
            "UPDATE users SET tg_id = NULL WHERE id = :secondary"
        ), params)
        # Удалить secondary — остальное подчистится через ON DELETE CASCADE.
        await self.s.execute(_text(
            "DELETE FROM users WHERE id = :secondary"
        ), params)

    async def count_identities(self, user_id: int) -> int:
        res = await self.s.execute(
            select(func.count(UserIdentity.id)).where(
                UserIdentity.user_id == user_id
            )
        )
        return int(res.scalar() or 0)

    async def unlink_identity(self, user_id: int, provider: str) -> bool:
        """Удалить привязку провайдера. False если это последний способ входа."""
        if await self.count_identities(user_id) <= 1:
            return False
        from sqlalchemy import delete
        await self.s.execute(
            delete(UserIdentity).where(
                UserIdentity.user_id == user_id,
                UserIdentity.provider == provider,
            )
        )
        return True

    async def has_active_subscription(self, user: User) -> bool:
        if user.subscription_until is None:
            return False
        return user.subscription_until > utcnow()

    async def add_subscription_days(
        self,
        *,
        user: User,
        days: int,
        plan: str = "admin_grant",
        granted_by_tg_id: Optional[int] = None,
        amount_rub: float = 0.0,
        notes: Optional[str] = None,
    ) -> None:
        """Продлить подписку на N дней. Если уже активна — прибавить к концу."""
        now = utcnow()
        base = (
            user.subscription_until
            if user.subscription_until and user.subscription_until > now
            else now
        )
        new_until = base + timedelta(days=days)
        await self.s.execute(
            update(User).where(User.id == user.id).values(subscription_until=new_until)
        )
        # Запись в payments для аудита
        self.s.add(
            Payment(
                user_id=user.id,
                amount_rub=amount_rub,
                plan=plan,
                status="succeeded",
                days_granted=days,
                granted_by_tg_id=granted_by_tg_id,
                notes=notes,
                created_at=now,
                updated_at=now,
            )
        )

    # ─── usage / лимиты ────────────────────────────────────────────────
    async def get_used_seconds_today(self, user_id: int) -> int:
        res = await self.s.execute(
            select(DailyUsage.used_seconds).where(
                DailyUsage.user_id == user_id,
                DailyUsage.usage_date == msk_today(),
            )
        )
        return int(res.scalar_one_or_none() or 0)

    async def get_bonus_seconds_today(self, user_id: int) -> int:
        """Бонус за выполненный Daily Quest (сбрасывается в 00:00 МСК)."""
        res = await self.s.execute(
            select(DailyUsage.bonus_seconds).where(
                DailyUsage.user_id == user_id,
                DailyUsage.usage_date == msk_today(),
            )
        )
        return int(res.scalar_one_or_none() or 0)

    async def add_used_seconds(self, *, user_id: int, seconds: int) -> int:
        """Прибавить N секунд к дневному счётчику. Возвращает итоговое значение."""
        if seconds <= 0:
            return await self.get_used_seconds_today(user_id)
        today = msk_today()
        now = utcnow()
        stmt = mysql_insert(DailyUsage).values(
            user_id=user_id,
            usage_date=today,
            used_seconds=seconds,
            updated_at=now,
        )
        stmt = stmt.on_duplicate_key_update(
            used_seconds=DailyUsage.used_seconds + seconds,
            updated_at=now,
        )
        await self.s.execute(stmt)
        return await self.get_used_seconds_today(user_id)

    # ─── speaking-only счётчик (миграция 0016) ──────────────────────────
    # Лимит говорения считается отдельно от used_seconds, чтобы слушание/
    # грамматика (которые тоже пишут used_seconds для аналитики) не тратили
    # бюджет говорения.
    async def get_speaking_seconds_today(self, user_id: int) -> int:
        res = await self.s.execute(
            select(DailyUsage.speaking_seconds).where(
                DailyUsage.user_id == user_id,
                DailyUsage.usage_date == msk_today(),
            )
        )
        return int(res.scalar_one_or_none() or 0)

    async def add_speaking_seconds(self, *, user_id: int, seconds: int) -> int:
        """Прибавить N секунд к дневному счётчику говорения. Возвращает итог."""
        if seconds <= 0:
            return await self.get_speaking_seconds_today(user_id)
        today = msk_today()
        now = utcnow()
        stmt = mysql_insert(DailyUsage).values(
            user_id=user_id,
            usage_date=today,
            speaking_seconds=seconds,
            updated_at=now,
        )
        stmt = stmt.on_duplicate_key_update(
            speaking_seconds=DailyUsage.speaking_seconds + seconds,
            updated_at=now,
        )
        await self.s.execute(stmt)
        return await self.get_speaking_seconds_today(user_id)

    async def count_sessions_today(self, user_id: int, mode: str) -> int:
        """Сколько сессий данного режима юзер начал сегодня (по МСК).

        Используется для посекционных дневных лимитов (listening/grammar).
        Граница дня — полночь МСК, переведённая в наивный UTC (как хранятся
        started_at через utcnow()).
        """
        # Полночь МСК сегодня → naive UTC.
        msk_midnight = datetime.combine(msk_today(), time(0, 0), tzinfo=MSK)
        utc_boundary = msk_midnight.astimezone(timezone.utc).replace(tzinfo=None)
        res = await self.s.execute(
            select(func.count(SessionRow.id)).where(
                SessionRow.user_id == user_id,
                SessionRow.mode == mode,
                SessionRow.started_at >= utc_boundary,
            )
        )
        return int(res.scalar() or 0)

    # ─── streak ─────────────────────────────────────────────────────────
    async def bump_streak(
        self, user_id: int, *, role: Optional[str] = None,
    ) -> tuple[int, int]:
        """Зафиксировать практику сегодня и обновить стрик.

        Логика:
          - Если уже занимались сегодня → стрик не меняем, но last_session_role
            всё равно перезаписываем (юзер мог в эту сессию выбрать другую роль).
          - Если занимались вчера → streak_days += 1.
          - Иначе (пропустили день или первый раз) → streak_days = 1.
          - best_streak_days тянется как max(best, current).

        Параметр `role` — роль сессии из SessionSettings.role; сохраняется
        в users.last_session_role для assign_daily_quest.

        Возвращает (current_streak, best_streak) после апдейта. Если юзер
        не найден — (0, 0).
        """
        user = await self.get_user_by_id(user_id)
        if user is None:
            return (0, 0)

        today = msk_today()
        already_today = user.last_practice_date == today

        if already_today:
            new_streak = user.streak_days
            new_best = user.best_streak_days
        elif user.last_practice_date == today - timedelta(days=1):
            new_streak = user.streak_days + 1
            new_best = max(user.best_streak_days, new_streak)
        else:
            # Пропуск или первый раз.
            new_streak = 1
            new_best = max(user.best_streak_days, new_streak)

        values: dict = {
            "streak_days": new_streak,
            "best_streak_days": new_best,
            "last_practice_date": today,
        }
        if role:
            values["last_session_role"] = role
        await self.s.execute(
            update(User).where(User.id == user_id).values(**values)
        )
        return (new_streak, new_best)

    async def get_streak(self, user_id: int) -> tuple[int, int, Optional[date]]:
        """Текущий стрик / лучший / дата последней практики (МСК)."""
        user = await self.get_user_by_id(user_id)
        if user is None:
            return (0, 0, None)
        return (user.streak_days, user.best_streak_days, user.last_practice_date)

    # ─── learner context (vocabulary + mistakes) ───────────────────────
    # Лимит на количество пользовательских слов в словаре одного юзера.
    # В system_prompt подмешиваем только топ-10 свежих (см.
    # get_user_words_for_prompt), так что большой лимит не раздувает промпт —
    # хранилище нужно под SRS-карточки.
    USER_WORDS_LIMIT: int = 3000

    async def get_recent_vocabulary(
        self, user_id: int, *, limit: int = 15, days: int = 7,
    ) -> list[dict]:
        """Топ N слов, которые тьютор подкидывал юзеру за последние N дней.

        Только source != 'user' — пользовательские слова идут отдельно
        через get_user_words_for_prompt (без cutoff по дате).

        Возвращает list[{word, times_used, last_seen_at}] — отсортирован
        по last_seen_at DESC (самые свежие сначала). Используется как для
        контекста next session, так и для post-session summary в Mini App.
        """
        cutoff = utcnow() - timedelta(days=days)
        res = await self.s.execute(
            select(
                UserVocabulary.word,
                UserVocabulary.times_used,
                UserVocabulary.last_seen_at,
            )
            .where(
                UserVocabulary.user_id == user_id,
                UserVocabulary.last_seen_at >= cutoff,
                UserVocabulary.source != "user",
            )
            .order_by(UserVocabulary.last_seen_at.desc())
            .limit(limit)
        )
        return [
            {
                "word": row[0],
                "times_used": int(row[1] or 1),
                "last_seen_at": row[2],
            }
            for row in res.all()
        ]

    async def get_recent_mistakes(
        self, user_id: int, *, limit: int = 5, days: int = 7,
    ) -> list[dict]:
        """Свежие ошибки юзера. Возвращает list[{category, bad, good, occurred_at}]."""
        cutoff = utcnow() - timedelta(days=days)
        res = await self.s.execute(
            select(
                UserMistake.category,
                UserMistake.bad_phrase,
                UserMistake.good_phrase,
                UserMistake.occurred_at,
            )
            .where(
                UserMistake.user_id == user_id,
                UserMistake.occurred_at >= cutoff,
            )
            .order_by(UserMistake.occurred_at.desc())
            .limit(limit)
        )
        return [
            {
                "category": row[0],
                "bad": row[1],
                "good": row[2],
                "occurred_at": row[3],
            }
            for row in res.all()
        ]

    async def get_learner_context(self, user_id: int) -> dict:
        """Контекст для подмешивания в system_prompt + UI:
          - user_words: то что юзер сам добавил (приоритет в промпте).
          - recent_vocab: что тьютор сам ввёл за последние 7 дней.
          - recent_mistakes: ошибки за неделю.
        """
        user_words = await self.get_user_words_for_prompt(user_id, limit=10)
        vocab = await self.get_recent_vocabulary(user_id, limit=15, days=7)
        mistakes = await self.get_recent_mistakes(user_id, limit=5, days=7)
        return {
            "user_words": user_words,
            "recent_vocab": vocab,
            "recent_mistakes": mistakes,
        }

    # ─── user-added words (Mini App «Мои слова») ────────────────────────
    async def get_user_words_for_prompt(
        self, user_id: int, *, limit: int = 10,
    ) -> list[str]:
        """Топ N свежих пользовательских слов (source='user').

        Без cutoff по дате — это активный учебный список юзера, не
        история. Используется для подмеса в system_prompt с пометкой
        «learner ACTIVELY WANTS to practice».
        """
        res = await self.s.execute(
            select(UserVocabulary.word)
            .where(
                UserVocabulary.user_id == user_id,
                UserVocabulary.source == "user",
            )
            .order_by(UserVocabulary.last_seen_at.desc())
            .limit(limit)
        )
        return [row[0] for row in res.all()]

    async def list_user_words(self, user_id: int) -> list[dict]:
        """Полный список пользовательских слов для Mini App.

        Возвращает list[{word, translation, note, last_seen_at, srs_box,
        srs_due_at}], сорт by last_seen_at DESC.
        """
        res = await self.s.execute(
            select(
                UserVocabulary.word,
                UserVocabulary.translation,
                UserVocabulary.note,
                UserVocabulary.last_seen_at,
                UserVocabulary.srs_box,
                UserVocabulary.srs_due_at,
            )
            .where(
                UserVocabulary.user_id == user_id,
                UserVocabulary.source == "user",
            )
            .order_by(UserVocabulary.last_seen_at.desc())
        )
        return [
            {
                "word": row[0],
                "translation": row[1],
                "note": row[2],
                "last_seen_at": row[3],
                "srs_box": int(row[4] or 0),
                "srs_due_at": row[5],
            }
            for row in res.all()
        ]

    async def count_user_words(self, user_id: int) -> int:
        """Сколько user-слов у юзера — для лимита."""
        res = await self.s.execute(
            select(func.count(UserVocabulary.id)).where(
                UserVocabulary.user_id == user_id,
                UserVocabulary.source == "user",
            )
        )
        return int(res.scalar() or 0)

    async def add_user_word(
        self,
        user_id: int,
        word: str,
        *,
        translation: Optional[str] = None,
        note: Optional[str] = None,
    ) -> str:
        """Добавить пользовательское слово.

        Параметр `translation` — перевод (RU) для SRS-карточки. Если не
        передан, остаётся как был (или NULL для нового слова).

        Возвращает:
          - "ok": вставлено / row была tutor-словом и теперь оживлено как user
          - "duplicate": слово уже есть как user-слово
          - "empty": пустая строка после нормализации
          - "too_long": > 64 символа
          - "limit_reached": достигнут USER_WORDS_LIMIT
        """
        normalized = (word or "").strip().lower()
        if not normalized:
            return "empty"
        if len(normalized) > 64:
            return "too_long"
        translation = (translation or "").strip() or None
        if translation and len(translation) > 255:
            translation = translation[:255]

        # Уже user-слово? — duplicate.
        existing = await self.s.execute(
            select(UserVocabulary.source).where(
                UserVocabulary.user_id == user_id,
                UserVocabulary.word == normalized,
            )
        )
        existing_source = existing.scalar_one_or_none()
        if existing_source == "user":
            return "duplicate"

        # Лимит — считаем только если новое слово (existing_source != 'user').
        current = await self.count_user_words(user_id)
        if current >= self.USER_WORDS_LIMIT:
            return "limit_reached"

        now = utcnow()
        stmt = mysql_insert(UserVocabulary).values(
            user_id=user_id,
            word=normalized,
            translation=translation,
            first_seen_at=now,
            last_seen_at=now,
            times_used=0,
            context=None,
            source="user",
            note=note,
            # Новая карточка сразу available для review.
            srs_box=0,
            srs_due_at=now,
        )
        # Если строка существует как tutor-слово — конвертируем в user.
        # На промоушн tutor→user также включаем SRS (due=now), чтобы новая
        # карточка появилась в ближайшем review.
        stmt = stmt.on_duplicate_key_update(
            source="user",
            last_seen_at=now,
            translation=func.coalesce(stmt.inserted.translation, UserVocabulary.translation),
            note=stmt.inserted.note,
            srs_due_at=func.coalesce(UserVocabulary.srs_due_at, now),
        )
        await self.s.execute(stmt)
        return "ok"

    async def remove_user_word(self, user_id: int, word: str) -> bool:
        """Удалить пользовательское слово. Returns True если удалили.

        Удаляем ТОЛЬКО row с source='user' — tutor-слова не трогаем
        (юзер не должен иметь возможность стереть статистику разговора).
        """
        normalized = (word or "").strip().lower()
        if not normalized:
            return False
        from sqlalchemy import delete
        res = await self.s.execute(
            delete(UserVocabulary).where(
                UserVocabulary.user_id == user_id,
                UserVocabulary.word == normalized,
                UserVocabulary.source == "user",
            )
        )
        return (res.rowcount or 0) > 0

    # ─── SRS (Leitner box) ──────────────────────────────────────────────
    # Интервалы повторения по боксам (в днях). box 0 = "только что
    # провалил, повторить сейчас же". box 5 = "выучено, не показывать
    # месяц". Список итерируется по индексу: SRS_INTERVALS_DAYS[box].
    SRS_INTERVALS_DAYS: tuple[int, ...] = (0, 1, 3, 7, 14, 30)
    SRS_MAX_BOX: int = 5

    async def count_srs_due(self, user_id: int, *, now: Optional[datetime] = None) -> int:
        """Сколько user-слов готово к повторению."""
        n = now or utcnow()
        res = await self.s.execute(
            select(func.count(UserVocabulary.id)).where(
                UserVocabulary.user_id == user_id,
                UserVocabulary.source == "user",
                UserVocabulary.srs_due_at.is_not(None),
                UserVocabulary.srs_due_at <= n,
            )
        )
        return int(res.scalar() or 0)

    async def list_srs_due(
        self, user_id: int, *, limit: int = 20, now: Optional[datetime] = None,
    ) -> list[dict]:
        """Топ-N карточек, готовых к повторению.

        Сортировка: сначала самые «просроченные» (старые due_at), чтобы
        нагнать долг. Tutor-слова не попадают — только source='user'.
        """
        n = now or utcnow()
        res = await self.s.execute(
            select(
                UserVocabulary.word,
                UserVocabulary.translation,
                UserVocabulary.srs_box,
                UserVocabulary.srs_due_at,
            )
            .where(
                UserVocabulary.user_id == user_id,
                UserVocabulary.source == "user",
                UserVocabulary.srs_due_at.is_not(None),
                UserVocabulary.srs_due_at <= n,
            )
            .order_by(UserVocabulary.srs_due_at.asc())
            .limit(limit)
        )
        return [
            {
                "word": row[0],
                "translation": row[1],
                "srs_box": int(row[2] or 0),
                "srs_due_at": row[3],
            }
            for row in res.all()
        ]

    async def record_srs_review(
        self, user_id: int, word: str, *, correct: bool,
    ) -> Optional[dict]:
        """Применить Leitner-логику к карточке после ответа юзера.

        correct=True  → box = min(box+1, MAX), srs_correct_streak += 1
        correct=False → box = 0, srs_correct_streak = 0
        srs_due_at = now + INTERVAL_FOR_BOX[new_box]
        srs_total_attempts += 1

        Возвращает {new_box, next_due_at} либо None, если карточки нет.
        """
        normalized = (word or "").strip().lower()
        if not normalized:
            return None

        res = await self.s.execute(
            select(
                UserVocabulary.id,
                UserVocabulary.srs_box,
                UserVocabulary.srs_correct_streak,
                UserVocabulary.srs_total_attempts,
            ).where(
                UserVocabulary.user_id == user_id,
                UserVocabulary.word == normalized,
                UserVocabulary.source == "user",
            )
        )
        row = res.first()
        if row is None:
            return None
        row_id, cur_box, cur_streak, cur_attempts = (
            int(row[0]), int(row[1] or 0), int(row[2] or 0), int(row[3] or 0)
        )

        if correct:
            new_box = min(cur_box + 1, self.SRS_MAX_BOX)
            new_streak = cur_streak + 1
        else:
            new_box = 0
            new_streak = 0

        now = utcnow()
        interval_days = self.SRS_INTERVALS_DAYS[new_box]
        next_due = now + timedelta(days=interval_days)

        await self.s.execute(
            update(UserVocabulary)
            .where(UserVocabulary.id == row_id)
            .values(
                srs_box=new_box,
                srs_correct_streak=new_streak,
                srs_total_attempts=cur_attempts + 1,
                srs_due_at=next_due,
                srs_last_reviewed_at=now,
                last_seen_at=now,
            )
        )
        return {"new_box": new_box, "next_due_at": next_due}

    async def get_srs_reviews_total(self, user_id: int) -> int:
        """Суммарное количество SRS-повторений за всю историю — для медалей."""
        res = await self.s.execute(
            select(func.coalesce(func.sum(UserVocabulary.srs_total_attempts), 0))
            .where(
                UserVocabulary.user_id == user_id,
                UserVocabulary.source == "user",
            )
        )
        return int(res.scalar() or 0)

    # ─── sessions ───────────────────────────────────────────────────────
    async def open_session(
        self,
        *,
        user_id: int,
        mode: str,
        level: Optional[str],
        role: Optional[str],
    ) -> SessionRow:
        row = SessionRow(
            user_id=user_id,
            started_at=utcnow(),
            mode=mode,
            level=level,
            role=role,
        )
        self.s.add(row)
        await self.s.flush()  # чтобы row.id появился
        return row

    async def close_session(self, *, session_id: int, used_seconds: int) -> None:
        await self.s.execute(
            update(SessionRow)
            .where(SessionRow.id == session_id)
            .values(ended_at=utcnow(), used_seconds=used_seconds)
        )

    # ─── settings_kv ────────────────────────────────────────────────────
    async def get_kv(self, key: str, default: Optional[str] = None) -> Optional[str]:
        res = await self.s.execute(select(SettingKV.value).where(SettingKV.key == key))
        val = res.scalar_one_or_none()
        return val if val is not None else default

    async def set_kv(self, key: str, value: str) -> None:
        now = utcnow()
        stmt = mysql_insert(SettingKV).values(key=key, value=value, updated_at=now)
        stmt = stmt.on_duplicate_key_update(value=value, updated_at=now)
        await self.s.execute(stmt)

    async def get_kv_int(self, key: str, default: int) -> int:
        v = await self.get_kv(key)
        try:
            return int(v) if v is not None else default
        except (ValueError, TypeError):
            return default

    async def get_kv_bool(self, key: str, default: bool) -> bool:
        v = await self.get_kv(key)
        if v is None:
            return default
        return v.strip().lower() in ("1", "true", "yes", "on")

    # ─── Админские запросы ───────────────────────────────────────────────────
    async def count_users(self) -> int:
        res = await self.s.execute(select(func.count(User.id)))
        return int(res.scalar() or 0)

    async def count_active_subscriptions(self) -> int:
        """Активные сейчас подписки (subscription_until > now)."""
        now = utcnow()
        res = await self.s.execute(
            select(func.count(User.id)).where(User.subscription_until > now)
        )
        return int(res.scalar() or 0)

    async def count_blocked_users(self) -> int:
        res = await self.s.execute(
            select(func.count(User.id)).where(User.is_blocked.is_(True))
        )
        return int(res.scalar() or 0)

    async def count_bot_activated(self) -> int:
        """Сколько юзеров когда-либо активировали бота в Telegram
        (написали /start или любое сообщение). См. миграцию 0009."""
        res = await self.s.execute(
            select(func.count(User.id)).where(User.bot_activated_at.is_not(None))
        )
        return int(res.scalar() or 0)

    async def count_bot_activated_today(self) -> int:
        """Сколько юзеров активировали бота сегодня (по МСК).
        Хорошо ловит органический трафик от рекламы/постов."""
        from datetime import datetime as _dt
        today = msk_today()
        day_start_utc = _dt.combine(today, _dt.min.time())
        res = await self.s.execute(
            select(func.count(User.id)).where(User.bot_activated_at >= day_start_utc)
        )
        return int(res.scalar() or 0)

    async def count_active_users_since(self, since_date: date) -> int:
        """Сколько уникальных юзеров было активно начиная с since_date включительно."""
        res = await self.s.execute(
            select(func.count(func.distinct(DailyUsage.user_id))).where(
                DailyUsage.usage_date >= since_date
            )
        )
        return int(res.scalar() or 0)

    async def total_used_seconds_today(self) -> int:
        res = await self.s.execute(
            select(func.coalesce(func.sum(DailyUsage.used_seconds), 0)).where(
                DailyUsage.usage_date == msk_today()
            )
        )
        return int(res.scalar() or 0)

    async def search_users(
        self, query: str, limit: int = 50, offset: int = 0,
    ) -> Sequence[User]:
        """Поиск юзеров по tg_id или username/имени. Пустой query = последние созданные.

        offset нужен для пагинации в админке (кнопка «Загрузить ещё»).
        """
        q = (query or "").strip()
        stmt = select(User)
        if q:
            like = f"%{q}%"
            conds = [
                User.username.like(like),
                User.first_name.like(like),
                User.last_name.like(like),
                User.email.like(like),
            ]
            if q.lstrip("-").isdigit():
                conds.append(User.tg_id == int(q))
            stmt = stmt.where(or_(*conds))
        stmt = (
            stmt.order_by(User.created_at.desc())
                .offset(offset)
                .limit(limit)
        )
        res = await self.s.execute(stmt)
        return list(res.scalars().all())

    async def delete_user(self, user_id: int) -> bool:
        """Hard-delete юзера. CASCADE подчистит связанные таблицы.

        Возвращает True если что-то удалено, False если такого юзера не было.
        Перед удалением сбрасываем tg_id (UNIQUE) — лишняя страховка.
        """
        from sqlalchemy import delete as _del
        await self.s.execute(
            update(User).where(User.id == user_id).values(tg_id=None)
        )
        res = await self.s.execute(_del(User).where(User.id == user_id))
        return (res.rowcount or 0) > 0

    async def set_blocked(self, user: User, blocked: bool) -> None:
        await self.s.execute(
            update(User).where(User.id == user.id).values(is_blocked=blocked)
        )

    async def set_reminder(
        self,
        user: User,
        *,
        enabled: Optional[bool] = None,
        reminder_hour: Optional[int] = None,
    ) -> None:
        """Обновить настройки напоминания. Час в МСК (0–23)."""
        values: dict = {}
        if enabled is not None:
            values["reminder_enabled"] = enabled
        if reminder_hour is not None:
            h = max(0, min(23, int(reminder_hour)))
            values["reminder_time"] = time(h, 0)
        if values:
            await self.s.execute(
                update(User).where(User.id == user.id).values(**values)
            )

    async def get_users_for_reminder_hour(
        self, hour_msk: int
    ) -> Sequence[User]:
        """Все юзеры, которым надо послать напоминание в этот час МСК."""
        res = await self.s.execute(
            select(User).where(
                User.reminder_enabled.is_(True),
                User.is_blocked.is_(False),
                func.hour(User.reminder_time) == hour_msk,
            )
        )
        return list(res.scalars().all())

    # ─── Win-back (retention v1, миграция 0007) ───────────────────────

    async def users_for_winback(self, *, inactive_days: int = 3,
                                cooldown_days: int = 7) -> Sequence[User]:
        """Юзеры, которым пора слать win-back:
        - reminder_enabled=TRUE, не заблокированы;
        - last_practice_date < today - inactive_days (или NULL и created_at старый);
        - last_winback_at NULL или > cooldown_days назад (анти-спам).
        """
        today = msk_today()
        inactive_cutoff = today - timedelta(days=inactive_days)
        cooldown_cutoff = utcnow() - timedelta(days=cooldown_days)

        res = await self.s.execute(
            select(User).where(
                User.reminder_enabled.is_(True),
                User.is_blocked.is_(False),
                # либо была активность давно, либо вовсе не было +
                # создан больше N дней назад (не дёргаем свежих).
                or_(
                    User.last_practice_date < inactive_cutoff,
                    and_(
                        User.last_practice_date.is_(None),
                        User.created_at < datetime.combine(inactive_cutoff, time.min),
                    ),
                ),
                or_(
                    User.last_winback_at.is_(None),
                    User.last_winback_at < cooldown_cutoff,
                ),
            )
        )
        return list(res.scalars().all())

    async def mark_winback_sent(self, user_id: int) -> None:
        await self.s.execute(
            update(User).where(User.id == user_id).values(last_winback_at=utcnow())
        )

    # ─── Progress (для /api/me/progress в mini-app) ───────────────────

    async def user_total_sessions(self, user_id: int) -> int:
        res = await self.s.execute(
            select(func.count(SessionRow.id)).where(SessionRow.user_id == user_id)
        )
        return int(res.scalar() or 0)

    async def user_total_seconds(self, user_id: int) -> int:
        res = await self.s.execute(
            select(func.coalesce(func.sum(SessionRow.used_seconds), 0))
            .where(SessionRow.user_id == user_id)
        )
        return int(res.scalar() or 0)

    async def user_total_seconds_by_mode(self, user_id: int) -> dict[str, int]:
        """{'voice': N, 'chat': N, 'listening': N} — суммарные секунды по режимам.
        Возвращает только режимы, где есть >0 секунд; нулевые ключи опускаем."""
        res = await self.s.execute(
            select(
                SessionRow.mode,
                func.coalesce(func.sum(SessionRow.used_seconds), 0),
            )
            .where(SessionRow.user_id == user_id)
            .group_by(SessionRow.mode)
        )
        return {mode: int(secs) for mode, secs in res.all() if int(secs or 0) > 0}

    async def sessions_breakdown_since(
        self, since_dt: datetime,
    ) -> dict[str, tuple[int, int]]:
        """{'voice': (count, seconds), ...} по всем юзерам, сессии с
        started_at >= since_dt. Для дашборд-карточки «Режимы сегодня»."""
        res = await self.s.execute(
            select(
                SessionRow.mode,
                func.count(SessionRow.id),
                func.coalesce(func.sum(SessionRow.used_seconds), 0),
            )
            .where(SessionRow.started_at >= since_dt)
            .group_by(SessionRow.mode)
        )
        return {mode: (int(cnt or 0), int(secs or 0)) for mode, cnt, secs in res.all()}

    async def active_user_avg_seconds_by_mode(
        self, *, min_sessions_exclusive: int = 2, since_dt: Optional[datetime] = None,
    ) -> dict:
        """Среднее время «активного» юзера по режимам.

        Активный = у кого > min_sessions_exclusive сессий (по умолчанию «заходил
        более 2 раз»). Если задан since_dt — и активность, и суммы считаются
        только по сессиям с started_at >= since_dt (окно «за N дней»).

        Среднее считается по ВСЕМ активным юзерам (режим, который юзер не трогал,
        идёт как 0) — так сумма по режимам даёт полную картину «куда уходит
        время активного юзера».

        Возвращает {active_users, total_seconds, by_mode_seconds: {mode: secs}}
        — seconds_sum по режиму делить на active_users для среднего.
        """
        active_q = select(SessionRow.user_id).group_by(SessionRow.user_id)
        if since_dt is not None:
            active_q = active_q.where(SessionRow.started_at >= since_dt)
        active_subq = active_q.having(
            func.count(SessionRow.id) > min_sessions_exclusive
        ).subquery()

        n_res = await self.s.execute(select(func.count()).select_from(active_subq))
        n = int(n_res.scalar() or 0)
        if n == 0:
            return {"active_users": 0, "total_seconds": 0, "by_mode_seconds": {}}

        sums_q = select(
            SessionRow.mode,
            func.coalesce(func.sum(SessionRow.used_seconds), 0),
        ).where(SessionRow.user_id.in_(select(active_subq.c.user_id)))
        if since_dt is not None:
            sums_q = sums_q.where(SessionRow.started_at >= since_dt)
        res = await self.s.execute(sums_q.group_by(SessionRow.mode))
        by_mode = {mode: int(secs or 0) for mode, secs in res.all()}
        total = sum(by_mode.values())
        return {"active_users": n, "total_seconds": total, "by_mode_seconds": by_mode}

    async def listening_top_categories(
        self, since_dt: datetime, *, limit: int = 5,
    ) -> list[dict]:
        """[{category, count}] — топ категорий listening-подкастов (role)
        за период since_dt..now. Для дашборда."""
        res = await self.s.execute(
            select(SessionRow.role, func.count(SessionRow.id))
            .where(
                SessionRow.mode == "listening",
                SessionRow.started_at >= since_dt,
                SessionRow.role.is_not(None),
            )
            .group_by(SessionRow.role)
            .order_by(func.count(SessionRow.id).desc())
            .limit(limit)
        )
        return [{"category": role, "count": int(cnt or 0)} for role, cnt in res.all()]

    # ─── Grammar Learn (миграция 0011) ───────────────────────────────────

    async def list_grammar_topics(self) -> Sequence[GrammarTopic]:
        """Все активные темы, отсортированные по (level, sort_order)."""
        res = await self.s.execute(
            select(GrammarTopic)
            .where(GrammarTopic.is_active.is_(True))
            .order_by(GrammarTopic.level, GrammarTopic.sort_order)
        )
        return res.scalars().all()

    async def get_grammar_topic(self, key: str) -> Optional[GrammarTopic]:
        res = await self.s.execute(
            select(GrammarTopic).where(
                GrammarTopic.key == key, GrammarTopic.is_active.is_(True),
            )
        )
        return res.scalar_one_or_none()

    async def grammar_learn_counters(self, user_id: int) -> tuple[int, int]:
        """(тем пройдено, всего активных тем) — для профиля юзера в админке."""
        done_res = await self.s.execute(
            select(func.count(UserGrammarProgress.topic_key)).where(
                UserGrammarProgress.user_id == user_id,
                UserGrammarProgress.completed_at.is_not(None),
            )
        )
        total_res = await self.s.execute(
            select(func.count(GrammarTopic.key)).where(GrammarTopic.is_active.is_(True))
        )
        return int(done_res.scalar() or 0), int(total_res.scalar() or 0)

    async def get_user_grammar_progress(self, user_id: int) -> dict[str, dict]:
        """{topic_key: {completed: bool, best_score: int, attempts: int}}"""
        res = await self.s.execute(
            select(UserGrammarProgress).where(UserGrammarProgress.user_id == user_id)
        )
        return {
            row.topic_key: {
                "completed": row.completed_at is not None,
                "best_score": int(row.best_score or 0),
                "attempts": int(row.attempts or 0),
            }
            for row in res.scalars().all()
        }

    async def get_grammar_lesson_cache(self, topic_key: str) -> Optional[GrammarLessonCache]:
        res = await self.s.execute(
            select(GrammarLessonCache).where(GrammarLessonCache.topic_key == topic_key)
        )
        return res.scalar_one_or_none()

    async def save_grammar_lesson_cache(
        self, *, topic_key: str, theory: str, exercises: list,
    ) -> None:
        """UPSERT кеша урока (гонка двух одновременных генераций — последняя побеждает)."""
        now = utcnow()
        stmt = mysql_insert(GrammarLessonCache).values(
            topic_key=topic_key,
            theory=theory,
            exercises=exercises,
            generated_at=now,
        )
        stmt = stmt.on_duplicate_key_update(
            theory=theory, exercises=exercises, generated_at=now,
        )
        await self.s.execute(stmt)

    async def upsert_grammar_progress(
        self, *, user_id: int, topic_key: str, score: int, passed: bool,
    ) -> int:
        """Записать попытку прохождения темы. Возвращает best_score после апдейта.

        completed_at ставится один раз (COALESCE) — повторные прохождения
        не сбрасывают дату первого прохождения.
        """
        now = utcnow()
        stmt = mysql_insert(UserGrammarProgress).values(
            user_id=user_id,
            topic_key=topic_key,
            completed_at=now if passed else None,
            best_score=score,
            attempts=1,
            updated_at=now,
        )
        stmt = stmt.on_duplicate_key_update(
            best_score=func.greatest(UserGrammarProgress.best_score, score),
            attempts=UserGrammarProgress.attempts + 1,
            completed_at=(
                func.coalesce(UserGrammarProgress.completed_at, now)
                if passed
                else UserGrammarProgress.completed_at
            ),
            updated_at=now,
        )
        await self.s.execute(stmt)
        res = await self.s.execute(
            select(UserGrammarProgress.best_score).where(
                UserGrammarProgress.user_id == user_id,
                UserGrammarProgress.topic_key == topic_key,
            )
        )
        return int(res.scalar() or score)

    async def user_daily_usage_series(
        self, user_id: int, days: int = 30,
    ) -> list[dict]:
        """[{date, minutes}] за последние N дней по МСК. Дни без активности
        включены с minutes=0 — фронту не нужно заполнять дырки."""
        days = max(1, min(days, 90))
        since = msk_today() - timedelta(days=days - 1)
        res = await self.s.execute(
            select(DailyUsage.usage_date, DailyUsage.used_seconds)
            .where(
                DailyUsage.user_id == user_id,
                DailyUsage.usage_date >= since,
            )
        )
        by_date = {d: int(sec) // 60 for d, sec in res.all()}
        return [
            {"date": (since + timedelta(days=i)).isoformat(),
             "minutes": by_date.get(since + timedelta(days=i), 0)}
            for i in range(days)
        ]

    async def total_revenue_rub(self) -> float:
        res = await self.s.execute(
            select(func.coalesce(func.sum(Payment.amount_rub), 0)).where(
                Payment.status == "succeeded"
            )
        )
        return float(res.scalar() or 0)

    async def recent_payments(self, limit: int = 20) -> Sequence[Payment]:
        res = await self.s.execute(
            select(Payment).order_by(Payment.created_at.desc()).limit(limit)
        )
        return list(res.scalars().all())

    # ─── Timeseries для admin v2 dashboard ──────────────────────────────
    # Все *_series возвращают РОВНО `days` точек, включая дни с value=0.
    # Так фронту не нужно заполнять дырки самому.

    async def dau_series(self, days: int = 30) -> list[dict]:
        """[{date: 'YYYY-MM-DD', value: int}] — DAU по МСК-датам."""
        days = max(1, min(days, 90))
        since = msk_today() - timedelta(days=days - 1)
        res = await self.s.execute(
            select(
                DailyUsage.usage_date,
                func.count(func.distinct(DailyUsage.user_id)),
            )
            .where(DailyUsage.usage_date >= since)
            .group_by(DailyUsage.usage_date)
        )
        by_date = {d: int(v) for d, v in res.all()}
        return [
            {"date": (since + timedelta(days=i)).isoformat(),
             "value": by_date.get(since + timedelta(days=i), 0)}
            for i in range(days)
        ]

    async def new_users_series(self, days: int = 30) -> list[dict]:
        days = max(1, min(days, 90))
        since = msk_today() - timedelta(days=days - 1)
        res = await self.s.execute(
            select(func.date(User.created_at), func.count(User.id))
            .where(User.created_at >= datetime.combine(since, time.min))
            .group_by(func.date(User.created_at))
        )
        by_date: dict[date, int] = {}
        for d, v in res.all():
            # func.date возвращает date или строку — нормализуем.
            if isinstance(d, str):
                d = date.fromisoformat(d)
            by_date[d] = int(v)
        return [
            {"date": (since + timedelta(days=i)).isoformat(),
             "value": by_date.get(since + timedelta(days=i), 0)}
            for i in range(days)
        ]

    async def revenue_series(self, days: int = 30) -> list[dict]:
        """Сумма успешных платежей по дням (UTC-датам created_at)."""
        days = max(1, min(days, 90))
        since = msk_today() - timedelta(days=days - 1)
        res = await self.s.execute(
            select(
                func.date(Payment.created_at),
                func.coalesce(func.sum(Payment.amount_rub), 0),
            )
            .where(
                Payment.status == "succeeded",
                Payment.created_at >= datetime.combine(since, time.min),
            )
            .group_by(func.date(Payment.created_at))
        )
        by_date: dict[date, float] = {}
        for d, v in res.all():
            if isinstance(d, str):
                d = date.fromisoformat(d)
            by_date[d] = float(v or 0)
        return [
            {"date": (since + timedelta(days=i)).isoformat(),
             "value": by_date.get(since + timedelta(days=i), 0.0)}
            for i in range(days)
        ]

    async def retention_cohort(self, days: int = 30) -> list[dict]:
        """Cohort-retention D1/D7/D30 за последние N дней регистраций.

        Если cohort моложе порога (например, 5-дневный cohort и d7) —
        возвращаем null, чтобы UI нарисовал «—» вместо нечестного 0%.
        """
        from sqlalchemy import text
        days = max(1, min(days, 90))
        sql = text(
            """
            SELECT
              DATE(u.created_at) AS cohort_date,
              COUNT(DISTINCT u.id) AS size,
              COUNT(DISTINCT IF(du.usage_date = DATE(u.created_at) + INTERVAL 1 DAY,
                                du.user_id, NULL)) AS d1,
              COUNT(DISTINCT IF(du.usage_date = DATE(u.created_at) + INTERVAL 7 DAY,
                                du.user_id, NULL)) AS d7,
              COUNT(DISTINCT IF(du.usage_date = DATE(u.created_at) + INTERVAL 30 DAY,
                                du.user_id, NULL)) AS d30
            FROM users u
            LEFT JOIN daily_usage du ON du.user_id = u.id
            WHERE u.created_at >= CURDATE() - INTERVAL :days DAY
            GROUP BY cohort_date
            ORDER BY cohort_date DESC
            """
        )
        res = await self.s.execute(sql, {"days": days})
        today = msk_today()
        out: list[dict] = []
        for r in res.mappings().all():
            cd = r["cohort_date"]
            if isinstance(cd, datetime):
                cd = cd.date()
            elif isinstance(cd, str):
                cd = date.fromisoformat(cd)
            age = (today - cd).days
            out.append({
                "cohort_date": cd.isoformat(),
                "size": int(r["size"] or 0),
                "d1": int(r["d1"] or 0) if age >= 1 else None,
                "d7": int(r["d7"] or 0) if age >= 7 else None,
                "d30": int(r["d30"] or 0) if age >= 30 else None,
            })
        return out

    async def user_sessions(
        self, user_id: int, limit: int = 30
    ) -> list[dict]:
        """Последние сессии юзера (метаданные, без транскриптов)."""
        limit = max(1, min(limit, 100))
        res = await self.s.execute(
            select(
                SessionRow.id, SessionRow.started_at, SessionRow.ended_at,
                SessionRow.used_seconds, SessionRow.mode,
                SessionRow.level, SessionRow.role,
            )
            .where(SessionRow.user_id == user_id)
            .order_by(SessionRow.started_at.desc())
            .limit(limit)
        )
        return [
            {
                "id": int(row.id),
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "ended_at": row.ended_at.isoformat() if row.ended_at else None,
                "used_seconds": int(row.used_seconds),
                "mode": row.mode,
                "level": row.level,
                "role": row.role,
            }
            for row in res.all()
        ]

    # ─── Массовые операции ──────────────────────────────────────────────
    async def get_active_subscribers(self) -> Sequence[User]:
        """Юзеры с активной подпиской сейчас (subscription_until > now)."""
        now = utcnow()
        res = await self.s.execute(
            select(User).where(User.subscription_until > now)
        )
        return list(res.scalars().all())

    async def get_broadcast_recipients(self) -> Sequence[User]:
        """Получатели рассылки: все незаблокированные юзеры с tg_id."""
        res = await self.s.execute(
            select(User).where(
                User.is_blocked.is_(False),
                User.tg_id.is_not(None),
            )
        )
        return list(res.scalars().all())

    async def bulk_extend_active_subscriptions(
        self,
        *,
        days: int,
        plan: str = "admin_grant",
        granted_by_tg_id: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> int:
        """Продлить подписку всем активным подписчикам на N дней.

        Возвращает количество затронутых юзеров. Создаёт Payment-запись
        для каждого для аудита.
        """
        now = utcnow()
        active = await self.get_active_subscribers()
        count = 0
        for u in active:
            base = (
                u.subscription_until
                if u.subscription_until and u.subscription_until > now
                else now
            )
            new_until = base + timedelta(days=days)
            await self.s.execute(
                update(User)
                .where(User.id == u.id)
                .values(subscription_until=new_until)
            )
            self.s.add(
                Payment(
                    user_id=u.id,
                    amount_rub=0.0,
                    plan=plan,
                    status="succeeded",
                    days_granted=days,
                    granted_by_tg_id=granted_by_tg_id,
                    notes=notes,
                    created_at=now,
                    updated_at=now,
                )
            )
            count += 1
        return count
