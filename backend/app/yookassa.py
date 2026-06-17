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
) -> Optional[dict]:
    """Создать платёж в ЮKassa. Возвращает payload или None при ошибке.

    Ключевые поля ответа:
      - id          — provider_payment_id (UUID).
      - status      — 'pending' сразу после создания.
      - confirmation.confirmation_url — куда редиректить юзера.
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
            return None
        return resp.json()
    except Exception as exc:
        logger.warning("[yookassa] create_payment exception: %s", exc)
        return None


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
        logger.warning("[yookassa] fetch_payment exception: %s", exc)
        return None
