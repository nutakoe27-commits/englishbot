"""
recurring.py — автопродление месячной подписки через автоплатежи ЮKassa.

Поток (подробно в docs/recurring.md):

  1. Экран тарифов: месячная подписка всегда с автопродлением, условия
     (сумма, период, как отменить) написаны на карточке тарифа и в оферте.
     POST /api/payments/create {plan: monthly} → платёж с
     save_payment_method=true и metadata.recurring_setup="1".
     Отключить автопродление можно только отменой подписки в профиле.
  2. Вебхук ЮKassa: платёж succeeded и способ оплаты сохранён
     (payment_method.saved) → after_credit() заводит recurring_subscriptions
     со статусом active и next_charge_at = конец подписки − 24 ч.
  3. recurring_loop() (запускается в lifespan бэкенда, только при
     YOOKASSA_RECURRING_ENABLED):
       - remind_due(): за сутки до списания — напоминание (push + Telegram);
       - charge_due(): пора списывать → захват строки (locked_until), платёж
         по payment_method_id без участия человека. succeeded → подписка
         продлевается тем же credit_subscription_for_payment, что и обычная
         оплата; canceled → повтор через RECURRING_RETRY_HOURS, после
         RECURRING_MAX_ATTEMPTS или при отзыве карты — status=failed.
  4. Профиль: GET/POST /api/payments/recurring[/cancel|/resume].

Защита от двойного списания:
  - захват строки атомарным UPDATE … WHERE locked_until IS NULL OR < now;
  - Idempotence-Key = rec-<sub>-<next_charge_at>-<attempts>: повтор после
    сетевой ошибки возвращает тот же платёж ЮKassa, а не создаёт новый;
  - next_charge_at всегда пересчитывается от subscription_until после
    успешной оплаты, поэтому вебхук и прямой ответ ЮKassa сходятся в одно.

Флаг YOOKASSA_RECURRING_ENABLED — рубильник: выключен → галочка не
показывается и ничего не списывается (сохранённые подписки ждут).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, status
from sqlalchemy import or_, select, update

from . import auth as auth_lib
from . import yookassa as yk
from .config import settings
from .db import db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payments/recurring", tags=["Payments"])

RECURRING_PLAN = "monthly"

# Причины отказа ЮKassa, после которых повторять бессмысленно: способ
# оплаты больше не годится. Остальные (недостаточно средств, сбой банка) —
# повторяем через RECURRING_RETRY_HOURS.
PERMANENT_REASONS = {
    "permission_revoked", "card_expired", "invalid_card_number",
    "payment_method_restricted", "country_forbidden", "fraud_suspected",
}

# HTTP-ответы ЮKassa, которые не говорят о карте ничего: попытку не тратим.
TRANSIENT_HTTP = {401, 403, 429, 500, 502, 503, 504}


def enabled() -> bool:
    return bool(
        settings.YOOKASSA_RECURRING_ENABLED
        and settings.YOOKASSA_SHOP_ID and settings.YOOKASSA_SECRET_KEY
        and settings.DATABASE_URL
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _charge_at(until: Optional[datetime]) -> datetime:
    """Когда списывать: за RECURRING_CHARGE_BEFORE_HOURS до конца периода,
    но не в прошлом (иначе только что оформленная подписка списалась бы
    сразу второй раз)."""
    now = utcnow()
    if until is None:
        return now + timedelta(days=30)
    at = until - timedelta(hours=int(settings.RECURRING_CHARGE_BEFORE_HOURS))
    return max(at, now + timedelta(hours=1))


def _fmt_date(dt: Optional[datetime]) -> str:
    if dt is None:
        return ""
    msk = dt.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=3)))
    return msk.strftime("%d.%m.%Y")


def _monthly_price() -> int:
    return int(settings.SUBSCRIPTION_PRICE_MONTHLY_RUB)


# ─── Уведомления ─────────────────────────────────────────────────────────────


async def _notify(user_id: int, *, title: str, body: str, tg_text: str, tag: str) -> None:
    """Push в браузер/приложение + сообщение в Telegram. Всё best-effort."""
    from . import push
    from .db.repo import Repo
    from .db.models import User
    try:
        async with db_session() as s:
            repo = Repo(s)
            subs = await repo.list_push_subscriptions(user_id=user_id) if push.is_configured() else []
            tg_id = (await s.execute(select(User.tg_id).where(User.id == user_id))).scalar_one_or_none()
        if subs:
            payload = push.build_payload(title=title, body=body, url="/?account=1", tag=tag)
            res = await push.send_to_subscriptions(subs, payload)
            async with db_session() as s:
                repo = Repo(s)
                for sid in res.get("ok_ids", []):
                    await repo.mark_push_ok(sid)
                for sid in res.get("gone_ids", []):
                    await repo.mark_push_failed(sid, drop=True)
        if tg_id:
            from .auth import send_bot_message
            await send_bot_message(int(tg_id), tg_text)
    except Exception as e:  # noqa: BLE001
        logger.warning("[recurring] notify user_id=%s failed: %r", user_id, e)


def _fire(coro) -> None:
    try:
        asyncio.create_task(coro)
    except RuntimeError:
        logger.warning("[recurring] нет event loop — уведомление пропущено")


def dispatch(notes: list[dict]) -> None:
    """Отправить уведомления, собранные after_credit/after_cancel. Вызывать
    ПОСЛЕ commit, чтобы не уведомлять о том, что откатилось."""
    for n in notes or []:
        _fire(_notify(**n))


# ─── Реакция на результат платежа ────────────────────────────────────────────


async def _user_until(repo, user_id: int) -> Optional[datetime]:
    from .db.models import User
    return (await repo.s.execute(
        select(User.subscription_until).where(User.id == user_id)
    )).scalar_one_or_none()


async def _get_sub(repo, *, user_id: Optional[int] = None, sub_id: Optional[int] = None):
    from .db.models import RecurringSubscription as RS
    q = select(RS).where(RS.user_id == user_id) if user_id is not None else select(RS).where(RS.id == sub_id)
    return (await repo.s.execute(q)).scalar_one_or_none()


async def after_credit(repo, payment, confirmed: Optional[dict]) -> list[dict]:
    """Вызывается сразу после credit_subscription_for_payment (до commit).

    Заводит автопродление после первой оплаты с согласием, сбрасывает
    счётчик неудач после автосписания и в любом случае сдвигает
    next_charge_at от нового конца подписки. Идемпотентно: вебхук и прямой
    ответ ЮKassa могут прийти оба.
    """
    from .db.models import RecurringSubscription as RS
    notes: list[dict] = []
    if payment.plan == "org":
        return notes
    user_id = int(payment.user_id)
    until = await _user_until(repo, user_id)
    now = utcnow()
    sub = await _get_sub(repo, user_id=user_id)
    meta = (confirmed or {}).get("metadata") or {}

    if payment.recurring_subscription_id:
        # Продление прошло.
        if sub is not None:
            sub.attempts = 0
            sub.last_error = None
            sub.locked_until = None
            sub.next_charge_at = _charge_at(until)
            sub.updated_at = now
        if await repo.nudge_once(user_id=user_id, kind="recurring_renewed", dedup_key=str(payment.id)):
            amount = int(float(payment.amount_rub))
            notes.append(dict(
                user_id=user_id, tag="recurring",
                title="Подписка продлена",
                body=f"Списали {amount} ₽, доступ до {_fmt_date(until)}.",
                tg_text=(f"✅ Подписка продлена до <b>{_fmt_date(until)}</b>, списано {amount} ₽.\n"
                         "Отключить автопродление можно в профиле приложения."),
            ))
        return notes

    saved = yk.saved_method(confirmed or {})
    if meta.get("recurring_setup") == "1" and payment.plan == RECURRING_PLAN and saved:
        method_id, title = saved
        from .db.models import User
        email = (await repo.s.execute(select(User.email).where(User.id == user_id))).scalar_one_or_none()
        if sub is None:
            sub = RS(user_id=user_id, plan=RECURRING_PLAN, created_at=now)
            repo.s.add(sub)
        sub.status = "active"
        sub.payment_method_id = method_id
        sub.method_title = title
        # Продлеваем по полной цене месяца: скидка промокода действует только
        # на первую оплату (так и написано у галочки).
        sub.amount_rub = _monthly_price()
        sub.period_days = 30
        sub.email = email
        sub.next_charge_at = _charge_at(until)
        sub.attempts = 0
        sub.last_error = None
        sub.locked_until = None
        sub.reminded_for = None
        sub.canceled_at = None
        sub.updated_at = now
        await repo.s.flush()
        if await repo.nudge_once(user_id=user_id, kind="recurring_on", dedup_key=str(payment.id)):
            notes.append(dict(
                user_id=user_id, tag="recurring",
                title="Автопродление включено",
                body=f"Следующее списание {sub.amount_rub} ₽ — {_fmt_date(sub.next_charge_at)}.",
                tg_text=(f"🔁 Автопродление включено: {sub.amount_rub} ₽ каждые 30 дней, "
                         f"следующее списание {_fmt_date(sub.next_charge_at)}. "
                         "Напомним за день. Отключить — в профиле приложения."),
            ))
        return notes

    # Обычная покупка при включённом автопродлении — сдвигаем дату списания,
    # чтобы не взять деньги за уже оплаченное время.
    if sub is not None and sub.status == "active":
        sub.next_charge_at = _charge_at(until)
        sub.reminded_for = None
        sub.updated_at = now
    return notes


async def _fail(repo, sub, *, reason: str, payment_id: Optional[int]) -> list[dict]:
    """Неудачное автосписание: повтор или отключение. Возвращает уведомления."""
    now = utcnow()
    sub.attempts = int(sub.attempts or 0) + 1
    sub.last_error = (reason or "unknown")[:255]
    sub.locked_until = None
    sub.updated_at = now
    final = reason in PERMANENT_REASONS or sub.attempts >= int(settings.RECURRING_MAX_ATTEMPTS)
    key = f"{payment_id or 'x'}"
    notes: list[dict] = []
    if final:
        sub.status = "failed"
        if await repo.nudge_once(user_id=sub.user_id, kind="recurring_failed", dedup_key=key):
            notes.append(dict(
                user_id=sub.user_id, tag="recurring",
                title="Не удалось продлить подписку",
                body="Автопродление отключено. Оплатить можно в профиле.",
                tg_text=("⚠️ Не получилось списать оплату за следующий месяц, "
                         "автопродление отключено. Доступ сохранится до конца "
                         "оплаченного периода; продлить можно в профиле приложения."),
            ))
    else:
        sub.next_charge_at = now + timedelta(hours=int(settings.RECURRING_RETRY_HOURS))
        if await repo.nudge_once(user_id=sub.user_id, kind="recurring_retry", dedup_key=key):
            notes.append(dict(
                user_id=sub.user_id, tag="recurring",
                title="Не прошла оплата подписки",
                body="Попробуем ещё раз через сутки. Проверь карту.",
                tg_text=("⚠️ Не получилось списать оплату за следующий месяц "
                         f"(попытка {sub.attempts} из {settings.RECURRING_MAX_ATTEMPTS}). "
                         "Попробуем снова через сутки — проверь, что на карте есть деньги."),
            ))
    logger.info("[recurring] sub=%s fail reason=%s attempts=%s final=%s",
                sub.id, reason, sub.attempts, final)
    return notes


async def after_cancel(repo, payment, confirmed: Optional[dict]) -> list[dict]:
    """Вебхук: платёж canceled. Касается только автосписаний."""
    if not payment.recurring_subscription_id:
        return []
    sub = await _get_sub(repo, sub_id=int(payment.recurring_subscription_id))
    if sub is None or sub.status != "active":
        return []
    reason = str(((confirmed or {}).get("cancellation_details") or {}).get("reason") or "canceled")
    return await _fail(repo, sub, reason=reason, payment_id=int(payment.id))


# ─── Планировщик ─────────────────────────────────────────────────────────────


def _idem_key(sub) -> str:
    ts = int(sub.next_charge_at.replace(tzinfo=timezone.utc).timestamp())
    return f"rec-{sub.id}-{ts}-{int(sub.attempts or 0)}"


async def _claim(sub_id: int, minutes: int = 15) -> bool:
    """Атомарно забрать строку под списание. False — её уже взял другой процесс."""
    from .db.models import RecurringSubscription as RS
    now = utcnow()
    async with db_session() as s:
        res = await s.execute(
            update(RS)
            .where(
                RS.id == sub_id, RS.status == "active", RS.next_charge_at <= now,
                or_(RS.locked_until.is_(None), RS.locked_until < now),
            )
            .values(locked_until=now + timedelta(minutes=minutes), updated_at=now)
        )
        return bool(res.rowcount)


async def charge_one(sub_id: int) -> str:
    """Списать один период. Возвращает итог для логов и тестов:
    succeeded | pending | failed | retry_later | skipped."""
    from .db.repo import Repo

    async with db_session() as s:
        repo = Repo(s)
        sub = await _get_sub(repo, sub_id=sub_id)
        if sub is None or sub.status != "active":
            return "skipped"
        user = await repo.get_user_by_id(int(sub.user_id))
        if user is None:
            sub.status = "canceled"
            sub.canceled_at = utcnow()
            sub.locked_until = None
            return "skipped"
        # Страховка: подписку могли продлить мимо бэкенда (оплата в боте через
        # Telegram Payments не вызывает after_credit). Если до конца периода
        # ещё далеко — не списываем, а переносим дату.
        until = user.subscription_until
        guard = timedelta(hours=int(settings.RECURRING_CHARGE_BEFORE_HOURS) + 2)
        if until is not None and until > utcnow() + guard:
            sub.next_charge_at = _charge_at(until)
            sub.locked_until = None
            sub.reminded_for = None
            sub.updated_at = utcnow()
            logger.info("[recurring] sub=%s period ends %s — списание перенесено", sub_id, until)
            return "skipped"
        idem = _idem_key(sub)
        amount = int(sub.amount_rub)
        email = sub.email or user.email
        local = await repo.create_pending_payment(
            user_id=int(sub.user_id), plan=RECURRING_PLAN, amount_rub=amount,
            days_granted=int(sub.period_days), provider_payment_id="tmp_" + secrets.token_urlsafe(16),
            notes=f"recurring sub={sub.id} key={idem}",
        )
        local.recurring_subscription_id = int(sub.id)
        local_id, method_id, user_id = int(local.id), sub.payment_method_id, int(sub.user_id)

    resp, http = await yk.create_recurring_payment(
        payment_method_id=method_id, amount_rub=amount,
        description="English Tutor: продление подписки на месяц",
        metadata={"user_id": str(user_id), "plan": RECURRING_PLAN,
                  "payment_id": str(local_id), "recurring_subscription_id": str(sub_id)},
        customer_email=email, idempotence_key=idem,
    )

    notes: list[dict] = []
    outcome = "pending"
    async with db_session() as s:
        repo = Repo(s)
        sub = await _get_sub(repo, sub_id=sub_id)
        local = await repo.find_payment_by_id(local_id)

        if resp is None or (http in TRANSIENT_HTTP):
            # Результат неизвестен или проблема не с картой. Повторим позже с
            # тем же Idempotence-Key (attempts и next_charge_at не меняем).
            await repo.mark_payment_status(local_id, "canceled")
            local.notes = (local.notes or "") + f" | transient http={http}"
            sub.locked_until = utcnow() + timedelta(minutes=30)
            logger.warning("[recurring] sub=%s transient (http=%s) — повтор через 30 мин", sub_id, http)
            return "retry_later"

        pid = str(resp.get("id") or "")
        if not pid:
            # 4xx с описанием ошибки: платёж не создан, вина способа оплаты/данных.
            await repo.mark_payment_status(local_id, "canceled")
            reason = str(resp.get("code") or resp.get("description") or f"http_{http}")
            notes = await _fail(repo, sub, reason=reason, payment_id=local_id)
            outcome = "failed"
        else:
            existing = await repo.find_payment_by_provider_id(pid)
            if existing is not None and int(existing.id) != local_id:
                # Повтор по тому же ключу вернул уже известный платёж.
                await repo.mark_payment_status(local_id, "canceled")
                local.notes = (local.notes or "") + f" | dup of {existing.id}"
                local = existing
            else:
                local.provider_payment_id = pid
                local.updated_at = utcnow()
            await s.flush()
            st = str(resp.get("status") or "").lower()
            if st == "succeeded":
                await repo.credit_subscription_for_payment(int(local.id))
                notes = await after_credit(repo, local, resp)
                outcome = "succeeded"
                if await repo.nudge_once(user_id=user_id, kind="recurring_mt", dedup_key=str(local.id)):
                    notes.append({"_mytracker": dict(
                        user_id=user_id, payment_id=int(local.id), amount_rub=float(amount),
                        plan=RECURRING_PLAN, days=int(sub.period_days),
                    )})
            elif st == "canceled":
                if local.status != "succeeded":
                    await repo.mark_payment_status(int(local.id), "canceled")
                reason = str((resp.get("cancellation_details") or {}).get("reason") or "canceled")
                notes = await _fail(repo, sub, reason=reason, payment_id=int(local.id))
                outcome = "failed"
            else:
                # pending: ждём вебхук; строку держим, чтобы не списать дважды.
                sub.locked_until = utcnow() + timedelta(hours=6)
                outcome = "pending"
        await s.commit()

    for n in notes:
        if "_mytracker" in n:
            from . import mytracker
            mytracker.track_payment_async(**n["_mytracker"])
    dispatch([n for n in notes if "_mytracker" not in n])
    logger.info("[recurring] sub=%s charge outcome=%s", sub_id, outcome)
    return outcome


async def charge_due(limit: int = 50) -> dict:
    from .db.models import RecurringSubscription as RS
    now = utcnow()
    async with db_session() as s:
        ids = (await s.execute(
            select(RS.id).where(
                RS.status == "active", RS.next_charge_at <= now,
                or_(RS.locked_until.is_(None), RS.locked_until < now),
            ).order_by(RS.next_charge_at).limit(limit)
        )).scalars().all()
    stats: dict[str, int] = {}
    for sid in ids:
        if not await _claim(int(sid)):
            continue
        try:
            out = await charge_one(int(sid))
        except Exception as e:  # noqa: BLE001
            logger.exception("[recurring] sub=%s charge crashed: %r", sid, e)
            out = "error"
        stats[out] = stats.get(out, 0) + 1
    return stats


async def remind_due() -> int:
    """Напоминание за RECURRING_REMIND_BEFORE_HOURS до списания, один раз."""
    from .db.models import RecurringSubscription as RS
    now = utcnow()
    horizon = now + timedelta(hours=int(settings.RECURRING_REMIND_BEFORE_HOURS))
    sent = 0
    async with db_session() as s:
        rows = (await s.execute(
            select(RS).where(
                RS.status == "active", RS.next_charge_at > now, RS.next_charge_at <= horizon,
                RS.attempts == 0,
                or_(RS.reminded_for.is_(None), RS.reminded_for != RS.next_charge_at),
            ).limit(200)
        )).scalars().all()
        todo = []
        for sub in rows:
            sub.reminded_for = sub.next_charge_at
            todo.append((int(sub.user_id), int(sub.amount_rub), sub.next_charge_at))
    for user_id, amount, at in todo:
        dispatch([dict(
            user_id=user_id, tag="recurring",
            title="Завтра продлим подписку",
            body=f"Спишем {amount} ₽ за следующий месяц. Отключить — в профиле.",
            tg_text=(f"🔔 Завтра ({_fmt_date(at)}) продлим подписку: спишем {amount} ₽ "
                     "за следующий месяц. Если не нужно — отключи автопродление в профиле приложения."),
        )])
        sent += 1
    return sent


async def recurring_loop() -> None:
    logger.info("[recurring] loop started, every %ss", settings.RECURRING_LOOP_SECONDS)
    await asyncio.sleep(30)  # дать бэкенду подняться
    while True:
        try:
            if enabled():
                r = await remind_due()
                c = await charge_due()
                if r or c:
                    logger.info("[recurring] reminders=%s charges=%s", r, c)
        except Exception as e:  # noqa: BLE001
            logger.exception("[recurring] loop error: %r", e)
        await asyncio.sleep(max(60, int(settings.RECURRING_LOOP_SECONDS)))


# ─── API профиля ─────────────────────────────────────────────────────────────


def _state(sub) -> dict:
    if sub is None:
        return {"available": enabled(), "status": "none"}
    return {
        "available": enabled(),
        "status": sub.status,
        "amount_rub": int(sub.amount_rub),
        "period_days": int(sub.period_days),
        # «Z» — время в UTC, иначе браузер прочтёт его как местное.
        "next_charge_at": sub.next_charge_at.isoformat() + "Z" if sub.next_charge_at else None,
        "method_title": sub.method_title,
        "canceled_at": sub.canceled_at.isoformat() + "Z" if sub.canceled_at else None,
        "last_error": sub.last_error if sub.status == "failed" else None,
    }


@router.get("")
async def get_recurring(authorization: Optional[str] = Header(None)) -> dict:
    from .db.repo import Repo
    if not settings.DATABASE_URL:
        return {"available": False, "status": "none"}
    async with db_session() as s:
        repo = Repo(s)
        user = await auth_lib.resolve_user(repo, authorization=authorization)
        return _state(await _get_sub(repo, user_id=int(user.id)))


@router.post("/cancel")
async def cancel_recurring(authorization: Optional[str] = Header(None)) -> dict:
    """Отключить автопродление. Оплаченный период сохраняется."""
    from .db.repo import Repo
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db_not_configured")
    async with db_session() as s:
        repo = Repo(s)
        user = await auth_lib.resolve_user(repo, authorization=authorization)
        sub = await _get_sub(repo, user_id=int(user.id))
        if sub is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no_recurring")
        if sub.status == "active":
            now = utcnow()
            sub.status = "canceled"
            sub.canceled_at = now
            sub.locked_until = None
            sub.updated_at = now
            logger.info("[recurring] user_id=%s canceled autorenew", user.id)
        return _state(sub)


@router.post("/resume")
async def resume_recurring(authorization: Optional[str] = Header(None)) -> dict:
    """Снова включить автопродление сохранённым способом оплаты. Только пока
    подписка действует: после её окончания нужна новая оплата с галочкой."""
    from .db.repo import Repo
    if not enabled():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "recurring_unavailable")
    async with db_session() as s:
        repo = Repo(s)
        user = await auth_lib.resolve_user(repo, authorization=authorization)
        sub = await _get_sub(repo, user_id=int(user.id))
        if sub is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no_recurring")
        if sub.status == "failed":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "method_failed")
        until = await _user_until(repo, int(user.id))
        if until is None or until <= utcnow():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "subscription_expired")
        if sub.status != "active":
            now = utcnow()
            sub.status = "active"
            sub.canceled_at = None
            sub.attempts = 0
            sub.last_error = None
            sub.reminded_for = None
            sub.next_charge_at = _charge_at(until)
            sub.updated_at = now
            logger.info("[recurring] user_id=%s resumed autorenew", user.id)
        return _state(sub)
