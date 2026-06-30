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
def _plan_catalog() -> dict[str, dict]:
    return {
        "monthly": {"days": 30,  "amount_rub": settings.SUBSCRIPTION_PRICE_MONTHLY_RUB,
                    "title": "Подписка на месяц"},
        "yearly":  {"days": 365, "amount_rub": settings.SUBSCRIPTION_PRICE_YEARLY_RUB,
                    "title": "Подписка на год"},
        "twoyear": {"days": 730, "amount_rub": settings.SUBSCRIPTION_PRICE_TWOYEAR_RUB,
                    "title": "Подписка на 2 года"},
    }


class _CreatePaymentIn(BaseModel):
    plan: str                          # monthly | yearly | twoyear
    email: Optional[str] = None        # для 54-ФЗ чека, если у юзера ещё нет
    promo_code: Optional[str] = None   # промокод для скидки (опционально)


def _require_db() -> None:
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db_not_configured")


def _return_url(payment_id: int) -> str:
    base = (settings.MINIAPP_URL or "").rstrip("/") or "/"
    return f"{base}/?payment_id={payment_id}"


@router.get("/plans")
async def list_plans() -> dict:
    """Список тарифов для отображения на странице подписки. Без auth."""
    return {"plans": [
        {"key": k, "days": v["days"], "amount_rub": v["amount_rub"], "title": v["title"]}
        for k, v in _plan_catalog().items()
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
            await repo.credit_subscription_for_payment(int(payment.id))
            await session.commit()
            logger.info(
                "[yookassa/webhook] credited user_id=%s plan=%s days=%s amount=%s",
                payment.user_id, payment.plan, payment.days_granted, payment.amount_rub,
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
