"""Бизнес-репозиторий: всё, что нужно из БД, выражено как методы Repo.

Дизайн: Repo — тонкая обёртка вокруг AsyncSession. Создаётся внутри
db_session() и не переживает её. Никакой кешированной валидации.
"""

from __future__ import annotations

from datetime import datetime, date, time, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

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

        # Защита от дублей: если у обоих аккаунтов есть identity одного и
        # того же провайдера (с разными uid), молчаливый merge оставит обе
        # — у юзера в итоге окажется, например, два email/password. Это баг
        # UX. Отказываем merge со списком конфликтующих провайдеров; бот
        # покажет понятное сообщение, юзер сам решит, какой identity отвязать.
        primary_identities = await self.list_identities(primary.id)
        secondary_identities = await self.list_identities(secondary.id)
        primary_providers = {i["provider"] for i in primary_identities}
        secondary_providers = {i["provider"] for i in secondary_identities}
        conflict_providers = sorted(primary_providers & secondary_providers)
        if conflict_providers:
            return {
                "kind": "conflict",
                "primary_id": primary.id,
                "secondary_id": secondary.id,
                "conflict_providers": conflict_providers,
            }

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
        """Личная подписка ИЛИ активное место ученика в активной школе (B2B).
        Все пейволлы/лимиты/бот завязаны на эту функцию — org-доступ
        подхватывается везде автоматически."""
        if user.subscription_until is not None and user.subscription_until > utcnow():
            return True
        return await self.user_active_org(user.id) is not None

    # ─── B2B: школы (миграция 0029) ────────────────────────────────────

    async def user_active_org(self, user_id: int):
        """Активная школа юзера-ученика (org.active, срок не истёк,
        членство active, role='student'). None — юзер не школьник."""
        from .models import Organization, OrgMember
        res = await self.s.execute(
            select(Organization)
            .join(OrgMember, OrgMember.org_id == Organization.id)
            .where(
                OrgMember.user_id == user_id,
                OrgMember.active.is_(True),
                OrgMember.role == "student",
                Organization.active.is_(True),
                Organization.valid_until.is_not(None),
                Organization.valid_until > utcnow(),
            )
            .limit(1)
        )
        return res.scalar_one_or_none()

    async def get_org_by_invite(self, invite_code: str):
        """Школа по ученическому коду."""
        from .models import Organization
        code = invite_code.strip().upper()
        if not code:
            return None
        res = await self.s.execute(
            select(Organization).where(Organization.invite_code == code)
        )
        return res.scalar_one_or_none()

    async def get_org_by_any_code(self, code: str) -> tuple[object, str]:
        """Школа по ученическому ИЛИ учительскому коду.
        Возвращает (org, role): role = 'student' | 'teacher'. (None, '') —
        код не найден."""
        from .models import Organization
        norm = (code or "").strip().upper()
        if not norm:
            return None, ""
        res = await self.s.execute(
            select(Organization).where(Organization.invite_code == norm)
        )
        org = res.scalar_one_or_none()
        if org is not None:
            return org, "student"
        res = await self.s.execute(
            select(Organization).where(Organization.teacher_code == norm)
        )
        org = res.scalar_one_or_none()
        if org is not None:
            return org, "teacher"
        return None, ""

    async def org_seats_used(self, org_id: int) -> int:
        from .models import OrgMember
        res = await self.s.execute(
            select(func.count()).select_from(OrgMember).where(
                OrgMember.org_id == org_id,
                OrgMember.role == "student",
                OrgMember.active.is_(True),
            )
        )
        return int(res.scalar() or 0)

    async def join_org(self, invite_code: str, user_id: int) -> tuple[str, object]:
        """Подключить юзера к школе по инвайт-коду.
        Возвращает (status, org): 'ok' — подключён (или реактивирован),
        'already' — уже участник, 'no_seats' — мест нет,
        'invalid' — код не найден / школа неактивна / срок истёк (org=None
        только для не найденного кода)."""
        from .models import OrgMember
        org, role = await self.get_org_by_any_code(invite_code)
        if org is None:
            return "invalid", None
        if not org.active or org.valid_until is None or org.valid_until <= utcnow():
            return "invalid", org
        res = await self.s.execute(
            select(OrgMember).where(
                OrgMember.org_id == org.id, OrgMember.user_id == user_id,
            )
        )
        member = res.scalar_one_or_none()

        # Учитель мест не занимает. Если человек уже был учеником и перешёл
        # по учительской ссылке — повышаем роль и освобождаем его место.
        if role == "teacher":
            if member is not None:
                if member.active and member.role in ("teacher", "admin"):
                    return "already_teacher", org
                member.role = "teacher"
                member.active = True
            else:
                self.s.add(OrgMember(
                    org_id=org.id, user_id=user_id, role="teacher",
                    active=True, joined_at=utcnow(),
                ))
            await self.s.flush()
            return "ok_teacher", org

        if member is not None and member.active:
            return "already", org
        if await self.org_seats_used(org.id) >= org.seats_total:
            return "no_seats", org
        if member is not None:
            member.active = True
            member.role = "student"
        else:
            self.s.add(OrgMember(
                org_id=org.id, user_id=user_id, role="student",
                active=True, joined_at=utcnow(),
            ))
        await self.s.flush()
        return "ok", org

    # ─── B2B self-service: заказы школ (миграция 0030) ─────────────────

    async def create_org_order(
        self, *, payment_id: int, user_id: int, school_name: str,
        contact_person: Optional[str], contact_email: Optional[str],
        seats: int, months: int, amount_rub: int,
        kind: str = "new", target_org_id: Optional[int] = None,
    ):
        from .models import OrgOrder
        order = OrgOrder(
            payment_id=payment_id, user_id=user_id,
            school_name=school_name.strip()[:128],
            contact_person=(contact_person or "").strip()[:128] or None,
            contact_email=(contact_email or "").strip()[:255] or None,
            seats=seats, months=months, amount_rub=amount_rub,
            kind=kind, target_org_id=target_org_id,
            status="pending", created_at=utcnow(),
        )
        self.s.add(order)
        await self.s.flush()
        return order

    async def get_org_order_by_payment(self, payment_id: int):
        from .models import OrgOrder
        res = await self.s.execute(
            select(OrgOrder).where(OrgOrder.payment_id == payment_id)
        )
        return res.scalar_one_or_none()

    async def fulfill_org_order(self, payment_id: int) -> Optional[int]:
        """Оплата школы прошла: создаём организацию и делаем плательщика её
        админом. Идемпотентно — повторный вызов вебхука ничего не дублирует.
        Возвращает org_id либо None."""
        from .models import OrgMember, Payment
        order = await self.get_org_order_by_payment(payment_id)
        if order is None:
            return None
        if order.status == "done" and order.org_id:
            return int(order.org_id)          # уже выдано

        kind = getattr(order, "kind", "new") or "new"
        if kind == "new":
            org = await self.create_org(
                name=order.school_name,
                seats_total=int(order.seats),
                valid_until=utcnow() + timedelta(days=int(order.months) * 30),
                contact_email=order.contact_email,
            )
            # Плательщик становится админом школы: кабинет доступен сразу.
            self.s.add(OrgMember(
                org_id=org.id, user_id=int(order.user_id), role="admin",
                active=True, joined_at=utcnow(),
            ))
        else:
            org = await self.get_org(int(order.target_org_id or 0))
            if org is None:
                order.status = "failed"
                await self.s.flush()
                return None
            if kind == "renew":
                # Продление: считаем от большей из дат — не сжигаем остаток.
                base = org.valid_until if (
                    org.valid_until and org.valid_until > utcnow()
                ) else utcnow()
                org.valid_until = base + timedelta(days=int(order.months) * 30)
                # Продление могло идти с изменённым числом мест.
                if int(order.seats) > 0:
                    org.seats_total = int(order.seats)
                org.active = True
                # Оплаченная школа перестаёт быть пробной.
                org.is_trial = False
            elif kind == "seats":
                org.seats_total = int(org.seats_total) + int(order.seats)
        order.org_id = org.id
        order.status = "done"
        # Платёж помечаем succeeded — личную подписку при этом НЕ трогаем.
        payment = await self.s.get(Payment, int(payment_id))
        if payment is not None:
            payment.status = "succeeded"
            payment.updated_at = utcnow()
        await self.s.flush()
        return int(org.id)

    async def user_trial_org(self, user_id: int):
        """Пробная школа, заведённая этим пользователем. Нужна для правила
        «один пробный период на человека»."""
        from .models import Organization, OrgMember
        res = await self.s.execute(
            select(Organization)
            .join(OrgMember, OrgMember.org_id == Organization.id)
            .where(
                OrgMember.user_id == user_id,
                OrgMember.role == "admin",
                Organization.is_trial.is_(True),
            )
            .limit(1)
        )
        return res.scalar_one_or_none()

    async def create_trial_org(self, *, name: str, user_id: int,
                               seats: int, days: int, contact_email: Optional[str]):
        """Бесплатный пробный период: школа на N мест и N дней, плательщик —
        сразу админ. Повторно тем же юзером не выдаётся (проверка снаружи)."""
        from .models import OrgMember
        org = await self.create_org(
            name=name, seats_total=seats,
            valid_until=utcnow() + timedelta(days=days),
            contact_email=contact_email, is_trial=True,
        )
        self.s.add(OrgMember(
            org_id=org.id, user_id=user_id, role="admin",
            active=True, joined_at=utcnow(),
        ))
        await self.s.flush()
        return org

    async def create_invoice_request(self, **kw):
        """Заявка на счёт и договор для юрлица."""
        from .models import OrgInvoiceRequest
        req = OrgInvoiceRequest(
            school_name=(kw.get("school_name") or "").strip()[:128],
            inn=(kw.get("inn") or "").strip()[:32] or None,
            contact_person=(kw.get("contact_person") or "").strip()[:128] or None,
            contact_email=(kw.get("contact_email") or "").strip()[:255],
            phone=(kw.get("phone") or "").strip()[:32] or None,
            seats=int(kw.get("seats") or 0),
            months=int(kw.get("months") or 0),
            amount_rub=int(kw.get("amount_rub") or 0),
            comment=(kw.get("comment") or "").strip()[:1000] or None,
            user_id=kw.get("user_id"),
            status="new", created_at=utcnow(),
        )
        self.s.add(req)
        await self.s.flush()
        return req

    @staticmethod
    def _gen_invite_code() -> str:
        # Без похожих символов (0/O, 1/I/L) — код диктуют вслух ученикам.
        import secrets
        alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
        return "".join(secrets.choice(alphabet) for _ in range(8))

    async def _gen_unique_code(self) -> str:
        """Свободный код (проверяем оба поля — коды живут в одном
        пространстве имён, чтобы вход по коду был однозначным)."""
        for _ in range(8):
            code = self._gen_invite_code()
            org, _role = await self.get_org_by_any_code(code)
            if org is None:
                return code
        return self._gen_invite_code()

    async def create_org(
        self, *, name: str, seats_total: int,
        valid_until: Optional[datetime], contact_email: Optional[str],
        is_trial: bool = False,
    ):
        from .models import Organization
        code = await self._gen_unique_code()
        teacher_code = await self._gen_unique_code()
        org = Organization(
            name=name.strip(), invite_code=code, teacher_code=teacher_code,
            seats_total=seats_total, valid_until=valid_until, active=True,
            is_trial=is_trial,
            contact_email=(contact_email or "").strip() or None,
            created_at=utcnow(),
        )
        self.s.add(org)
        await self.s.flush()
        return org

    async def ensure_teacher_code(self, org) -> str:
        """Досоздать учительский код школам, заведённым до миграции 0031."""
        if getattr(org, "teacher_code", None):
            return org.teacher_code
        org.teacher_code = await self._gen_unique_code()
        await self.s.flush()
        return org.teacher_code

    async def list_orgs(self) -> list[dict]:
        from .models import Organization, OrgMember
        used = (
            select(OrgMember.org_id, func.count().label("used"))
            .where(OrgMember.role == "student", OrgMember.active.is_(True))
            .group_by(OrgMember.org_id)
            .subquery()
        )
        res = await self.s.execute(
            select(Organization, func.coalesce(used.c.used, 0))
            .outerjoin(used, used.c.org_id == Organization.id)
            .order_by(Organization.created_at.desc())
        )
        out = []
        for org, seats_used in res.all():
            out.append({
                "id": org.id, "name": org.name, "invite_code": org.invite_code,
                "teacher_code": getattr(org, "teacher_code", None),
                "is_trial": bool(getattr(org, "is_trial", False)),
                "seats_total": org.seats_total, "seats_used": int(seats_used or 0),
                "valid_until": org.valid_until, "active": org.active,
                "contact_email": org.contact_email, "created_at": org.created_at,
            })
        return out

    async def get_org(self, org_id: int):
        from .models import Organization
        return await self.s.get(Organization, org_id)

    async def update_org(
        self, org_id: int, *, name: Optional[str] = None,
        seats_total: Optional[int] = None,
        valid_until: Optional[datetime] = None,
        active: Optional[bool] = None,
    ) -> bool:
        org = await self.get_org(org_id)
        if org is None:
            return False
        if name is not None:
            org.name = name.strip()
        if seats_total is not None:
            org.seats_total = seats_total
        if valid_until is not None:
            org.valid_until = valid_until
        if active is not None:
            org.active = active
        await self.s.flush()
        return True

    async def list_org_members(self, org_id: int) -> list[dict]:
        from .models import OrgMember
        res = await self.s.execute(
            select(OrgMember, User)
            .join(User, User.id == OrgMember.user_id)
            .where(OrgMember.org_id == org_id)
            .order_by(OrgMember.joined_at.desc())
        )
        out = []
        for m, u in res.all():
            out.append({
                "user_id": u.id, "tg_id": u.tg_id,
                "first_name": u.first_name, "username": u.username,
                "role": m.role, "active": m.active, "joined_at": m.joined_at,
            })
        return out

    async def set_org_member_active(
        self, org_id: int, user_id: int, active: bool,
    ) -> bool:
        from .models import OrgMember
        res = await self.s.execute(
            select(OrgMember).where(
                OrgMember.org_id == org_id, OrgMember.user_id == user_id,
            )
        )
        member = res.scalar_one_or_none()
        if member is None:
            return False
        member.active = active
        await self.s.flush()
        return True

    async def set_org_member_role(
        self, org_id: int, user_id: int, role: str,
    ) -> bool:
        """student ↔ teacher ↔ admin. teacher/admin не занимают место
        (org_seats_used считает только student)."""
        from .models import OrgMember
        if role not in ("student", "teacher", "admin"):
            return False
        res = await self.s.execute(
            select(OrgMember).where(
                OrgMember.org_id == org_id, OrgMember.user_id == user_id,
            )
        )
        member = res.scalar_one_or_none()
        if member is None:
            return False
        member.role = role
        await self.s.flush()
        return True

    async def user_org_membership(self, user_id: int):
        """(Organization, role) активного участника активной непросроченной
        школы — любая роль. None — юзер не в школе. Для /api/auth/me и
        гейта кабинета (кабинет — только role teacher/admin)."""
        from .models import Organization, OrgMember
        res = await self.s.execute(
            select(Organization, OrgMember.role)
            .join(OrgMember, OrgMember.org_id == Organization.id)
            .where(
                OrgMember.user_id == user_id,
                OrgMember.active.is_(True),
                Organization.active.is_(True),
                Organization.valid_until.is_not(None),
                Organization.valid_until > utcnow(),
            )
            .limit(1)
        )
        row = res.first()
        return (row[0], row[1]) if row is not None else None

    # Ученик считается «спящим», если не занимался столько дней.
    ORG_ATTENTION_DAYS = 7

    def org_period_bounds(
        self, days: Optional[int],
    ) -> tuple[datetime, datetime, datetime, datetime]:
        """Границы текущего и предыдущего окна в наивном UTC.

        days=None — календарный МСК-месяц (как было). Иначе — последние N
        дней. Предыдущее окно той же длины нужно для динамики «стало лучше
        или хуже», без неё сводка ничего не говорит.
        """
        if days:
            end = utcnow()
            start = end - timedelta(days=int(days))
            return start, end, start - timedelta(days=int(days)), start
        first, nxt = self._month_bounds_utc()
        length = nxt - first
        return first, nxt, first - length, first

    async def _org_sessions_agg(
        self, ids: list[int], start: datetime, end: datetime,
    ) -> dict[int, tuple[int, int, int]]:
        """{user_id: (сек. говорения, сек. слушания, уроков грамматики)}."""
        if not ids:
            return {}
        speak = func.sum(
            case((SessionRow.mode.in_(("voice", "chat")), SessionRow.used_seconds), else_=0)
        )
        listen = func.sum(
            case((SessionRow.mode == "listening", SessionRow.used_seconds), else_=0)
        )
        gram = func.sum(case((SessionRow.mode == "grammar", 1), else_=0))
        agg = await self.s.execute(
            select(SessionRow.user_id, speak, listen, gram)
            .where(
                SessionRow.user_id.in_(ids),
                SessionRow.started_at >= start,
                SessionRow.started_at < end,
            )
            .group_by(SessionRow.user_id)
        )
        return {
            int(uid): (int(sp or 0), int(li or 0), int(gr or 0))
            for uid, sp, li, gr in agg.all()
        }

    async def org_students_stats(
        self, org_id: int, days: Optional[int] = None,
    ) -> list[dict]:
        """Статистика учеников школы за выбранный период.

        days=None — календарный месяц; 7/30 — последние N дней. Месячная
        статистика в первых числах показывает нули у всех, поэтому кабинет
        по умолчанию спрашивает неделю.
        """
        from .models import OrgMember
        from ..points import compute_points
        # 1) Все ученики школы (включая отключённых — с флагом active).
        res = await self.s.execute(
            select(OrgMember, User)
            .join(User, User.id == OrgMember.user_id)
            .where(OrgMember.org_id == org_id, OrgMember.role == "student")
            .order_by(OrgMember.joined_at.desc())
        )
        members = res.all()
        if not members:
            return []
        ids = [u.id for _, u in members]
        start, end, _ps, _pe = self.org_period_bounds(days)
        by_user = await self._org_sessions_agg(ids, start, end)
        today = msk_today()
        out = []
        for m, u in members:
            sp, li, gr = by_user.get(int(u.id), (0, 0, 0))
            last = u.last_practice_date
            days_since = (today - last).days if last else None
            # Классификацию делаем на сервере: фронт не должен считать даты
            # сам — он не знает про МСК и «сегодня» устройства может врать.
            if last is None:
                status = "never"
            elif days_since >= self.ORG_ATTENTION_DAYS:
                status = "attention"
            else:
                status = "ok"
            out.append({
                "user_id": u.id,
                "first_name": u.first_name,
                "username": u.username,
                "active": bool(m.active),
                "joined_at": m.joined_at,
                "speaking_min": sp // 60,
                "listening_min": li // 60,
                "grammar_lessons": gr,
                # Суммарная практика за период — главная метрика в списке.
                "practice_min": (sp + li) // 60,
                "points_month": compute_points(sp, li, gr),
                "streak_days": int(u.streak_days or 0),
                "last_practice_date": last,
                "days_since_practice": days_since,
                "status": status,
            })
        return out

    async def org_summary(self, org_id: int, days: Optional[int] = None) -> dict:
        """Сводка по школе за период + сравнение с предыдущим таким же
        периодом. Без динамики цифры не говорят, стало лучше или хуже."""
        from .models import OrgMember
        res = await self.s.execute(
            select(OrgMember, User)
            .join(User, User.id == OrgMember.user_id)
            .where(
                OrgMember.org_id == org_id,
                OrgMember.role == "student",
                OrgMember.active.is_(True),
            )
        )
        members = res.all()
        ids = [int(u.id) for _m, u in members]
        if not ids:
            return {
                "students_total": 0, "active_students": 0, "active_students_prev": 0,
                "practice_min": 0, "practice_min_prev": 0,
                "need_attention": 0, "never_started": 0,
            }
        start, end, prev_start, prev_end = self.org_period_bounds(days)
        cur = await self._org_sessions_agg(ids, start, end)
        prev = await self._org_sessions_agg(ids, prev_start, prev_end)

        def totals(agg: dict[int, tuple[int, int, int]]) -> tuple[int, int]:
            minutes = sum((sp + li) for sp, li, _g in agg.values()) // 60
            active = sum(1 for sp, li, g in agg.values() if (sp + li + g) > 0)
            return minutes, active

        cur_min, cur_active = totals(cur)
        prev_min, prev_active = totals(prev)

        today = msk_today()
        attention = never = 0
        for _m, u in members:
            if u.last_practice_date is None:
                never += 1
            elif (today - u.last_practice_date).days >= self.ORG_ATTENTION_DAYS:
                attention += 1
        return {
            "students_total": len(ids),
            "active_students": cur_active,
            "active_students_prev": prev_active,
            "practice_min": cur_min,
            "practice_min_prev": prev_min,
            "need_attention": attention,
            "never_started": never,
        }

    # ─── веб-оплата (PR-8: ЮKassa) ─────────────────────────────────────
    async def create_pending_payment(
        self,
        *,
        user_id: int,
        plan: str,
        amount_rub: int,
        days_granted: int,
        provider_payment_id: str,
        notes: Optional[str] = None,
        promo_code: Optional[str] = None,
        discount_percent: Optional[int] = None,
    ) -> Payment:
        """Создать запись о платеже в статусе pending. Используется до того,
        как webhook ЮKassa подтвердит оплату.
        """
        now = utcnow()
        p = Payment(
            user_id=user_id,
            amount_rub=float(amount_rub),
            plan=plan,
            status="pending",
            provider_payment_id=provider_payment_id,
            days_granted=days_granted,
            notes=notes,
            promo_code=promo_code,
            discount_percent=discount_percent,
            created_at=now,
            updated_at=now,
        )
        self.s.add(p)
        await self.s.flush()
        return p

    async def find_payment_by_provider_id(
        self, provider_payment_id: str,
    ) -> Optional[Payment]:
        if not provider_payment_id:
            return None
        res = await self.s.execute(
            select(Payment).where(Payment.provider_payment_id == provider_payment_id)
        )
        return res.scalar_one_or_none()

    async def find_payment_by_id(self, payment_id: int) -> Optional[Payment]:
        res = await self.s.execute(select(Payment).where(Payment.id == payment_id))
        return res.scalar_one_or_none()

    async def mark_payment_status(self, payment_id: int, status: str) -> None:
        await self.s.execute(
            update(Payment).where(Payment.id == payment_id).values(
                status=status, updated_at=utcnow(),
            )
        )

    async def credit_subscription_for_payment(self, payment_id: int) -> bool:
        """Подтверждение оплаты по webhook'у: продлеваем подписку и метим
        payment status='succeeded'. Идемпотентно: если уже succeeded — return True
        без изменений."""
        payment = await self.find_payment_by_id(payment_id)
        if payment is None:
            return False
        if payment.status == "succeeded":
            return True
        user = await self.get_user_by_id(payment.user_id)
        if user is None:
            return False
        now = utcnow()
        base = (
            user.subscription_until
            if user.subscription_until and user.subscription_until > now
            else now
        )
        new_until = base + timedelta(days=payment.days_granted)
        await self.s.execute(
            update(User).where(User.id == user.id).values(subscription_until=new_until)
        )
        await self.s.execute(
            update(Payment).where(Payment.id == payment_id).values(
                status="succeeded", updated_at=now,
            )
        )
        # Фиксируем активацию промокода (если был) — на УСПЕШНОЙ оплате.
        if payment.promo_code:
            await self.record_promo_activation(
                code=payment.promo_code,
                user_id=payment.user_id,
                payment_id=payment.id,
                discount_percent=int(payment.discount_percent or 0),
            )
        return True

    # ─── Промокоды ──────────────────────────────────────────────────────
    async def get_promo(self, code: str):
        """PromoCode по коду (любой статус) или None."""
        from .models import PromoCode
        if not code:
            return None
        res = await self.s.execute(
            select(PromoCode).where(PromoCode.code == code.strip().upper())
        )
        return res.scalar_one_or_none()

    async def promo_used_by_user(self, code: str, user_id: int) -> bool:
        """Есть ли успешная активация этого промокода у юзера (правило
        «1 раз на юзера»)."""
        from .models import PromoActivation
        res = await self.s.execute(
            select(func.count()).select_from(PromoActivation).where(
                PromoActivation.code == code.strip().upper(),
                PromoActivation.user_id == user_id,
            )
        )
        return int(res.scalar() or 0) > 0

    async def record_promo_activation(
        self, *, code: str, user_id: int, payment_id: Optional[int], discount_percent: int,
    ) -> None:
        """Записать активацию (идемпотентно через UNIQUE(code,user_id)) +
        инкремент used_count."""
        from .models import PromoActivation, PromoCode
        code = code.strip().upper()
        # Уже активировал? — ничего не делаем.
        if await self.promo_used_by_user(code, user_id):
            return
        self.s.add(PromoActivation(
            code=code, user_id=user_id, payment_id=payment_id,
            discount_percent=discount_percent, created_at=utcnow(),
        ))
        await self.s.execute(
            update(PromoCode).where(PromoCode.code == code).values(
                used_count=PromoCode.used_count + 1,
            )
        )

    async def create_promo(self, code: str, discount_percent: int):
        """Создать промокод. Возвращает PromoCode или бросает при дубликате."""
        from .models import PromoCode
        code = code.strip().upper()
        p = PromoCode(
            code=code,
            discount_percent=max(1, min(100, int(discount_percent))),
            active=True,
            used_count=0,
            created_at=utcnow(),
        )
        self.s.add(p)
        await self.s.flush()
        return p

    async def ensure_promo(self, code: str, discount_percent: int) -> None:
        """Гарантировать существование промокода (для рассылки скидки).
        Если уже есть — НЕ перетираем (мог быть изменён админом)."""
        code = code.strip().upper()
        if not code:
            return
        if await self.get_promo(code) is not None:
            return
        await self.create_promo(code, discount_percent)

    async def list_promos(self) -> list:
        from .models import PromoCode
        res = await self.s.execute(
            select(PromoCode).order_by(PromoCode.created_at.desc())
        )
        return list(res.scalars().all())

    async def set_promo_active(self, code: str, active: bool) -> bool:
        from .models import PromoCode
        code = code.strip().upper()
        res = await self.s.execute(
            update(PromoCode).where(PromoCode.code == code).values(active=active)
        )
        return (res.rowcount or 0) > 0

    async def list_promo_activations(self, code: str) -> list[dict]:
        """[{user_id, tg_id, username, discount_percent, created_at}]."""
        from .models import PromoActivation
        code = code.strip().upper()
        res = await self.s.execute(
            select(
                PromoActivation.user_id,
                PromoActivation.discount_percent,
                PromoActivation.created_at,
                User.tg_id,
                User.username,
            )
            .join(User, User.id == PromoActivation.user_id, isouter=True)
            .where(PromoActivation.code == code)
            .order_by(PromoActivation.created_at.desc())
        )
        return [
            {
                "user_id": int(uid),
                "discount_percent": int(disc or 0),
                "created_at": created.isoformat() if created else None,
                "tg_id": int(tg) if tg is not None else None,
                "username": uname,
            }
            for uid, disc, created, tg, uname in res.all()
        ]

    async def mark_tutorial_done(self, user_id: int) -> None:
        """Юзер прошёл (или скипнул) онбординг. Идемпотентно."""
        await self.s.execute(
            update(User).where(User.id == user_id, User.tutorial_done_at.is_(None))
            .values(tutorial_done_at=utcnow(), updated_at=utcnow())
        )

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

    # ─── Очки / лидерборд / уровень ─────────────────────────────────────
    def _month_bounds_utc(self) -> tuple[datetime, datetime]:
        """[начало текущего МСК-месяца, начало следующего) в наивном UTC.
        started_at в sessions хранится как naive UTC (utcnow())."""
        today = msk_today()
        first = today.replace(day=1)
        if today.month == 12:
            nxt = today.replace(year=today.year + 1, month=1, day=1)
        else:
            nxt = today.replace(month=today.month + 1, day=1)
        first_utc = (
            datetime.combine(first, time(0, 0), tzinfo=MSK)
            .astimezone(timezone.utc).replace(tzinfo=None)
        )
        nxt_utc = (
            datetime.combine(nxt, time(0, 0), tzinfo=MSK)
            .astimezone(timezone.utc).replace(tzinfo=None)
        )
        return first_utc, nxt_utc

    async def user_points(self, user_id: int, *, month_only: bool) -> int:
        """Очки юзера: speaking_min + listening_min + 5·grammar_sessions.
        month_only=True — только текущий МСК-месяц (лидерборд);
        False — за всё время (уровень)."""
        from ..points import compute_points
        conds = [SessionRow.user_id == user_id]
        if month_only:
            first_utc, nxt_utc = self._month_bounds_utc()
            conds += [SessionRow.started_at >= first_utc, SessionRow.started_at < nxt_utc]
        res = await self.s.execute(
            select(
                SessionRow.mode,
                func.count(SessionRow.id),
                func.coalesce(func.sum(SessionRow.used_seconds), 0),
            ).where(*conds).group_by(SessionRow.mode)
        )
        speak_sec = listen_sec = grammar_cnt = 0
        for mode, cnt, secs in res.all():
            secs = int(secs or 0)
            cnt = int(cnt or 0)
            if mode in ("voice", "chat"):
                speak_sec += secs
            elif mode == "listening":
                listen_sec += secs
            elif mode == "grammar":
                grammar_cnt += cnt
        return compute_points(speak_sec, listen_sec, grammar_cnt)

    def _org_students_subq(self, org_id: int):
        """Подзапрос: user_id активных учеников школы (для org-лидерборда)."""
        from .models import OrgMember
        return (
            select(OrgMember.user_id).where(
                OrgMember.org_id == org_id,
                OrgMember.role == "student",
                OrgMember.active.is_(True),
            )
        )

    async def leaderboard_month(
        self, limit: int = 3, org_id: Optional[int] = None,
    ) -> list[dict]:
        """Топ-N юзеров по очкам за текущий месяц.
        [{user_id, first_name, points}], отсортировано по убыванию очков.
        org_id — лидерборд только среди учеников школы (B2B)."""
        from ..points import GRAMMAR_POINTS
        first_utc, nxt_utc = self._month_bounds_utc()
        # Очки агрегируем прямо в SQL: minutes(speaking)+minutes(listening)+5·grammar.
        speak = func.sum(
            case((SessionRow.mode.in_(("voice", "chat")), SessionRow.used_seconds), else_=0)
        )
        listen = func.sum(
            case((SessionRow.mode == "listening", SessionRow.used_seconds), else_=0)
        )
        gram = func.sum(case((SessionRow.mode == "grammar", 1), else_=0))
        points_expr = (
            func.floor(speak / 60) + func.floor(listen / 60) + gram * GRAMMAR_POINTS
        )
        conds = [SessionRow.started_at >= first_utc, SessionRow.started_at < nxt_utc]
        if org_id is not None:
            conds.append(SessionRow.user_id.in_(self._org_students_subq(org_id)))
        res = await self.s.execute(
            select(
                SessionRow.user_id,
                User.first_name,
                points_expr.label("points"),
            )
            .join(User, User.id == SessionRow.user_id)
            .where(*conds)
            .group_by(SessionRow.user_id, User.first_name)
            .having(points_expr > 0)
            .order_by(points_expr.desc())
            .limit(limit)
        )
        return [
            {"user_id": int(uid), "first_name": fn, "points": int(pts or 0)}
            for uid, fn, pts in res.all()
        ]

    async def user_month_rank(
        self, user_id: int, org_id: Optional[int] = None,
    ) -> tuple[int, int]:
        """(rank, points) юзера в месячном лидерборде. rank = (кол-во юзеров
        с очками строго больше) + 1. Если у юзера 0 очков — rank=0.
        org_id — место внутри школьного лидерборда (B2B)."""
        my_points = await self.user_points(user_id, month_only=True)
        if my_points <= 0:
            return 0, 0
        from ..points import GRAMMAR_POINTS
        first_utc, nxt_utc = self._month_bounds_utc()
        speak = func.sum(
            case((SessionRow.mode.in_(("voice", "chat")), SessionRow.used_seconds), else_=0)
        )
        listen = func.sum(
            case((SessionRow.mode == "listening", SessionRow.used_seconds), else_=0)
        )
        gram = func.sum(case((SessionRow.mode == "grammar", 1), else_=0))
        points_expr = (
            func.floor(speak / 60) + func.floor(listen / 60) + gram * GRAMMAR_POINTS
        )
        conds = [SessionRow.started_at >= first_utc, SessionRow.started_at < nxt_utc]
        if org_id is not None:
            conds.append(SessionRow.user_id.in_(self._org_students_subq(org_id)))
        sub = (
            select(SessionRow.user_id, points_expr.label("p"))
            .where(*conds)
            .group_by(SessionRow.user_id)
            .subquery()
        )
        res = await self.s.execute(
            select(func.count()).select_from(sub).where(sub.c.p > my_points)
        )
        ahead = int(res.scalar() or 0)
        return ahead + 1, my_points

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

    async def list_payments(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        plan: Optional[str] = None,
    ) -> tuple[Sequence[Payment], int]:
        """Пагинированный список всех платежей с опциональными фильтрами."""
        q = select(Payment).order_by(Payment.created_at.desc())
        cq = select(func.count(Payment.id))
        if status:
            q = q.where(Payment.status == status)
            cq = cq.where(Payment.status == status)
        if plan:
            q = q.where(Payment.plan == plan)
            cq = cq.where(Payment.plan == plan)
        q = q.limit(limit).offset(offset)
        rows = list((await self.s.execute(q)).scalars().all())
        total = int((await self.s.execute(cq)).scalar() or 0)
        return rows, total

    async def revenue_month_chart(self) -> dict:
        """Сумма успешных платежей по дням ТЕКУЩЕГО календарного месяца
        (1-е → текущий день). Возвращает {month, days_in_month, today_day, series}."""
        today = msk_today()
        # Первое число текущего месяца
        first = today.replace(day=1)
        # Последний день месяца (для отрисовки оси даже если не дошли)
        if today.month == 12:
            next_first = today.replace(year=today.year + 1, month=1, day=1)
        else:
            next_first = today.replace(month=today.month + 1, day=1)
        days_in_month = (next_first - first).days
        # Запрос: суммы по датам с первого числа
        res = await self.s.execute(
            select(
                func.date(Payment.created_at),
                func.coalesce(func.sum(Payment.amount_rub), 0),
            )
            .where(
                Payment.status == "succeeded",
                Payment.created_at >= datetime.combine(first, time.min),
                Payment.created_at < datetime.combine(next_first, time.min),
            )
            .group_by(func.date(Payment.created_at))
        )
        by_date: dict[date, float] = {}
        for d, v in res.all():
            if isinstance(d, str):
                d = date.fromisoformat(d)
            by_date[d] = float(v or 0)
        series = [
            {
                "date": (first + timedelta(days=i)).isoformat(),
                "day": i + 1,
                "value": by_date.get(first + timedelta(days=i), 0.0),
            }
            for i in range(days_in_month)
        ]
        total = sum(p["value"] for p in series)
        return {
            "month": first.strftime("%Y-%m"),
            "days_in_month": days_in_month,
            "today_day": today.day,
            "total_rub": total,
            "series": series,
        }

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

    # ─── Рефералка и аналитические ссылки (миграция 0032) ───────────────
    # Правила продукта:
    #   • дни получает ТОЛЬКО пригласивший — за КАЖДОГО приведённого
    #     нового человека, без лимита на количество;
    #   • если получатель ссылки УЖЕ зарегистрирован в боте — дни не
    #     начисляются никому (иначе ссылку можно гонять по своим же);
    #   • одного человека нельзя «привести» дважды (UNIQUE invited_user_id);
    #   • сколько кому начислять — REFERRAL_DAYS_INVITED/_REFERRER в .env,
    #     нули просто не начисляются.

    @staticmethod
    def _gen_ref_code() -> str:
        import secrets
        alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
        return "".join(secrets.choice(alphabet) for _ in range(8))

    async def get_user_by_ref_code(self, code: str) -> Optional[User]:
        code = (code or "").strip().upper()
        if not code:
            return None
        res = await self.s.execute(
            select(User).where(User.ref_code == code).limit(1)
        )
        return res.scalar_one_or_none()

    async def ensure_ref_code(self, user: User) -> str:
        """Личный код приглашения. Выдаётся лениво — у старых юзеров NULL."""
        if user.ref_code:
            return str(user.ref_code)
        for _ in range(8):
            code = self._gen_ref_code()
            if await self.get_user_by_ref_code(code) is not None:
                continue
            res = await self.s.execute(
                update(User).where(User.id == user.id, User.ref_code.is_(None))
                .values(ref_code=code, updated_at=utcnow())
            )
            if res.rowcount:
                user.ref_code = code
                return code
            # rowcount == 0 → код успели проставить параллельно, читаем из БД
            # (скалярный select, а не сущность — identity map мог бы отдать
            # старый объект с ref_code=None).
            cur = await self.s.execute(
                select(User.ref_code).where(User.id == user.id)
            )
            existing = cur.scalar_one_or_none()
            if existing:
                user.ref_code = str(existing)
                return str(existing)
        raise RuntimeError("ref_code_generation_failed")

    async def _touch_bot_activated(self, user_id: int) -> None:
        """Проставить bot_activated_at, если он был NULL (single-write)."""
        await self.s.execute(
            update(User)
            .where(User.id == user_id, User.bot_activated_at.is_(None))
            .values(bot_activated_at=utcnow(), updated_at=utcnow())
        )

    async def referral_stats(self, user_id: int) -> dict:
        """Сколько человек привёл юзер и сколько дней за это получил."""
        from .models import Referral
        res = await self.s.execute(
            select(
                func.count(Referral.id),
                func.coalesce(func.sum(Referral.days_referrer), 0),
                func.sum(case((Referral.status == "rewarded", 1), else_=0)),
            ).where(Referral.referrer_user_id == user_id)
        )
        total, days, rewarded = res.one()
        return {
            "invited_total": int(total or 0),
            "invited_rewarded": int(rewarded or 0),
            "days_earned": int(days or 0),
        }

    async def apply_referral(
        self,
        *,
        code: str,
        tg_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        language_code: Optional[str] = None,
        days_invited: int = 7,
        days_referrer: int = 7,
    ) -> dict:
        """Обработать переход по /start ref_<code>.

        Статусы: rewarded | skipped_existing | already_referred | self |
        invalid. Коммит делает вызывающая сторона.
        """
        from .models import Referral

        referrer = await self.get_user_by_ref_code(code)
        if referrer is None:
            return {"status": "invalid"}

        # Фиксируем «был ли человек уже в боте» ДО upsert'а — после него
        # различить новичка и старожила уже нельзя.
        # Активная/бывшая подписка тоже считается признаком «уже наш юзер»
        # (аккаунт мог появиться из мини-аппа без записи в чат бота).
        before = await self.get_user_by_tg_id(tg_id)
        was_registered = before is not None and (
            before.bot_activated_at is not None or before.subscription_until is not None
        )

        invited = await self.upsert_user(
            tg_id=tg_id, username=username, first_name=first_name,
            last_name=last_name, language_code=language_code,
        )
        if invited is None:
            return {"status": "invalid"}
        await self._touch_bot_activated(int(invited.id))

        if int(invited.id) == int(referrer.id):
            return {"status": "self"}

        row_status = "skipped_existing" if was_registered else "rewarded"
        d_inv = int(days_invited) if row_status == "rewarded" else 0
        d_ref = int(days_referrer) if row_status == "rewarded" else 0

        # UNIQUE(invited_user_id): второй раз «привести» человека нельзя.
        ins = mysql_insert(Referral).values(
            referrer_user_id=int(referrer.id),
            invited_user_id=int(invited.id),
            status=row_status,
            days_invited=d_inv,
            days_referrer=d_ref,
            created_at=utcnow(),
        ).prefix_with("IGNORE")
        res = await self.s.execute(ins)
        if not res.rowcount:
            return {"status": "already_referred"}

        if row_status == "rewarded":
            # days=0 не начисляем вовсе: add_subscription_days иначе создаст
            # мусорную строку в payments и перепишет subscription_until.
            if d_inv > 0:
                await self.add_subscription_days(
                    user=invited, days=d_inv, plan="referral", amount_rub=0.0,
                    notes=f"referral: приглашён пользователем #{referrer.id}",
                )
            if d_ref > 0:
                await self.add_subscription_days(
                    user=referrer, days=d_ref, plan="referral", amount_rub=0.0,
                    notes=f"referral: пригласил пользователя #{invited.id}",
                )

        stats = await self.referral_stats(int(referrer.id))
        return {
            "status": row_status,
            "days_invited": d_inv,
            "days_referrer": d_ref,
            "referrer_user_id": int(referrer.id),
            "referrer_tg_id": int(referrer.tg_id) if referrer.tg_id else None,
            "referrer_name": (referrer.first_name or "").strip() or None,
            "invited_name": (invited.first_name or "").strip() or None,
            "invited_username": invited.username,
            "referrer_stats": stats,
        }

    # ─── Админка: сводка по рефералке ───────────────────────────────────
    async def referral_overview(self, days: int = 30) -> dict:
        from .models import Referral
        since = utcnow() - timedelta(days=int(days))
        res = await self.s.execute(
            select(
                func.count(Referral.id),
                func.sum(case((Referral.status == "rewarded", 1), else_=0)),
                func.sum(case((Referral.status == "skipped_existing", 1), else_=0)),
                func.coalesce(
                    func.sum(Referral.days_invited + Referral.days_referrer), 0,
                ),
            )
        )
        total, rewarded, skipped, days_total = res.one()
        res2 = await self.s.execute(
            select(
                func.count(Referral.id),
                func.sum(case((Referral.status == "rewarded", 1), else_=0)),
            ).where(Referral.created_at >= since)
        )
        p_total, p_rewarded = res2.one()
        res3 = await self.s.execute(
            select(func.count(func.distinct(Referral.referrer_user_id)))
        )
        referrers = res3.scalar_one_or_none() or 0
        return {
            "total": int(total or 0),
            "rewarded": int(rewarded or 0),
            "skipped_existing": int(skipped or 0),
            "days_granted": int(days_total or 0),
            "referrers": int(referrers),
            "period_days": int(days),
            "period_total": int(p_total or 0),
            "period_rewarded": int(p_rewarded or 0),
        }

    async def referral_top(self, limit: int = 20) -> list[dict]:
        """Топ пригласивших: сколько привёл и сколько дней получил."""
        from .models import Referral
        rewarded = func.sum(case((Referral.status == "rewarded", 1), else_=0))
        res = await self.s.execute(
            select(
                Referral.referrer_user_id,
                func.count(Referral.id).label("total"),
                rewarded.label("rewarded"),
                func.coalesce(func.sum(Referral.days_referrer), 0).label("days"),
                User.tg_id, User.username, User.first_name,
            )
            .join(User, User.id == Referral.referrer_user_id, isouter=True)
            .group_by(
                Referral.referrer_user_id, User.tg_id, User.username, User.first_name,
            )
            .order_by(rewarded.desc(), func.count(Referral.id).desc())
            .limit(int(limit))
        )
        return [
            {
                "user_id": int(uid),
                "total": int(total or 0),
                "rewarded": int(rew or 0),
                "days_earned": int(days or 0),
                "tg_id": int(tg) if tg is not None else None,
                "username": uname,
                "first_name": fname,
            }
            for uid, total, rew, days, tg, uname, fname in res.all()
        ]

    async def referral_feed(self, limit: int = 50) -> list[dict]:
        """Последние приглашения — кто кого привёл и чем закончилось."""
        from .models import Referral
        RefUser = aliased(User)
        InvUser = aliased(User)
        res = await self.s.execute(
            select(
                Referral.id, Referral.status, Referral.created_at,
                Referral.days_invited, Referral.days_referrer,
                Referral.referrer_user_id, RefUser.username, RefUser.first_name,
                Referral.invited_user_id, InvUser.username, InvUser.first_name,
            )
            .join(RefUser, RefUser.id == Referral.referrer_user_id, isouter=True)
            .join(InvUser, InvUser.id == Referral.invited_user_id, isouter=True)
            .order_by(Referral.id.desc())
            .limit(int(limit))
        )
        return [
            {
                "id": int(rid),
                "status": st,
                "created_at": created.isoformat() if created else None,
                "days_invited": int(di or 0),
                "days_referrer": int(dr or 0),
                "referrer_user_id": int(ruid),
                "referrer_username": run,
                "referrer_name": rfn,
                "invited_user_id": int(iuid),
                "invited_username": iun,
                "invited_name": ifn,
            }
            for (rid, st, created, di, dr, ruid, run, rfn, iuid, iun, ifn) in res.all()
        ]

    # ─── Аналитические ссылки ───────────────────────────────────────────
    async def get_ad_link_by_code(self, code: str):
        from .models import AdLink
        code = (code or "").strip()
        if not code:
            return None
        res = await self.s.execute(
            select(AdLink).where(func.lower(AdLink.code) == code.lower()).limit(1)
        )
        return res.scalar_one_or_none()

    async def create_ad_link(self, *, code: str, title: str, note: Optional[str]):
        from .models import AdLink
        link = AdLink(
            code=code.strip(), title=title.strip(),
            note=(note or "").strip() or None,
            active=True, clicks=0, created_at=utcnow(),
        )
        self.s.add(link)
        await self.s.flush()
        return link

    async def set_ad_link_active(self, link_id: int, active: bool) -> bool:
        from .models import AdLink
        res = await self.s.execute(
            update(AdLink).where(AdLink.id == int(link_id)).values(active=bool(active))
        )
        return bool(res.rowcount)

    async def delete_ad_link(self, link_id: int) -> bool:
        from sqlalchemy import delete as _delete
        from .models import AdLink, AdLinkHit
        await self.s.execute(_delete(AdLinkHit).where(AdLinkHit.link_id == int(link_id)))
        res = await self.s.execute(_delete(AdLink).where(AdLink.id == int(link_id)))
        return bool(res.rowcount)

    async def register_ad_link_hit(
        self,
        *,
        code: str,
        tg_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        language_code: Optional[str] = None,
    ) -> dict:
        """Переход по /start src_<code>. Бонусов не даёт, только считает."""
        from .models import AdLink, AdLinkHit
        link = await self.get_ad_link_by_code(code)
        if link is None:
            return {"status": "invalid"}

        before = await self.get_user_by_tg_id(tg_id)
        is_new = before is None or before.bot_activated_at is None
        user = await self.upsert_user(
            tg_id=tg_id, username=username, first_name=first_name,
            last_name=last_name, language_code=language_code,
        )
        if user is None:
            return {"status": "invalid"}
        await self._touch_bot_activated(int(user.id))

        await self.s.execute(
            update(AdLink).where(AdLink.id == link.id).values(clicks=AdLink.clicks + 1)
        )
        ins = mysql_insert(AdLinkHit).values(
            link_id=int(link.id), user_id=int(user.id),
            is_new_user=bool(is_new), created_at=utcnow(),
        ).prefix_with("IGNORE")
        await self.s.execute(ins)
        # Атрибуция юзера к первой ссылке, по которой он пришёл.
        await self.s.execute(
            update(User)
            .where(User.id == user.id, User.source_link_id.is_(None))
            .values(source_link_id=int(link.id), updated_at=utcnow())
        )
        return {
            "status": "ok",
            "title": link.title,
            "active": bool(link.active),
            "is_new_user": bool(is_new),
        }

    async def list_ad_links(self) -> list[dict]:
        """Ссылки + статистика: клики, уникальные, новые, платившие."""
        from .models import AdLink, AdLinkHit
        res = await self.s.execute(select(AdLink).order_by(AdLink.id.desc()))
        links = list(res.scalars().all())
        if not links:
            return []
        ids = [int(l.id) for l in links]

        agg = await self.s.execute(
            select(
                AdLinkHit.link_id,
                func.count(AdLinkHit.id),
                func.sum(case((AdLinkHit.is_new_user.is_(True), 1), else_=0)),
            )
            .where(AdLinkHit.link_id.in_(ids))
            .group_by(AdLinkHit.link_id)
        )
        by_link = {
            int(lid): (int(uniq or 0), int(new or 0))
            for lid, uniq, new in agg.all()
        }

        # Платящие и выручка — по атрибуции users.source_link_id.
        pay = await self.s.execute(
            select(
                User.source_link_id,
                func.count(func.distinct(Payment.user_id)),
                func.coalesce(func.sum(Payment.amount_rub), 0),
            )
            .join(Payment, Payment.user_id == User.id)
            .where(
                User.source_link_id.in_(ids),
                Payment.status == "succeeded",
                Payment.amount_rub > 0,
            )
            .group_by(User.source_link_id)
        )
        pay_by_link = {
            int(lid): (int(cnt or 0), float(amt or 0))
            for lid, cnt, amt in pay.all()
        }

        out = []
        for l in links:
            uniq, new = by_link.get(int(l.id), (0, 0))
            payers, revenue = pay_by_link.get(int(l.id), (0, 0.0))
            out.append({
                "id": int(l.id),
                "code": l.code,
                "title": l.title,
                "note": l.note,
                "active": bool(l.active),
                "clicks": int(l.clicks or 0),
                "unique_users": uniq,
                "new_users": new,
                "payers": payers,
                "revenue_rub": round(revenue, 2),
                "created_at": l.created_at.isoformat() if l.created_at else None,
            })
        return out

    async def ad_link_hits(self, link_id: int, limit: int = 100) -> list[dict]:
        from .models import AdLinkHit
        res = await self.s.execute(
            select(
                AdLinkHit.user_id, AdLinkHit.is_new_user, AdLinkHit.created_at,
                User.tg_id, User.username, User.first_name, User.subscription_until,
            )
            .join(User, User.id == AdLinkHit.user_id, isouter=True)
            .where(AdLinkHit.link_id == int(link_id))
            .order_by(AdLinkHit.id.desc())
            .limit(int(limit))
        )
        now = utcnow()
        return [
            {
                "user_id": int(uid),
                "is_new_user": bool(new),
                "created_at": created.isoformat() if created else None,
                "tg_id": int(tg) if tg is not None else None,
                "username": uname,
                "first_name": fname,
                "subscribed": bool(sub and sub > now),
            }
            for uid, new, created, tg, uname, fname, sub in res.all()
        ]

    # ─── Бесплатный welcome-триал и проактивные сообщения (0033) ────────
    # Первые FREE_TRIAL_DAYS дней после регистрации — полный доступ.
    # Окно считаем от users.created_at: так оно само работает для всех
    # путей создания юзера (бот, мини-апп, веб-регистрация) и не требует
    # ни колонки, ни правки каждого INSERT'а.

    @staticmethod
    def free_trial_until(user) -> Optional[datetime]:
        """Момент окончания welcome-триала. None — если триал отключён."""
        from ..config import settings
        days = int(getattr(settings, "FREE_TRIAL_DAYS", 0) or 0)
        if days <= 0 or user is None or user.created_at is None:
            return None
        return user.created_at + timedelta(days=days)

    @classmethod
    def in_free_trial(cls, user) -> bool:
        until = cls.free_trial_until(user)
        return until is not None and until > utcnow()

    async def has_full_access(self, user) -> bool:
        """Полный доступ = активная подписка/место в школе ИЛИ welcome-триал."""
        if self.in_free_trial(user):
            return True
        return await self.has_active_subscription(user)

    async def nudge_once(
        self, *, user_id: int, kind: str, dedup_key: str = "",
    ) -> bool:
        """Отметить проактивное сообщение. True — отправляем (ещё не слали),
        False — уже слали такое. Коммит на вызывающей стороне."""
        from .models import UserNudge
        ins = mysql_insert(UserNudge).values(
            user_id=int(user_id), kind=kind, dedup_key=(dedup_key or "")[:64],
            created_at=utcnow(),
        ).prefix_with("IGNORE")
        res = await self.s.execute(ins)
        return bool(res.rowcount)

    async def count_active_days(
        self, user_id: int, *, start: date, end: date,
    ) -> int:
        """Сколько дней в [start, end] юзер вообще занимался."""
        res = await self.s.execute(
            select(func.count(func.distinct(DailyUsage.usage_date))).where(
                DailyUsage.user_id == int(user_id),
                DailyUsage.usage_date >= start,
                DailyUsage.usage_date <= end,
                DailyUsage.used_seconds > 0,
            )
        )
        return int(res.scalar_one_or_none() or 0)

    async def has_used_plan(self, user_id: int, plan: str) -> bool:
        """Покупал ли юзер этот тариф (успешно). Для разовых — trial7."""
        res = await self.s.execute(
            select(func.count(Payment.id)).where(
                Payment.user_id == int(user_id),
                Payment.plan == plan,
                Payment.status == "succeeded",
            )
        )
        return int(res.scalar_one_or_none() or 0) > 0

    # ─── История тем подкастов (миграция 0034) ──────────────────────────
    async def recent_listening_topics(
        self, user_id: int, *, category: Optional[str] = None, limit: int = 12,
    ) -> list[str]:
        """Последние темы юзера — чтобы попросить модель их не повторять."""
        from .models import ListeningTopic
        q = select(ListeningTopic.topic).where(ListeningTopic.user_id == int(user_id))
        if category:
            q = q.where(ListeningTopic.category == category)
        res = await self.s.execute(
            q.order_by(ListeningTopic.id.desc()).limit(int(limit))
        )
        return [t for (t,) in res.all() if t]

    async def add_listening_topic(
        self, *, user_id: int, category: str, topic: str,
    ) -> None:
        from .models import ListeningTopic
        topic = (topic or "").strip()[:160]
        if not topic:
            return
        self.s.add(ListeningTopic(
            user_id=int(user_id), category=(category or "")[:32],
            topic=topic, created_at=utcnow(),
        ))

    # ─── Тест уровня (миграция 0035) ────────────────────────────────────
    async def save_level_test(
        self, *, user_id: int, cefr: str, correct_cnt: int, total_cnt: int,
        answers: list[dict],
    ) -> int:
        """Записать прохождение и проставить уровень юзеру."""
        from .models import LevelTest
        row = LevelTest(
            user_id=int(user_id), cefr=cefr,
            correct_cnt=int(correct_cnt), total_cnt=int(total_cnt),
            answers={"items": answers}, created_at=utcnow(),
        )
        self.s.add(row)
        await self.s.flush()
        await self.s.execute(
            update(User).where(User.id == int(user_id)).values(
                cefr_level=cefr, cefr_tested_at=utcnow(), updated_at=utcnow(),
            )
        )
        return int(row.id)

    async def attach_level_test_report(self, test_id: int, report: str) -> None:
        from .models import LevelTest
        await self.s.execute(
            update(LevelTest).where(LevelTest.id == int(test_id))
            .values(report=(report or "")[:8000])
        )

    async def last_level_test(self, user_id: int) -> Optional[dict]:
        """Предыдущее прохождение — чтобы показать динамику уровня."""
        from .models import LevelTest
        res = await self.s.execute(
            select(LevelTest.cefr, LevelTest.created_at, LevelTest.correct_cnt,
                   LevelTest.total_cnt)
            .where(LevelTest.user_id == int(user_id))
            .order_by(LevelTest.id.desc()).limit(1)
        )
        row = res.first()
        if row is None:
            return None
        cefr, created, ok, total = row
        return {
            "cefr": cefr,
            "created_at": created.isoformat() if created else None,
            "correct_cnt": int(ok or 0), "total_cnt": int(total or 0),
        }
