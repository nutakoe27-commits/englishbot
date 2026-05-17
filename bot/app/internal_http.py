"""Внутренний HTTP-сервер бота.

Нужен для того, чтобы backend мог postнуть боту уведомление и бот совершил
действие в Telegram. Вне Docker-сети не выставлен — слушает только внутри.

Эндпоинты:
  POST /internal/battle-judged — backend говорит: «battle #X отсужен,
    опубликуй результат в исходный чат/inline-сообщение».
  POST /internal/quest-completed — backend говорит: «юзеру выдан бонус
    +30 мин за квест, скажи ему об этом в ЛС».

Аутентификация — общий секрет BACKEND_BOT_SECRET (тот же, что бот шлёт
на backend в X-Bot-Secret).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from aiohttp import web
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import backend_client


def _revanche_keyboard(battle_id: int) -> InlineKeyboardMarkup:
    """Кнопка «🔄 Реванш» под результатом battle. callback_data парсится
    в bot/app/main.py::cb_battle_revanche."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="🔄 Реванш",
                callback_data=f"battle:revanche:{battle_id}",
            ),
        ]]
    )


log = logging.getLogger(__name__)


def _check_secret(request: web.Request) -> bool:
    expected = os.getenv("BACKEND_BOT_SECRET", "").strip()
    if not expected:
        log.warning("[internal_http] BACKEND_BOT_SECRET not set — rejecting")
        return False
    got = request.headers.get("X-Bot-Secret", "")
    return bool(got) and got == expected


async def _handle_battle_judged(request: web.Request) -> web.Response:
    if not _check_secret(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)

    battle_id = data.get("battle_id")
    if not isinstance(battle_id, int):
        return web.json_response({"error": "battle_id required"}, status=400)

    bot: Bot = request.app["bot"]
    state = await backend_client.battle_state(battle_id)
    if state is None:
        return web.json_response({"error": "battle not found"}, status=404)
    if state.get("status") != "judged":
        return web.json_response({"error": "battle not judged yet"}, status=409)

    # Сначала пробуем имя из backend-state (там username из базы, собранный при
    # первом /start), иначе дотягиваем через bot.get_chat.
    a_name = state.get("initiator_name") or await _name_of(bot, state.get("initiator_tg_id"))
    b_name = state.get("opponent_name") or await _name_of(bot, state.get("opponent_tg_id"))

    # Рендерим сообщение. Скоры приходят как dict, считаем total сами.
    text = _render_judge_message(state, a_name=a_name, b_name=b_name)

    # Достаём inline_message_id / chat_id из battle напрямую из backend.
    # battle_state сейчас не отдаёт эти поля — добавим.
    # Пока используем raw-запрос, если надо.
    inline_message_id = state.get("inline_message_id")
    chat_id = state.get("chat_id")
    chat_message_id = state.get("chat_message_id")

    revanche_kb = _revanche_keyboard(battle_id)

    edited = False
    try:
        if inline_message_id:
            await bot.edit_message_text(
                inline_message_id=inline_message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=revanche_kb,
            )
            edited = True
        elif chat_id and chat_message_id:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=chat_message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=revanche_kb,
            )
            edited = True
    except Exception as exc:
        log.warning("[internal_http] edit_message_text failed: %s", exc)

    # Дублируем в ЛС обоим участникам — им важно знать результат,
    # даже если исходный inline-пост недоступен. С кнопкой реванша.
    for tg_id in (state.get("initiator_tg_id"), state.get("opponent_tg_id")):
        if not tg_id:
            continue
        try:
            await bot.send_message(
                tg_id, text, parse_mode="HTML", reply_markup=revanche_kb,
            )
        except Exception as exc:
            log.info("[internal_http] DM result to %s failed: %s", tg_id, exc)

    return web.json_response({"ok": True, "edited_original": edited})


async def _handle_quest_completed(request: web.Request) -> web.Response:
    if not _check_secret(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)

    tg_id = data.get("tg_id")
    title_ru = data.get("title_ru", "")
    reward_seconds = int(data.get("reward_seconds", 0))
    if not isinstance(tg_id, int):
        return web.json_response({"error": "tg_id required"}, status=400)

    bot: Bot = request.app["bot"]
    minutes = reward_seconds // 60
    text = (
        f"✅ <b>Квест выполнен: {title_ru}</b>\n\n"
        f"Получено: +{minutes} мин к дневному лимиту. "
        f"Отлично сработал — возвращайся завтра за новым."
    )
    try:
        await bot.send_message(tg_id, text, parse_mode="HTML")
    except Exception as exc:
        log.warning("[internal_http] quest DM failed for %s: %s", tg_id, exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=200)
    return web.json_response({"ok": True})


async def _name_of(bot: Bot, tg_id: Any) -> str:
    """Пытаемся достать красивое имя через bot.get_chat.

    Fallback — 'Player <tg_id>'.
    """
    if not tg_id:
        return "Player"
    try:
        chat = await bot.get_chat(tg_id)
        name = (chat.first_name or "") + (f" {chat.last_name}" if chat.last_name else "")
        name = name.strip()
        if not name and chat.username:
            name = f"@{chat.username}"
        return name or f"Player {tg_id}"
    except Exception:
        return f"Player {tg_id}"


def _render_judge_message(state: dict, *, a_name: str, b_name: str) -> str:
    """Локальная копия render_judge_message из backend/app/battle.py.

    Дублирование — потому что bot/ не импортирует backend/ (см. архитектуру).
    """
    a_score = state.get("a_score") or {}
    b_score = state.get("b_score") or {}
    a_total = sum(int(v) for v in a_score.values()) if a_score else 0
    b_total = sum(int(v) for v in b_score.values()) if b_score else 0
    winner = state.get("winner") or "tie"

    def _fmt(s: dict) -> str:
        if not s:
            return "—"
        return (
            f"gram {s.get('grammar','?')}/10 · "
            f"flu {s.get('fluency','?')}/10 · "
            f"arg {s.get('argumentation','?')}/10"
        )

    if winner == "a":
        headline = f"🏆 Победил {a_name}"
    elif winner == "b":
        headline = f"🏆 Победил {b_name}"
    else:
        headline = "🤝 Ничья"

    battle_id = state.get("id", "?")
    topic = state.get("topic_title_ru", "")
    comment = state.get("judge_comment") or ""

    return (
        f"<b>⚔️ Battle #{battle_id} — {topic}</b>\n"
        f"{headline}\n\n"
        f"<b>{a_name}</b> — {a_total}/30\n"
        f"<i>{_fmt(a_score)}</i>\n\n"
        f"<b>{b_name}</b> — {b_total}/30\n"
        f"<i>{_fmt(b_score)}</i>\n\n"
        f"💬 <i>{comment}</i>"
    )


def build_app(bot: Bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_post("/internal/battle-judged", _handle_battle_judged)
    app.router.add_post("/internal/quest-completed", _handle_quest_completed)
    return app


async def start_internal_server(bot: Bot, port: int = 8080) -> web.AppRunner:
    app = build_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("[internal_http] listening on 0.0.0.0:%d", port)
    return runner
