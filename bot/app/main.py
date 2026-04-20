import asyncio
import logging
import os
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from dotenv import load_dotenv

from .reminders import (
    get_user_reminder,
    is_db_ready,
    reminders_loop,
    set_user_reminder,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
MINIAPP_URL: str = os.getenv("MINIAPP_URL", "https://englishbot.krichigindocs.ru")

# Цены синхронизированы с settings_kv (DB) и LockScreen в mini app.
# Источник истины сейчас — эти константы (бот не ходит в DB).
PRICE_MONTHLY_RUB = int(os.getenv("SUBSCRIPTION_PRICE_MONTHLY_RUB", "699"))
PRICE_YEARLY_RUB = int(os.getenv("SUBSCRIPTION_PRICE_YEARLY_RUB", "4990"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def _miniapp_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎤 Начать разговор",
                    web_app=WebAppInfo(url=MINIAPP_URL),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📖 Как заниматься (инструкция)",
                    callback_data="show_guide",
                )
            ],
        ]
    )


# ─── /start ──────────────────────────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject) -> None:
    # Deep-link из mini app: «/start subscribe» — сразу показываем экран подписки
    payload = (command.args or "").strip().lower()
    if payload == "subscribe":
        await message.answer(
            text=SUBSCRIBE_TEXT,
            parse_mode="HTML",
            reply_markup=_subscribe_keyboard(),
        )
        return

    user_name = message.from_user.first_name if message.from_user else "друг"

    await message.answer(
        text=(
            f"Привет, {user_name}! 👋\n\n"
            "Я — твой AI-репетитор английского. С тобой можно говорить голосом "
            "или переписываться текстом — как с живым носителем языка.\n\n"
            "<b>Прежде чем начать — прочитай короткую инструкцию /guide.</b> "
            "Это всего пара минут, но сильно изменит то, как ты будешь учиться. "
            "Без неё многие пытаются «переводить» вместо того, чтобы "
            "<i>учиться думать на английском</i>, и быстро бросают.\n\n"
            "Когда будешь готов — жми «🎤 Начать разговор»."
        ),
        parse_mode="HTML",
        reply_markup=_miniapp_keyboard(),
    )


# ─── /guide ──────────────────────────────────────────────────────────────────
GUIDE_TEXT = (
    "📖 <b>Как заниматься английским с этим ботом</b>\n\n"
    "Главный принцип: <b>я — носитель английского, который не знает русского</b>. "
    "Я не переведу тебе слово и не объясню грамматику по-русски — но я объясню "
    "любое непонятное слово простым английским, нарисую картинку словами, дам "
    "пример в контексте. Так учатся реально думать на языке, а не «переводить в "
    "голове».\n\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "🎯 <b>4 правила, которые делают всю разницу</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n\n"
    "<b>1. Не лезь в переводчик.</b>\n"
    "Услышал/увидел незнакомое слово — не переводи. Спроси у меня прямо в чате:\n"
    "<i>— What does «overwhelmed» mean?</i>\n"
    "<i>— Can you explain it with simple words?</i>\n"
    "<i>— Give me an example, please.</i>\n"
    "Я объясню так, чтобы стало понятно из контекста — именно так слово "
    "запоминается надолго.\n\n"
    "<b>2. Говори, даже если с ошибками.</b>\n"
    "Не молчи в поисках идеальной фразы. Скажи как можешь — я пойму и мягко "
    "переформулирую правильно. Ошибки — это не провал, это материал для роста.\n\n"
    "<b>3. Если совсем ступор — переходи на русский в одной фразе.</b>\n"
    "Можно написать или сказать по-русски, что хотел выразить. Я отвечу на "
    "английском и помогу собрать фразу. Но не злоупотребляй — цель в том, "
    "чтобы со временем русский тебе вообще не понадобился.\n\n"
    "<b>4. Регулярность важнее длительности.</b>\n"
    "10 минут каждый день дадут больше, чем час раз в неделю. Я пришлю "
    "напоминание вечером — не игнорируй его.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "🎙 <b>Два режима общения</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n\n"
    "<b>🎤 Голосовой</b> — основной режим. Удерживай кнопку, говори фразу, "
    "отпусти — я отвечу голосом. Это тренирует и понимание на слух, "
    "и произношение.\n\n"
    "<b>💬 Текстовый</b> — включается в настройках mini app (шестерёнка → "
    "Mode → chat). Полезен в шумном месте, в транспорте, или если хочешь "
    "разобрать тему медленно и вдумчиво.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "⚙️ <b>Настройки под тебя</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n\n"
    "В mini app нажми шестерёнку и подгони бота под себя:\n"
    "• <b>Уровень</b> (A1–C1) — насколько простым языком я буду говорить\n"
    "• <b>Роль</b> — кем я для тебя: дружелюбным учителем, собеседником в "
    "кафе, бизнес-партнёром\n"
    "• <b>Длина ответов</b> — короткие реплики или развёрнутые\n"
    "• <b>Исправления</b> — указывать ли на ошибки сразу или дать сначала "
    "выговориться\n\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "💡 <b>Идеи, с чего начать прямо сейчас</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n\n"
    "• <i>«Let's just chat. Ask me about my day.»</i>\n"
    "• <i>«I want to practice ordering food at a restaurant.»</i>\n"
    "• <i>«Teach me 5 useful phrases for a job interview.»</i>\n"
    "• <i>«Pretend you're a tourist in Moscow and I'm helping you.»</i>\n"
    "• <i>«Let's discuss my favorite movie. I'll tell you about it.»</i>\n\n"
    "Готов? Жми «🎤 Начать разговор» из /start. Удачи! 🚀"
)


