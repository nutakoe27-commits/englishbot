"""Внутренний HTTP-сервер бота.

Слушает только внутри docker-сети (порт 8080). Используется backend'ом
для уведомления бота о событиях, требующих действия в Telegram.

После сноса Battle/Quest активных endpoint'ов нет — оставляем каркас,
чтобы compose-depends на `bot:8080` не отвалился, и чтобы новые
интеграции (если появятся) добавлялись в одно место.

Аутентификация — общий секрет BACKEND_BOT_SECRET (тот же, что бот шлёт
на backend в X-Bot-Secret).
"""

from __future__ import annotations

import logging

from aiohttp import web
from aiogram import Bot

log = logging.getLogger(__name__)


async def _handle_ping(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "bot-internal"})


def build_app(bot: Bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/internal/ping", _handle_ping)
    return app


async def start_internal_server(bot: Bot, port: int = 8080) -> web.AppRunner:
    app = build_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("[internal_http] listening on 0.0.0.0:%d", port)
    return runner
