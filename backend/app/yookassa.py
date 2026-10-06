"""
yookassa.py — клиент API ЮKassa v3 для веб-оплаты подписки (PR-8).

Используется только бэкендом (через `/api/payments/*`). Telegram-бот идёт
другим путём — через Telegram Payments + provider_token (bot/app/main.py).

Документация: https://yookassa.ru/developers/api
"""

from __future__ import annotations

import logging
import secrets
from typing import Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.yookassa.ru/v3"


def _auth_ok() -> bool:
    return bool(settings.YOOKASSA_SHOP_ID and settings.YOOKASSA_SECRET_KEY)


def _basic_auth() -> tuple[str, str]:
    return (settings.YOOKASSA_SHOP_ID or "", settings.YOOKASSA_SECRET_KEY or "")


def _build_receipt(amount_rub: int, description: str, email: str) -> dict:
    """54-ФЗ чек. Один item на сумму платежа, эл. почта получателя."""
    return {
        "customer": {"email": email},
        "items": [{
            "description": description[:128],
            "quantity": "1.00",
            "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
            "vat_code": settings.YOOKASSA_VAT_CODE,
            "payment_subject": "service",
            "payment_mode": "full_payment",
        }],
    }


async def create_payment(
    *,
    amount_rub: int,
    description: str,
    return_url: str,
    metadata: dict,
    customer_email: Optional[str],
    save_payment_method: bool = False,
) -> Optional[dict]:
    """Создать платёж в ЮKassa. Возвращает payload или None при ошибке.

    Ключевые поля ответа:
      - id          — provider_payment_id (UUID).
      - status      — 'pending' сразу после создания.
      - confirmation.confirmation_url — куда редиректить юзера.

    save_payment_method=True — попросить ЮKassa сохранить способ оплаты для
    автопродления. Если магазину автоплатежи не подключены, ЮKassa отвечает
    4xx — тогда повторяем как обычный платёж, чтобы человек всё равно смог
    оплатить, и помечаем ответ флагом _recurring_unavailable.
    """
    if not _auth_ok():
        logger.warning("[yookassa] not configured: SHOP_ID/SECRET_KEY missing")
        return None

    payload: dict = {
        "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
        "capture": True,
        "description": description,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "metadata": metadata,
    }
    if save_payment_method:
        payload["save_payment_method"] = True
    if settings.YOOKASSA_FISCALIZATION:
        if not customer_email:
            logger.warning("[yookassa] fiscalization on but no email — skipping receipt")
        else:
            payload["receipt"] = _build_receipt(amount_rub, description, customer_email)

    headers = {
        "Idempotence-Key": secrets.token_urlsafe(24),
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{API_BASE}/payments",
                json=payload,
                headers=headers,
                auth=_basic_auth(),
            )
        if resp.status_code not in (200, 201):
            logger.warning(
                "[yookassa] create_payment failed %s: %s",
                resp.status_code, resp.text[:400],
            )
            if save_payment_method and 400 <= resp.status_code < 500:
                logger.warning("[yookassa] автоплатежи недоступны — платим без сохранения карты")
                retry = await create_payment(
                    amount_rub=amount_rub, description=description,
                    return_url=return_url, metadata={**metadata, "recurring_setup": "0"},
                    customer_email=customer_email, save_payment_method=False,
                )
                if retry is not None:
                    retry["_recurring_unavailable"] = True
                return retry
            return None
        return resp.json()
    except Exception as exc:
        logger.warning("[yookassa] create_payment exception: %r", exc)
        return None


async def create_recurring_payment(
    *,
    payment_method_id: str,
    amount_rub: int,
    description: str,
    metadata: dict,
    customer_email: Optional[str],
    idempotence_key: str,
) -> tuple[Optional[dict], Optional[int]]:
    """Автосписание по сохранённому способу оплаты — без участия человека.

    Возвращает (payload, http_status). payload=None и http_status=None —
    сеть/таймаут: результат неизвестен, повторять С ТЕМ ЖЕ idempotence_key
    (ЮKassa вернёт тот же платёж, а не спишет второй раз).
    """
    if not _auth_ok():
        return None, None
    payload: dict = {
        "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
        "capture": True,
        "payment_method_id": payment_method_id,
        "description": description,
        "metadata": metadata,
    }
    if settings.YOOKASSA_FISCALIZATION and customer_email:
        payload["receipt"] = _build_receipt(amount_rub, description, customer_email)
    headers = {"Idempotence-Key": idempotence_key, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{API_BASE}/payments", json=payload, headers=headers, auth=_basic_auth(),
            )
    except Exception as exc:
        logger.warning("[yookassa] recurring payment exception: %r", exc)
        return None, None
    if resp.status_code not in (200, 201):
        logger.warning("[yookassa] recurring payment failed %s: %s",
                       resp.status_code, resp.text[:400])
        try:
            return resp.json(), resp.status_code
        except Exception:
            return {}, resp.status_code
    return resp.json(), resp.status_code


def saved_method(payment: dict) -> Optional[tuple[str, str]]:
    """(payment_method_id, title) из подтверждённого платежа, если ЮKassa
    сохранила способ оплаты для автоплатежей; иначе None."""
    pm = (payment or {}).get("payment_method") or {}
    if not pm.get("saved") or not pm.get("id"):
        return None
    title = str(pm.get("title") or "")
    card = pm.get("card") or {}
    if not title and card.get("last4"):
        title = f"{card.get('card_type') or 'Карта'} *{card['last4']}"
    return str(pm["id"]), (title or "Сохранённый способ оплаты")[:64]


async def fetch_payment(provider_payment_id: str) -> Optional[dict]:
    """GET /v3/payments/<id> — текущее состояние платежа на стороне ЮKassa.

    Используем в webhook-обработчике: не доверяем телу нотификации, берём
    статус из api.yookassa.ru напрямую (защита от подделанных webhook'ов).
    """
    if not _auth_ok() or not provider_payment_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{API_BASE}/payments/{provider_payment_id}",
                auth=_basic_auth(),
            )
        if resp.status_code != 200:
            logger.warning(
                "[yookassa] fetch_payment %s failed %s: %s",
                provider_payment_id, resp.status_code, resp.text[:300],
            )
            return None
        return resp.json()
    except Exception as exc:
        logger.warning("[yookassa] fetch_payment exception: %r", exc)
        return None