@dp.message(Command("guide"))
async def cmd_guide(message: Message) -> None:
    await message.answer(
        text=GUIDE_TEXT,
        parse_mode="HTML",
        reply_markup=_miniapp_keyboard(),
    )


@dp.callback_query(lambda c: c.data == "show_guide")
async def cb_show_guide(callback) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            text=GUIDE_TEXT,
            parse_mode="HTML",
            reply_markup=_miniapp_keyboard(),
        )


# ─── /help ───────────────────────────────────────────────────────────────────
@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        text=(
            "ℹ️ <b>Доступные команды</b>\n\n"
            "/start — главное меню и кнопка запуска разговора\n"
            "/guide — <b>как правильно заниматься</b> (прочитай обязательно)\n"
            "/profile — твой прогресс и статистика\n"
            "/subscribe — информация о подписке\n"
            "/reminder — настройка ежедневного напоминания\n"
            "/help — эта справка\n\n"
            "Чтобы начать практику — нажми «🎤 Начать разговор» в /start."
        ),
        parse_mode="HTML",
    )


# ─── /profile ────────────────────────────────────────────────────────────────
@dp.message(Command("profile"))
async def cmd_profile(message: Message) -> None:
    await message.answer(
        text=(
            "👤 <b>Твой профиль</b>\n\n"
            "⏳ Скоро: твой прогресс, уровень английского "
            "и статистика занятий появятся здесь."
        ),
        parse_mode="HTML",
    )


# ─── /subscribe ──────────────────────────────────────────────────────────────
SUBSCRIBE_TEXT = (
    "⭐ <b>Подписка English Tutor</b>\n\n"
    "На бесплатном тарифе — <b>10 минут в день</b> практики. "
    "Лимит сбрасывается в полночь по МСК.\n\n"
    "С подпиской — <b>без лимитов</b> и круглые сутки:\n"
    f"• <b>{PRICE_MONTHLY_RUB} ₽ / месяц</b>\n"
    f"• <b>{PRICE_YEARLY_RUB} ₽ / год</b> (выгоднее на ~40%)\n\n"
    "⏳ <i>Оплата будет доступна ближайшими днями — мы сейчас подключаем платёжную "
    "систему. Нажми кнопку ниже — мы сообщим, как только всё будет готово.</i>"
)


def _subscribe_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💳 Оплатить месяц — {PRICE_MONTHLY_RUB} ₽",
                    callback_data="subscribe:monthly",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"💳 Оплатить год — {PRICE_YEARLY_RUB} ₽",
                    callback_data="subscribe:yearly",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎙 Начать разговор",
                    web_app=WebAppInfo(url=MINIAPP_URL),
                )
            ],
        ]
    )


