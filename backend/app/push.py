"""Веб-пуши (Web Push API) — канал до человека помимо Telegram.

Зачем вообще. Telegram в России доступен рывками, и бот вместе с ним. Web
push работает и в обычном мобильном Chrome, и внутри установленного из
магазина приложения (TWA) — одна и та же подписка обслуживает обе
площадки, и один и тот же код на клиенте.

Как это устроено. Браузер выдаёт подписку: адрес своего пуш-сервиса
(endpoint) и два ключа. Мы шифруем этими ключами тело уведомления и шлём
на endpoint, подписав запрос парой VAPID-ключей — так сервис знает, что
отправитель тот же, что и при подписке. Содержимое промежуточный сервис
не видит: расшифровать может только браузер.

Ограничения, о которых надо помнить:

* доставка требует сервисов Google на устройстве (у Chrome пуш-сервис —
  инфраструктура Firebase). На телефонах без них уведомления не придут;
* iOS доставляет push только приложению, добавленному на домашний экран,
  и только с iOS 16.4+;
* отозванную подписку сервис помечает 404 или 410 — такие строки надо
  удалять сразу, иначе таблица за год превратится в кладбище.

Отправка синхронная (pywebpush ходит через requests), поэтому вызывается
через to_thread: блокировать event loop рассылкой на тысячу подписок
нельзя.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from .config import settings

logger = logging.getLogger(__name__)

# Столько подписок обрабатываем одновременно. Пуш-сервисы не любят
# всплесков, а нам спешить некуда.
_CONCURRENCY = 16
# Таймаут одной отправки, секунды.
_TIMEOUT = 10


def is_configured() -> bool:
    """Настроены ли ключи. Без них весь механизм молча выключен."""
    return bool(settings.VAPID_PRIVATE_KEY and settings.VAPID_PUBLIC_KEY)


def public_key() -> str:
    return settings.VAPID_PUBLIC_KEY or ""


def _claims() -> dict:
    # subject обязателен по спецификации: способ связаться с отправителем,
    # если он начнёт злоупотреблять. mailto: или https:.
    sub = (settings.VAPID_SUBJECT or "").strip()
    if not sub:
        sub = "mailto:admin@localhost"
    return {"sub": sub}


def _send_one_sync(sub: dict, payload: str) -> tuple[bool, bool, str]:
    """Отправить одно уведомление. → (ok, надо_удалить, пояснение).

    Вторым флагом отмечаем именно отозванную подписку: её удаляем сразу,
    а сетевую ошибку просто считаем.
    """
    from pywebpush import WebPushException, webpush

    info = {
        "endpoint": sub["endpoint"],
        "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
    }
    try:
        webpush(
            subscription_info=info,
            data=payload,
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims=dict(_claims()),
            timeout=_TIMEOUT,
        )
        return True, False, "ok"
    except WebPushException as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        # 404 — endpoint не существует, 410 — подписка отозвана.
        # 403/401 — не сошлись VAPID-ключи: удалять подписку нельзя, это
        # наша проблема с конфигурацией, а не мёртвый браузер.
        gone = code in (404, 410)
        return False, gone, f"{code}: {exc}"
    except Exception as exc:                     # сеть, DNS, таймаут
        return False, False, repr(exc)


def build_payload(
    *, title: str, body: str, url: str = "/", tag: Optional[str] = None,
) -> str:
    """Тело уведомления. Разбирается в sw.js — формат менять только вместе."""
    data = {"title": title, "body": body, "url": url}
    if tag:
        data["tag"] = tag
    return json.dumps(data, ensure_ascii=False)


async def send_to_subscriptions(subs: list[dict], payload: str) -> dict:
    """Разослать по списку подписок.

    Возвращает сводку и списки id: доставленные, отозванные (их удаляем) и
    просто неудачные. Саму БД тут не трогаем — вызывающий решает, что
    записать, чтобы не тащить сессию через to_thread.
    """
    if not is_configured():
        return {"sent": 0, "failed": 0, "gone": 0, "ok_ids": [], "gone_ids": [], "fail_ids": []}

    sem = asyncio.Semaphore(_CONCURRENCY)
    ok_ids: list[int] = []
    gone_ids: list[int] = []
    fail_ids: list[int] = []

    async def one(sub: dict) -> None:
        async with sem:
            ok, gone, note = await asyncio.to_thread(_send_one_sync, sub, payload)
        if ok:
            ok_ids.append(sub["id"])
        elif gone:
            gone_ids.append(sub["id"])
        else:
            fail_ids.append(sub["id"])
            logger.warning("[push] не доставлено id=%s: %s", sub["id"], note)

    await asyncio.gather(*(one(s) for s in subs))
    return {
        "sent": len(ok_ids),
        "gone": len(gone_ids),
        "failed": len(fail_ids),
        "ok_ids": ok_ids,
        "gone_ids": gone_ids,
        "fail_ids": fail_ids,
    }
