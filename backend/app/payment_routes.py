"""
payment_routes.py — веб-оплата подписки через ЮKassa (PR-8).

Поток:
  1. Юзер на сайте кликает тариф → POST /api/payments/create →
     backend создаёт pending Payment, дёргает ЮKassa, отдаёт
     confirmation_url.
  2. Браузер редиректится на ЮKassa → юзер платит → ЮKassa редиректит
     обратно на return_url=<MINIAPP_URL>/?payment_id=<local>.
  3. Параллельно ЮKassa стучится на POST /api/payments/yookassa/webhook.
     Webhook делает СВОЙ GET в api.yookassa.ru/v3/payments/<id> (защита
     от подделок) и при status='succeeded' продлевает подписку через
     repo.credit_subscription_for_payment (идемпотентно).
  4. Фронт после возврата опрашивает GET /api/payments/status?payment_id=<local>
     до status='succeeded'.

Telegram-бот живёт параллельно (Telegram Payments + provider_token), здесь
не трогаем.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from . import auth as auth_lib
from . import yookassa as yk
from .config import settings
from .db import db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payments", tags=["Payments"])


# ─── Каталог тарифов (синхронизирован с bot/app/main.py:_PLAN_CATALOG) ──
# Порядок ключей = порядок карточек на экране подписки.
# trial7 — первая ступень лестницы: дешёвый вход, дальше апсейл на год тем,
# кто реально занимался. «Рекомендуем» стоит на месяце: рекомендовать год
# незнакомому продукту — поднимать тревожность там, где её надо снимать.
def _plan_catalog() -> dict[str, dict]:
    return {
        "trial7":  {"days": 7,   "amount_rub": settings.SUBSCRIPTION_PRICE_TRIAL7_RUB,
                    "title": "Пробная неделя", "badge": "Попробовать",
                    "note": "Полный доступ на 7 дней. Один раз на аккаунт."},
        "monthly": {"days": 30,  "amount_rub": settings.SUBSCRIPTION_PRICE_MONTHLY_RUB,
                    "title": "Подписка на месяц", "badge": "Рекомендуем"},
        "yearly":  {"days": 365, "amount_rub": settings.SUBSCRIPTION_PRICE_YEARLY_RUB,
                    "title": "Подписка на год"},
        "twoyear": {"days": 730, "amount_rub": settings.SUBSCRIPTION_PRICE_TWOYEAR_RUB,
                    "title": "Подписка на 2 года"},
    }


# Тариф, который можно купить только один раз на аккаунт.
ONE_TIME_PLANS = ("trial7",)


class _CreatePaymentIn(BaseModel):
    plan: str                          # trial7 | monthly | yearly | twoyear
    email: Optional[str] = None        # для 54-ФЗ чека, если у юзера ещё нет
    promo_code: Optional[str] = None   # промокод для скидки (опционально)


def _require_db() -> None:
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db_not_configured")


def _return_url(payment_id: int) -> str:
    base = (settings.MINIAPP_URL or "").rstrip("/") or "/"
    return f"{base}/?payment_id={payment_id}"


def _org_return_url(payment_id: int) -> str:
    """Возврат после оплаты школы — на лендинг /schools, где показываются
    статус заказа и ссылки-приглашения (в приложении этого экрана нет)."""
    base = (settings.MINIAPP_URL or "").rstrip("/")
    return f"{base}/schools?payment_id={payment_id}&org=1"


@router.get("/plans")
async def list_plans(authorization: Optional[str] = Header(None)) -> dict:
    """Список тарифов для страницы подписки.

    Auth необязателен: с ним прячем разовые тарифы (trial7), которые юзер
    уже покупал, — чтобы не показывать кнопку, которая всё равно откажет.
    """
    used_one_time: set[str] = set()
    if authorization and settings.DATABASE_URL:
        from .db import Repo
        try:
            async with db_session() as session:
                repo = Repo(session)
                user = await auth_lib.resolve_user(repo, authorization=authorization)
                for plan_key in ONE_TIME_PLANS:
                    if await repo.has_used_plan(user.id, plan_key):
                        used_one_time.add(plan_key)
        except Exception:
            # Неавторизованный/битый токен — просто показываем полный список.
            used_one_time = set()
    return {"plans": [
        {
            "key": k, "days": v["days"], "amount_rub": v["amount_rub"],
            "title": v["title"], "badge": v.get("badge"), "note": v.get("note"),
        }
        for k, v in _plan_catalog().items()
        if k not in used_one_time
    ]}


@router.get("/promo/check")
async def promo_check(
    code: str, authorization: Optional[str] = Header(None),
) -> dict:
    """Проверить промокод до оплаты — фронт показывает цену со скидкой.
    {valid, discount_percent, already_used}."""
    _require_db()
    from .db import Repo
    norm = (code or "").strip().upper()
    if not norm:
        return {"valid": False, "discount_percent": 0, "already_used": False}
    async with db_session() as session:
        repo = Repo(session)
        user = await auth_lib.resolve_user(repo, authorization=authorization)
        promo = await repo.get_promo(norm)
        if not promo or not promo.active:
            return {"valid": False, "discount_percent": 0, "already_used": False}
        used = await repo.promo_used_by_user(norm, user.id)
        return {
            "valid": not used,
            "discount_percent": int(promo.discount_percent),
            "already_used": used,
        }


@router.post("/create")
async def create_payment(
    body: _CreatePaymentIn, authorization: Optional[str] = Header(None),
) -> dict:
    """Создать платёж в ЮKassa. Возвращает confirmation_url + локальный
    payment_id (для последующего опроса статуса)."""
    _require_db()
    catalog = _plan_catalog()
    plan = (body.plan or "").strip().lower()
    if plan not in catalog:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_plan")
    info = catalog[plan]

    if not settings.YOOKASSA_SHOP_ID or not settings.YOOKASSA_SECRET_KEY:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "yookassa_not_configured")

    # Итоговая сумма (со скидкой промокода) и метаданные промо — вычисляются
    # внутри db-блока, используются дальше при вызове ЮKassa.
    final_amount = int(info["amount_rub"])
    promo_pct = 0
    promo_code_norm: Optional[str] = None

    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await auth_lib.resolve_user(repo, authorization=authorization)

        # Разовые тарифы (пробная неделя) — строго один раз на аккаунт,
        # иначе вместо подписки люди будут покупать триал по кругу.
        if plan in ONE_TIME_PLANS and await repo.has_used_plan(user.id, plan):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "plan_already_used")

        # Промокод: валидируем, проверяем «1 раз на юзера», применяем скидку.
        if body.promo_code:
            promo_code_norm = body.promo_code.strip().upper()
            promo = await repo.get_promo(promo_code_norm)
            if not promo or not promo.active:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "promo_invalid")
            if await repo.promo_used_by_user(promo_code_norm, user.id):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "promo_already_used")
            promo_pct = int(promo.discount_percent)
            final_amount = max(1, round(int(info["amount_rub"]) * (100 - promo_pct) / 100))

        # Email для чека: приоритет — переданный в body (свежий ввод от юзера),
        # потом users.email. Если фискализация включена и email нигде нет — 400.
        email = (body.email or "").strip() or user.email
        if settings.YOOKASSA_FISCALIZATION and not email:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "email_required")
        # Если юзер ввёл новый email, и у него email пустой — сохраним.
        if body.email and not user.email:
            from sqlalchemy import update as _upd
            from .db.models import User as _U
            from .db.repo import utcnow as _now
            await session.execute(
                _upd(_U).where(_U.id == user.id).values(
                    email=body.email.strip(), updated_at=_now(),
                )
            )

        # 1) Создаём pending payment локально, чтобы получить ID для metadata.
        #    provider_payment_id поставим заглушкой и обновим после ответа ЮKassa.
        import secrets as _secrets
        tmp_pid = "tmp_" + _secrets.token_urlsafe(16)
        payment = await repo.create_pending_payment(
            user_id=user.id,
            plan=plan,
            amount_rub=final_amount,
            days_granted=info["days"],
            provider_payment_id=tmp_pid,
            notes=f"web yookassa shop={settings.YOOKASSA_SHOP_ID}",
            promo_code=promo_code_norm,
            discount_percent=promo_pct or None,
        )
        await session.commit()
        local_payment_id = int(payment.id)

    # 2) Зовём ЮKassa. user.id и плата — в metadata, обратный adres — return_url.
    yk_resp = await yk.create_payment(
        amount_rub=final_amount,
        description=f"English Tutor: {info['title']}",
        return_url=_return_url(local_payment_id),
        metadata={
            "user_id": str(user.id),
            "plan": plan,
            "payment_id": str(local_payment_id),
        },
        customer_email=email,
    )
    if not yk_resp or "id" not in yk_resp:
        async with db_session() as session:
            repo = Repo(session)
            await repo.mark_payment_status(local_payment_id, "canceled")
            await session.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "yookassa_create_failed")

    provider_pid = str(yk_resp["id"])
    confirmation_url = (yk_resp.get("confirmation") or {}).get("confirmation_url")
    if not confirmation_url:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "no_confirmation_url")

    # 3) Подставляем настоящий provider_payment_id.
    async with db_session() as session:
        repo = Repo(session)
        from sqlalchemy import update as _upd
        from .db.models import Payment as _P
        from .db.repo import utcnow as _now
        await session.execute(
            _upd(_P).where(_P.id == local_payment_id).values(
                provider_payment_id=provider_pid, updated_at=_now(),
            )
        )
        await session.commit()

    return {
        "payment_id": local_payment_id,
        "provider_payment_id": provider_pid,
        "confirmation_url": confirmation_url,
        "amount_rub": final_amount,
        "days": info["days"],
        "promo_code": promo_code_norm,
        "discount_percent": promo_pct,
    }


@router.get("/status")
async def payment_status(
    payment_id: int, authorization: Optional[str] = Header(None),
) -> dict:
    """Опрос локального статуса платежа. Webhook обновляет его независимо."""
    _require_db()
    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await auth_lib.resolve_user(repo, authorization=authorization)
        payment = await repo.find_payment_by_id(int(payment_id))
        if payment is None or payment.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        return {
            "payment_id": int(payment.id),
            "status": payment.status,
            "plan": payment.plan,
            "amount_rub": float(payment.amount_rub),
            "days_granted": int(payment.days_granted),
        }



def _notify_payment_success(*, tg_id: int, plan: str, days: int, until) -> None:
    """Онбординг после оплаты (миграция 0033).

    Первая неделя решает, останется человек или нет. Поэтому вместо
    «лимиты сняты» даём конкретный план: сколько, как часто и что делать
    после занятия. Fire-and-forget, ошибки только в лог.
    """
    import asyncio
    from .auth import send_bot_message

    until_str = ""
    if until is not None:
        try:
            until_str = until.strftime("%d.%m.%Y")
        except Exception:
            until_str = ""

    head = (
        "✅ <b>Оплата прошла. Полный доступ открыт"
        + (f" до {until_str}" if until_str else "")
        + ".</b>"
    )
    plan_lines = (
        "<b>План на неделю:</b>\n"
        "• 15–20 минут разговора в день, лучше в одно и то же время\n"
        "• 5 дней в неделю — это важнее, чем два раза по часу\n"
        "• после каждого разговора смотри разбор ошибок — рост именно там"
    )
    if plan == "trial7":
        tail = (
            "У тебя <b>7 дней</b> без ограничений. Этого хватит, чтобы понять, "
            "твоё это или нет — но только если заниматься, а не откладывать.\n\n"
            + plan_lines
            + "\n\nПозанимаешься 4 дня из 7 — в конце пришлю особые условия на год."
        )
    else:
        tail = plan_lines + (
            f"\n\nЧерез {min(max(days, 1), 7)} дней загляну, как идёт. "
            "Если пропадёшь — напомню. 🙂"
        )
    text = (
        f"{head}\n\n{tail}\n\n"
        "Поставь удобное время напоминания: /reminder"
    )
    try:
        asyncio.create_task(send_bot_message(int(tg_id), text))
    except RuntimeError:
        logger.warning("[payments] нет event loop для welcome-сообщения")


@router.post("/yookassa/webhook")
async def yookassa_webhook(request: Request) -> dict:
    """Нотификация от ЮKassa. Доверяем не телу запроса, а **проверке через
    GET /v3/payments/<id>** — защита от подделанных webhook'ов.

    ЮKassa шлёт event'ы: payment.succeeded, payment.canceled,
    payment.waiting_for_capture, refund.succeeded.

    Всегда отвечаем 200 — иначе ЮKassa будет ретраить, что усложнит
    отладку. Логика идемпотентна.
    """
    _require_db()
    try:
        body = await request.json()
    except Exception:
        logger.warning("[yookassa/webhook] bad json")
        return {"ok": True}

    event = body.get("event") or ""
    obj = body.get("object") or {}
    provider_pid = str(obj.get("id") or "").strip()
    if not provider_pid:
        logger.warning("[yookassa/webhook] no object.id, event=%s", event)
        return {"ok": True}

    # Подтверждаем у ЮKassa напрямую.
    confirmed = await yk.fetch_payment(provider_pid)
    if confirmed is None:
        logger.warning("[yookassa/webhook] fetch_payment failed for %s", provider_pid)
        return {"ok": True}
    real_status = str(confirmed.get("status") or "").lower()

    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        payment = await repo.find_payment_by_provider_id(provider_pid)
        if payment is None:
            logger.warning(
                "[yookassa/webhook] no local payment for provider_id=%s (event=%s)",
                provider_pid, event,
            )
            return {"ok": True}

        if real_status == "succeeded":
            if payment.plan == "org":
                # B2B: создаём школу и делаем плательщика её админом.
                # Личную подписку такой платёж не продлевает.
                org_id = await repo.fulfill_org_order(int(payment.id))
                await session.commit()
                logger.info(
                    "[yookassa/webhook] org fulfilled payment_id=%s org_id=%s amount=%s",
                    payment.id, org_id, payment.amount_rub,
                )
            else:
                await repo.credit_subscription_for_payment(int(payment.id))
                # Онбординг после оплаты: без него человек остаётся один на
                # один с «лимиты сняты» и отваливается на первой неделе.
                # nudge_once — чтобы ретрай вебхука не прислал второе.
                should_welcome = await repo.nudge_once(
                    user_id=int(payment.user_id), kind="paid_welcome",
                    dedup_key=str(payment.id),
                )
                # Читаем скаляром, а не через ORM-объект: identity map мог бы
                # отдать subscription_until до только что сделанного UPDATE.
                from sqlalchemy import select as _sel
                from .db.models import User as _U
                _row = (await repo.s.execute(
                    _sel(_U.tg_id, _U.subscription_until).where(
                        _U.id == int(payment.user_id)
                    )
                )).one_or_none()
                buyer_tg_id, buyer_until = _row if _row else (None, None)
                await session.commit()
                logger.info(
                    "[yookassa/webhook] credited user_id=%s plan=%s days=%s amount=%s",
                    payment.user_id, payment.plan, payment.days_granted, payment.amount_rub,
                )
                if should_welcome and buyer_tg_id:
                    _notify_payment_success(
                        tg_id=int(buyer_tg_id),
                        plan=str(payment.plan),
                        days=int(payment.days_granted or 0),
                        until=buyer_until,
                    )
        elif real_status == "canceled":
            if payment.status != "succeeded":  # не отменяем то, что уже зачтено
                await repo.mark_payment_status(int(payment.id), "canceled")
                await session.commit()
        else:
            # pending / waiting_for_capture — ничего не меняем.
            logger.info(
                "[yookassa/webhook] noop status=%s for payment_id=%s",
                real_status, payment.id,
            )

    return {"ok": True}


# ─── B2B: самостоятельное подключение школы (миграция 0030) ──────────────────

@router.get("/org/pricing")
async def org_pricing_limits() -> dict:
    """Параметры тарификации для калькулятора на лендинге. Без авторизации."""
    from . import org_pricing
    return org_pricing.limits()


class _OrgQuoteIn(BaseModel):
    seats: int
    months: int


@router.post("/org/quote")
async def org_quote(body: _OrgQuoteIn) -> dict:
    """Расчёт стоимости пакета. Публичный — используется калькулятором."""
    from . import org_pricing
    try:
        return org_pricing.quote(body.seats, body.months)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


class _OrgCheckoutIn(BaseModel):
    school_name: str
    seats: int
    months: int
    contact_person: Optional[str] = None
    email: Optional[str] = None        # для чека 54-ФЗ и связи со школой
    # Продление / докупка мест к существующей школе (кабинет школы).
    kind: str = "new"                  # new | renew | seats
    org_id: Optional[int] = None


@router.post("/org/checkout")
async def org_checkout(
    body: _OrgCheckoutIn, authorization: Optional[str] = Header(None),
) -> dict:
    """Оформление пакета мест: заказ + платёж ЮKassa.

    Сумма считается на сервере (org_pricing) — значение из браузера не
    принимается. После успешной оплаты вебхук создаёт школу и делает
    плательщика её администратором.
    """
    _require_db()
    from . import org_pricing
    from .db import Repo

    kind = (body.kind or "new").strip().lower()
    if kind not in ("new", "renew", "seats"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_kind")

    if not settings.YOOKASSA_SHOP_ID or not settings.YOOKASSA_SECRET_KEY:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "yookassa_not_configured")

    school_name = (body.school_name or "").strip()
    target_org_id: Optional[int] = None
    seats_for_order = int(body.seats or 0)
    months_for_order = int(body.months or 0)

    async with db_session() as session:
        repo = Repo(session)
        user = await auth_lib.resolve_user(repo, authorization=authorization)

        if kind == "new":
            if not school_name or len(school_name) > 128:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_school_name")
            try:
                calc = org_pricing.quote(body.seats, body.months)
            except ValueError as exc:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
            amount = int(calc["total_rub"])
            seats_for_order = int(calc["seats"])
            months_for_order = int(calc["months"])
            description = (
                f"English Tutor для школ: {seats_for_order} мест на "
                f"{months_for_order} мес."
            )
        else:
            # Продление и докупка — только для своей школы (teacher/admin).
            org, role = await _resolve_staff_org(repo, user, body.org_id)
            target_org_id = int(org.id)
            school_name = org.name
            if kind == "renew":
                try:
                    calc = org_pricing.quote(
                        body.seats or org.seats_total, body.months,
                    )
                except ValueError as exc:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
                amount = int(calc["total_rub"])
                seats_for_order = int(calc["seats"])
                months_for_order = int(calc["months"])
                description = (
                    f"English Tutor: продление школы «{org.name}» — "
                    f"{seats_for_order} мест на {months_for_order} мес."
                )
            else:  # seats
                remaining = _remaining_days(org)
                if remaining < 1:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, "expired")
                try:
                    calc = org_pricing.quote_addon(
                        body.seats, remaining, int(org.seats_total) + int(body.seats),
                    )
                except ValueError as exc:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
                amount = int(calc["total_rub"])
                seats_for_order = int(calc["seats_add"])
                months_for_order = 0
                description = (
                    f"English Tutor: +{seats_for_order} мест к школе «{org.name}» "
                    f"до конца срока"
                )

        email = (body.email or "").strip() or user.email
        if settings.YOOKASSA_FISCALIZATION and not email:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "email_required")
        if body.email and not user.email:
            from sqlalchemy import update as _upd
            from .db.models import User as _U
            from .db.repo import utcnow as _now
            await session.execute(
                _upd(_U).where(_U.id == user.id).values(
                    email=email, updated_at=_now(),
                )
            )

        import secrets as _secrets
        tmp_pid = "tmp_" + _secrets.token_urlsafe(16)
        payment = await repo.create_pending_payment(
            user_id=user.id,
            plan="org",
            amount_rub=amount,
            days_granted=int(calc.get("days") or 0),
            provider_payment_id=tmp_pid,
            notes=f"org {kind}: {school_name} · {description}"[:500],
        )
        await session.flush()
        local_payment_id = int(payment.id)
        await repo.create_org_order(
            payment_id=local_payment_id,
            user_id=int(user.id),
            school_name=school_name,
            contact_person=body.contact_person,
            contact_email=email,
            seats=seats_for_order,
            months=months_for_order,
            amount_rub=amount,
            kind=kind,
            target_org_id=target_org_id,
        )
        await session.commit()

    yk_resp = await yk.create_payment(
        amount_rub=amount,
        description=description,
        return_url=_org_return_url(local_payment_id),
        metadata={
            "user_id": str(user.id),
            "plan": "org",
            "payment_id": str(local_payment_id),
        },
        customer_email=email,
    )
    if not yk_resp or "id" not in yk_resp:
        async with db_session() as session:
            repo = Repo(session)
            await repo.mark_payment_status(local_payment_id, "canceled")
            await session.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "yookassa_create_failed")

    provider_pid = str(yk_resp["id"])
    confirmation_url = (yk_resp.get("confirmation") or {}).get("confirmation_url")
    if not confirmation_url:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "no_confirmation_url")

    async with db_session() as session:
        repo = Repo(session)
        from sqlalchemy import update as _upd
        from .db.models import Payment as _P
        from .db.repo import utcnow as _now
        await session.execute(
            _upd(_P).where(_P.id == local_payment_id).values(
                provider_payment_id=provider_pid, updated_at=_now(),
            )
        )
        await session.commit()

    return {
        "payment_id": local_payment_id,
        "confirmation_url": confirmation_url,
        "amount_rub": amount,
        "seats": seats_for_order,
        "months": months_for_order,
        "kind": kind,
    }


def _remaining_days(org) -> int:
    """Сколько полных дней доступа осталось у школы."""
    from .db.repo import utcnow
    if not org.valid_until:
        return 0
    delta = org.valid_until - utcnow()
    return max(0, delta.days)


async def _resolve_staff_org(repo, user, org_id: Optional[int]):
    """Школа, которой юзер управляет (teacher/admin). 403 иначе.
    Продлевать и докупать места может только сотрудник своей школы."""
    mem = await repo.user_org_membership(user.id)
    if mem is None or mem[1] not in ("teacher", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not_org_staff")
    org, role = mem
    if org_id is not None and int(org_id) != int(org.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "foreign_org")
    return org, role


class _OrgAddonQuoteIn(BaseModel):
    seats: int
    org_id: Optional[int] = None


@router.post("/org/addon-quote")
async def org_addon_quote(
    body: _OrgAddonQuoteIn, authorization: Optional[str] = Header(None),
) -> dict:
    """Расчёт докупки мест до конца оплаченного срока (для кабинета школы)."""
    _require_db()
    from . import org_pricing
    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await auth_lib.resolve_user(repo, authorization=authorization)
        org, _role = await _resolve_staff_org(repo, user, body.org_id)
        remaining = _remaining_days(org)
        try:
            calc = org_pricing.quote_addon(
                body.seats, max(1, remaining),
                int(org.seats_total) + int(body.seats),
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    calc["expired"] = remaining < 1
    return calc


class _OrgInvoiceIn(BaseModel):
    school_name: str
    contact_email: str
    seats: int
    months: int
    inn: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    comment: Optional[str] = None


@router.post("/org/invoice-request")
async def org_invoice_request(
    body: _OrgInvoiceIn, authorization: Optional[str] = Header(None),
) -> dict:
    """Заявка на счёт и договор для юрлица. Оплата картой подходит не всем
    школам — заявка сохраняется и сразу падает владельцу в Telegram.
    Авторизация не требуется: школа может ещё не иметь аккаунта."""
    _require_db()
    from . import org_pricing
    from .auth import send_bot_message
    from .db import Repo

    school_name = (body.school_name or "").strip()
    email = (body.contact_email or "").strip()
    if not school_name or len(school_name) > 128:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_school_name")
    if "@" not in email or len(email) > 255:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_email")
    try:
        calc = org_pricing.quote(body.seats, body.months)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    user_id: Optional[int] = None
    async with db_session() as session:
        repo = Repo(session)
        if authorization:
            try:
                user = await auth_lib.resolve_user(repo, authorization=authorization)
                user_id = int(user.id)
            except Exception:
                user_id = None
        req = await repo.create_invoice_request(
            school_name=school_name, inn=body.inn,
            contact_person=body.contact_person, contact_email=email,
            phone=body.phone, seats=int(calc["seats"]),
            months=int(calc["months"]), amount_rub=int(calc["total_rub"]),
            comment=body.comment, user_id=user_id,
        )
        await session.commit()
        req_id = int(req.id)

    text = (
        "🧾 <b>Заявка на счёт от школы</b>\n\n"
        f"Школа: <b>{school_name}</b>\n"
        f"Пакет: {calc['seats']} мест × {calc['months']} мес — "
        f"{calc['total_rub']} ₽\n"
        f"Контакт: {body.contact_person or '—'} · {email}"
        + (f" · {body.phone}" if body.phone else "") + "\n"
        + (f"ИНН: {body.inn}\n" if body.inn else "")
        + (f"Комментарий: {body.comment}\n" if body.comment else "")
        + f"\nЗаявка #{req_id}"
    )
    for admin_id in settings.admin_ids_list:
        asyncio.create_task(send_bot_message(admin_id, text))

    return {"ok": True, "request_id": req_id, "amount_rub": int(calc["total_rub"])}


@router.get("/org/order-status")
async def org_order_status(
    payment_id: int, authorization: Optional[str] = Header(None),
) -> dict:
    """Статус заказа школы + ссылки-приглашения после успешной оплаты."""
    _require_db()
    from .admin import _org_invite_link, _org_invite_link_web
    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await auth_lib.resolve_user(repo, authorization=authorization)
        payment = await repo.find_payment_by_id(int(payment_id))
        if payment is None or payment.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        order = await repo.get_org_order_by_payment(int(payment_id))
        if order is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        org = await repo.get_org(int(order.org_id)) if order.org_id else None

    out = {
        "payment_status": payment.status,
        "order_status": order.status,
        "school_name": order.school_name,
        "seats": int(order.seats),
        "months": int(order.months),
        "amount_rub": int(order.amount_rub),
    }
    if org is not None:
        out.update({
            "org_id": int(org.id),
            "invite_code": org.invite_code,
            "invite_link": _org_invite_link(org.invite_code),
            "invite_link_web": _org_invite_link_web(org.invite_code),
            "valid_until": org.valid_until.isoformat() if org.valid_until else None,
        })
    return out