@dp.message(Command("subscribe"))
async def cmd_subscribe(message: Message) -> None:
    await message.answer(
        text=SUBSCRIBE_TEXT,
        parse_mode="HTML",
        reply_markup=_subscribe_keyboard(),
    )


# ─── /reminder ───────────────────────────────────────────────────────────────
REMINDER_HOURS = (8, 12, 15, 19, 21)


def _reminder_settings_keyboard(current_hour: int, enabled: bool) -> InlineKeyboardMarkup:
    """Клавиатура выбора часа напоминания + кнопка отключения."""
    def _hour_btn(h: int) -> InlineKeyboardButton:
        mark = "✅ " if (enabled and h == current_hour) else ""
        return InlineKeyboardButton(
            text=f"{mark}{h:02d}:00",
            callback_data=f"reminder:hour:{h}",
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_hour_btn(h) for h in REMINDER_HOURS[:3]],
            [_hour_btn(h) for h in REMINDER_HOURS[3:]],
            [
                InlineKeyboardButton(
                    text=("🔕 Отключить напоминания" if enabled else "🔔 Уже отключены"),
                    callback_data="reminder:off",
                )
            ],
        ]
    )


def _format_reminder_status(enabled: bool, hour_msk: int) -> str:
    if enabled:
        return (
            "🔔 <b>Напоминания включены</b>\n\n"
            f"Сейчас: каждый день в <b>{hour_msk:02d}:00 МСК</b>.\n\n"
            "Регулярность важнее длительности — 10 минут каждый день "
            "дадут больше, чем час раз в неделю. Можно поменять время "
            "или выключить ниже."
        )
    return (
        "🔕 <b>Напоминания выключены</b>\n\n"
        "Выбери удобное время — я буду присылать короткий пинок "
        "в этот чат раз в день."
    )


DB_TIMEOUT_S = 5.0


async def _safe_get_reminder(tg_id: int) -> Optional[tuple[bool, int]]:
    try:
        return await asyncio.wait_for(get_user_reminder(tg_id), timeout=DB_TIMEOUT_S)
    except Exception as exc:
        logger.warning("reminder: get_user_reminder упал: %s", exc)
        return None


async def _safe_set_reminder(
    tg_id: int,
    *,
    enabled: Optional[bool] = None,
    hour_msk: Optional[int] = None,
) -> Optional[bool]:
    """True/False — результат set_user_reminder. None — ошибка/таймаут БД."""
    try:
        return await asyncio.wait_for(
            set_user_reminder(tg_id, enabled=enabled, hour_msk=hour_msk),
            timeout=DB_TIMEOUT_S,
        )
    except Exception as exc:
        logger.warning("reminder: set_user_reminder упал: %s", exc)
        return None


@dp.message(Command("reminder"))
async def cmd_reminder(message: Message) -> None:
    logger.info(
        "/reminder from tg_id=%s",
        message.from_user.id if message.from_user else "?",
    )
    if not message.from_user:
        return
    if not is_db_ready():
        await message.answer(
            "⚠️ Напоминания временно недоступны — подключаем БД. "
            "Попробуй через пару минут."
        )
        return
    tg_id = message.from_user.id
    row = await _safe_get_reminder(tg_id)
    if row is None:
        # Ни юзера в БД, ни ошибки в интерфейсе различить не можем —
        # покажем общий полезный экран.
        await message.answer(
            "Чтобы настроить напоминания, сначала открой приложение через "
            "«🎤 Начать разговор» в /start — я запомню тебя.",
            reply_markup=_miniapp_keyboard(),
        )
        return
    enabled, hour_msk = row
    await message.answer(
        text=_format_reminder_status(enabled, hour_msk),
        parse_mode="HTML",
        reply_markup=_reminder_settings_keyboard(hour_msk, enabled),
    )


async def _show_reminder_panel(callback: CallbackQuery, tg_id: int) -> None:
    """Показать (или перерисовать) экран настроек напоминания."""
    row = await _safe_get_reminder(tg_id)
    enabled, hour_msk = row if row else (False, 19)
    text = _format_reminder_status(enabled, hour_msk)
    kb = _reminder_settings_keyboard(hour_msk, enabled)
    if callback.message:
        try:
            await callback.message.edit_text(text=text, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            # Сообщение могло быть нередактируемым (старое или с web_app-кнопкой) —
            # пришлём новым.
            try:
                await callback.message.answer(text=text, parse_mode="HTML", reply_markup=kb)
            except Exception as exc:
                logger.warning("reminder: не удалось показать панель: %s", exc)


@dp.callback_query(lambda c: c.data and c.data.startswith("reminder:"))
async def cb_reminder(callback: CallbackQuery) -> None:
    logger.info(
        "reminder callback: data=%r from tg_id=%s",
        callback.data,
        callback.from_user.id if callback.from_user else "?",
    )
    if not callback.from_user or not callback.data:
        await callback.answer()
        return
    if not is_db_ready():
        await callback.answer(
            "Напоминания временно недоступны", show_alert=True
        )
        return

    tg_id = callback.from_user.id
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    # «reminder:settings» — кнопка из push-уведомления. Показываем панель.
    if action == "settings":
        await callback.answer()
        await _show_reminder_panel(callback, tg_id)
        return

    if action == "off":
        result = await _safe_set_reminder(tg_id, enabled=False)
        if result is None:
            await callback.answer(
                "Ошибка БД. Попробуй чуть позже.", show_alert=True
            )
            return
        if result is False:
            await callback.answer(
                "Сначала открой mini app — я тебя ещё не знаю.", show_alert=True
            )
            return
        await callback.answer("Напоминания выключены")
        await _show_reminder_panel(callback, tg_id)
        return

    if action == "hour" and len(parts) == 3:
        try:
            hour = int(parts[2])
        except ValueError:
            await callback.answer()
            return
        if hour < 0 or hour > 23:
            await callback.answer()
            return
        result = await _safe_set_reminder(tg_id, enabled=True, hour_msk=hour)
        if result is None:
            await callback.answer(
                "Ошибка БД. Попробуй чуть позже.", show_alert=True
            )
            return
        if result is False:
            await callback.answer(
                "Сначала открой mini app — я тебя ещё не знаю.", show_alert=True
            )
            return
        await callback.answer(f"Ок, буду напоминать в {hour:02d}:00 МСК")
        await _show_reminder_panel(callback, tg_id)
        return

    await callback.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("subscribe:"))
async def cb_subscribe_stub(callback: CallbackQuery) -> None:
    """Заглушка: оплата ещё не подключена. Просто сообщаем юзеру."""
    await callback.answer(
        "Оплата скоро будет доступна — мы сообщим в боте.",
        show_alert=True,
    )
    if callback.message:
        plan = (callback.data or "").split(":", 1)[-1]
        plan_label = "месяц" if plan == "monthly" else "год"
        price = PRICE_MONTHLY_RUB if plan == "monthly" else PRICE_YEARLY_RUB
        await callback.message.answer(
            text=(
                f"Ты выбрал тариф «<b>{plan_label}</b>» — <b>{price} ₽</b>.\n\n"
                "Платёжная система подключается — пришлём ссылку на оплату в этот чат, "
                "как только всё будет готово."
            ),
            parse_mode="HTML",
        )


async def _set_bot_commands() -> None:
    """Задать список команд, который виден в меню Telegram."""
    await bot.set_my_commands(
        commands=[
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="guide", description="Как заниматься (инструкция)"),
            BotCommand(command="profile", description="Мой профиль"),
            BotCommand(command="subscribe", description="Подписка"),
            BotCommand(command="reminder", description="Напоминания"),
            BotCommand(command="help", description="Справка по командам"),
        ]
    )


# ─── Точка входа ─────────────────────────────────────────────────────────────
async def main() -> None:
    logger.info("Starting bot in long polling mode...")
    await _set_bot_commands()
    # Фоновая задача: ежедневные напоминания.
    # Если БД не настроена, reminders_loop сам тихо завершится.
    if is_db_ready():
        logger.info("Starting reminders loop (DB ready)")
        asyncio.create_task(reminders_loop(bot, MINIAPP_URL))
    else:
        logger.warning(
            "Reminders loop NOT started — DATABASE_URL not set or DB not reachable"
        )
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
