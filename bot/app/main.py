import asyncio
import logging
import os
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    ChosenInlineResult,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    Update,
    WebAppInfo,
)
from dotenv import load_dotenv

from . import backend_client
from .internal_http import start_internal_server

import json

from .reminders import (
    credit_subscription_payment,
    get_maintenance_status,
    get_user_profile,
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

# Дневной лимит для free-тарифа (секунды). Источник истины — settings_kv
# в backend (ключ free_seconds_per_day), здесь держим фолбэк-значение для
# отображения в профиле.
FREE_DAILY_SECONDS = int(os.getenv("FREE_DAILY_SECONDS", "600"))

# ADMIN_IDS (через запятую) — эти tg_id минуют maintenance-гейт.
_ADMIN_IDS: set[int] = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

# ЮКасса: provider_token выдаёт @BotFather при привязке магазина ЮКассы.
# ShopID используем только для аудита/логов (в нативной интеграции запросы идут через Telegram, не напрямую в ЮКассу).
YOOKASSA_PROVIDER_TOKEN: str = os.getenv("YOOKASSA_PROVIDER_TOKEN", "").strip()
YOOKASSA_SHOP_ID: str = os.getenv("YOOKASSA_SHOP_ID", "").strip()
# Фискализация включена: передаём receipt + требуем email. Выключить:
# YOOKASSA_FISCALIZATION=0 в .env.
YOOKASSA_FISCALIZATION: bool = os.getenv("YOOKASSA_FISCALIZATION", "1") == "1"
# VAT code для receipt: 1=без НДС, 2=0%, 3=10%, 4=20%. Под УСН/ИП — 1.
YOOKASSA_VAT_CODE: int = int(os.getenv("YOOKASSA_VAT_CODE", "1"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ─── Maintenance middleware ───────────────────────────────────────────
# Если settings_kv.maintenance_mode = '1', бот всем (кроме админов) отвечает
# майнтенанс-сообщением и не пускает апдейт дальше по handler chain.
# Админы (из ADMIN_IDS) могут тестировать бота во время тех.работ.

@dp.update.outer_middleware()
async def _maintenance_middleware(handler, event: Update, data: dict):
    # Извлекаем tg_id и хендл для ответа в зависимости от типа апдейта
    tg_id: Optional[int] = None
    message: Optional[Message] = None
    callback: Optional[CallbackQuery] = None

    if event.message and event.message.from_user:
        tg_id = event.message.from_user.id
        message = event.message
    elif event.callback_query and event.callback_query.from_user:
        tg_id = event.callback_query.from_user.id
        callback = event.callback_query

    # Админы проходят всегда
    if tg_id is not None and tg_id in _ADMIN_IDS:
        return await handler(event, data)

    enabled, message_text = await get_maintenance_status()
    if not enabled:
        return await handler(event, data)

    # Тех.работы активны — отвечаем сообщением, handler chain не зовём
    try:
        if message is not None:
            await message.answer(message_text)
        elif callback is not None:
            # alert=True — модальный попап, чтобы точно увидели
            await callback.answer(message_text, show_alert=True)
    except Exception as exc:
        logger.warning("[maintenance] не удалось ответить: %s", exc)
    logger.info("[maintenance] апдейт от tg_id=%s заблокирован", tg_id)
    return None


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
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "⚔️ <b>Battle Mode — дуэль с другом</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Самый быстрый способ спровоцировать себя говорить на английском.\n\n"
    "1. В любом чате Telegram пишешь <code>@kmo_ai_english_bot battle</code>.\n"
    "2. Кликаешь по карточке «Бросить вызов» — она улетает в чат.\n"
    "3. Друг жмёт «Принять» — обоим в ЛС приходит тема и вопрос.\n"
    "4. Каждый открывает Mini App и записывает <b>60 секунд</b> аргумента.\n"
    "5. ИИ-судья оценивает грамматику, беглость и аргументацию — результат прилетает в чат.\n\n"
    "На принятие вызова у соперника 24 часа. Подписка нужна только инициатору — "
    "оппонент может быть новичком.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "🎯 <b>Квест дня</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Каждый день утром я выдаю тебе одну короткую задачу — например, "
    "«используй 5 phrasal verbs» или «поговори в Past Perfect не меньше 3 раз».\n\n"
    "Просто открываешь Mini App и разговариваешь как обычно. Я вижу по твоему транскрипту, "
    "было ли условие выполнено, и начисляю <b>+30 минут</b> к дневному лимиту. Квесты сбрасываются в полночь МСК.\n\n"
    "Посмотреть актуальный — /quest.\n\n"
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
            "/battle — англо-дуэль с другом (ИИ-судья оценивает ответы)\n"
            "/quest — твой квест дня (+30 мин к дневному лимиту)\n"
            "/help — эта справка\n\n"
            "Чтобы начать практику — нажми «🎤 Начать разговор» в /start. "
            "Для дуэли с другом в любом чате: <code>@kmo_ai_english_bot battle</code>."
        ),
        parse_mode="HTML",
    )


# ─── /quest ──────────────────────────────────────────────────────────────────
@dp.message(Command("quest"))
async def cmd_quest(message: Message) -> None:
    if not message.from_user:
        return
    tg_id = message.from_user.id
    quest = await backend_client.quest_assign(tg_id=tg_id, user_level=None)
    if quest is None:
        await message.answer(
            "Не удалось получить квест. Запусти /start и попробуй ещё раз.",
        )
        return
    reward_min = max(1, quest.reward_seconds // 60)
    await message.answer(
        text=(
            f"🎯 <b>Квест дня: {quest.title_ru}</b>\n\n"
            f"{quest.description_ru}\n\n"
            f"<b>Награда:</b> +{reward_min} мин к дневному лимиту.\n"
            f"Проверяется автоматически по разговору в Mini App."
        ),
        parse_mode="HTML",
        reply_markup=_miniapp_keyboard(),
    )


# ─── /profile ────────────────────────────────────────────────────────────────
def _fmt_minutes(seconds: int) -> str:
    """600 → '10 мин', 45 → '0.8 мин', 3725 → '62 мин'."""
    if seconds <= 0:
        return "0 мин"
    minutes = seconds / 60
    if minutes < 10:
        return f"{minutes:.1f} мин"
    return f"{int(round(minutes))} мин"


def _fmt_total_practice(seconds: int) -> str:
    """Для суммарной статистики: '5 ч 42 мин' или '18 мин'."""
    if seconds < 60:
        return "меньше минуты"
    total_min = seconds // 60
    if total_min < 60:
        return f"{total_min} мин"
    hours = total_min // 60
    mins = total_min % 60
    if mins == 0:
        return f"{hours} ч"
    return f"{hours} ч {mins} мин"


def _fmt_subscription_until(dt) -> str:
    """datetime → '21.05.2026'. None → '—'."""
    if dt is None:
        return "—"
    return dt.strftime("%d.%m.%Y")


def _profile_keyboard(has_sub: bool) -> InlineKeyboardMarkup:
    sub_text = "⭐ Продлить подписку" if has_sub else "⭐ Оформить подписку"
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
                    text=sub_text,
                    callback_data="profile:subscribe",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏰ Настроить напоминание",
                    callback_data="reminder:settings",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📘 Как правильно заниматься",
                    callback_data="show_guide",
                )
            ],
        ]
    )


def _build_profile_text(message: Message, profile: Optional[dict]) -> str:
    """Собирает HTML-текст карточки профиля."""
    from_user = message.from_user
    tg_id = from_user.id if from_user else 0

    first_name = (profile or {}).get("first_name") or (
        from_user.first_name if from_user else None
    ) or "Друг"
    username = (profile or {}).get("username") or (
        from_user.username if from_user else None
    )
    username_line = f"@{username}" if username else "<i>username не задан в Telegram</i>"

    lines = [
        "👤 <b>Твой профиль</b>",
        "",
        f"<b>{first_name}</b>",
        username_line,
        f"<code>ID: {tg_id}</code>",
        "",
    ]

    if profile is None:
        lines += [
            "⏳ Подробная статистика появится после первого разговора.",
            "Нажми «🎤 Начать разговор» и запусти мини-апп.",
        ]
        return "\n".join(lines)

    # Подписка
    lines.append("<b>⭐ Подписка</b>")
    if profile["has_subscription"]:
        until = _fmt_subscription_until(profile["subscription_until"])
        days = profile["days_left"]
        lines.append(f"Premium — активна до <b>{until}</b>")
        if days > 0:
            lines.append(f"Осталось: <b>{days}</b> дн.")
    else:
        lines.append("Free — бесплатный тариф")
    lines.append("")

    # Сегодня
    used_today = profile["used_seconds_today"]
    lines.append("<b>⏱ Сегодня</b>")
    if profile["has_subscription"]:
        lines.append(f"Практика: <b>{_fmt_minutes(used_today)}</b> — без лимитов")
    else:
        limit_min = FREE_DAILY_SECONDS // 60
        left_sec = max(0, FREE_DAILY_SECONDS - used_today)
        lines.append(
            f"Практика: <b>{_fmt_minutes(used_today)}</b> из {limit_min} мин"
        )
        if left_sec > 0:
            lines.append(
                f"Осталось: <b>{_fmt_minutes(left_sec)}</b> (сброс в 00:00 МСК)"
            )
        else:
            lines.append(
                "Дневной лимит исчерпан — продолжи завтра или оформи подписку."
            )
    lines.append("")

    # Всего практики
    total_sec = profile["used_seconds_total"]
    lines.append("<b>📈 Всего практики</b>")
    lines.append(f"<b>{_fmt_total_practice(total_sec)}</b> за всё время")
    lines.append("")

    # Напоминания
    lines.append("<b>⏰ Напоминания</b>")
    if profile["reminder_enabled"]:
        hour = profile["reminder_hour"]
        lines.append(f"Включены — ежедневно в <b>{hour:02d}:00</b> МСК")
    else:
        lines.append("Отключены")

    return "\n".join(lines)


@dp.message(Command("profile"))
async def cmd_profile(message: Message) -> None:
    if not message.from_user:
        return
    try:
        profile = await asyncio.wait_for(
            get_user_profile(message.from_user.id),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        logger.warning("[profile] get_user_profile превысил timeout 5с")
        profile = None
    except Exception as exc:
        logger.warning("[profile] get_user_profile упал: %s", exc)
        profile = None

    text = _build_profile_text(message, profile)
    has_sub = bool(profile and profile.get("has_subscription"))
    await message.answer(
        text=text,
        reply_markup=_profile_keyboard(has_sub),
        parse_mode="HTML",
    )


@dp.callback_query(lambda c: c.data == "profile:subscribe")
async def cb_profile_subscribe(query: CallbackQuery) -> None:
    await query.answer()
    if query.message:
        await query.message.answer(
            text=SUBSCRIBE_TEXT,
            reply_markup=_subscribe_keyboard(),
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
    "Оплата картой, SberPay или ЮМани — через ЮКассу прямо в Telegram. "
    "<i>Чек будет отправлен на указанный email.</i>"
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


# ---- Планы подписки (единственное место, где эти данные хранятся в боте) -----
_PLAN_CATALOG: dict[str, dict] = {
    "monthly": {
        "title": "English Tutor — подписка на месяц",
        "description": "Безлимитные разговоры с AI-репетитором на 30 дней.",
        "amount_rub": PRICE_MONTHLY_RUB,
        "days": 30,
        "label": "месяц",
    },
    "yearly": {
        "title": "English Tutor — подписка на год",
        "description": "Безлимитные разговоры с AI-репетитором на 365 дней (экономия ~40%).",
        "amount_rub": PRICE_YEARLY_RUB,
        "days": 365,
        "label": "год",
    },
}


def _build_provider_data(plan_key: str, plan: dict) -> Optional[str]:
    """Формирует provider_data для ЮКассы (receipt / ФФД 1.2).

    Сумма в чеке в РУБЛЯХ (в отличие от prices, где в копейках).
    """
    if not YOOKASSA_FISCALIZATION:
        return None
    data = {
        "receipt": {
            "items": [
                {
                    "description": plan["title"],
                    "quantity": "1.00",
                    "amount": {
                        "value": f"{plan['amount_rub']:.2f}",
                        "currency": "RUB",
                    },
                    "vat_code": YOOKASSA_VAT_CODE,
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                }
            ]
        }
    }
    return json.dumps(data, ensure_ascii=False)


@dp.callback_query(lambda c: c.data and c.data.startswith("subscribe:"))
async def cb_subscribe(callback: CallbackQuery) -> None:
    """При клике на тариф — посылаем sendInvoice с provider_token ЮКассы."""
    plan_key = (callback.data or "").split(":", 1)[-1]
    plan = _PLAN_CATALOG.get(plan_key)
    if not plan:
        await callback.answer("Неизвестный тариф", show_alert=True)
        return

    if not YOOKASSA_PROVIDER_TOKEN:
        await callback.answer(
            "Платёжная система временно недоступна. Попробуйте позже.",
            show_alert=True,
        )
        logger.error("YOOKASSA_PROVIDER_TOKEN is empty — cannot send invoice")
        return

    await callback.answer()
    if not callback.message or not callback.from_user:
        return

    tg_id = callback.from_user.id
    # invoice_payload: "sub:{plan_key}:{tg_id}" — читаем в successful_payment.
    payload = f"sub:{plan_key}:{tg_id}"
    amount_kop = plan["amount_rub"] * 100

    try:
        await bot.send_invoice(
            chat_id=callback.message.chat.id,
            title=plan["title"],
            description=plan["description"],
            payload=payload,
            provider_token=YOOKASSA_PROVIDER_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label=plan["title"], amount=amount_kop)],
            provider_data=_build_provider_data(plan_key, plan),
            need_email=YOOKASSA_FISCALIZATION,
            send_email_to_provider=YOOKASSA_FISCALIZATION,
            need_phone_number=False,
            need_shipping_address=False,
        )
        logger.info(
            "Invoice sent: tg_id=%s plan=%s amount=%s shop=%s",
            tg_id, plan_key, amount_kop, YOOKASSA_SHOP_ID or "?",
        )
    except Exception:
        logger.exception("Failed to send invoice to tg_id=%s plan=%s", tg_id, plan_key)
        await callback.message.answer(
            "Не удалось выставить счёт. Попробуйте ещё раз через пару минут. "
            "Если проблема повторится — напишите в поддержку."
        )


# ---- pre_checkout_query: отвечаем строго в течение 10 секунд ----
@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    payload = query.invoice_payload or ""
    # Быстрая валидация: payload должен быть вида sub:<plan>:<tg_id>.
    parts = payload.split(":")
    ok = (
        len(parts) == 3
        and parts[0] == "sub"
        and parts[1] in _PLAN_CATALOG
        and parts[2].isdigit()
    )
    try:
        if ok:
            await bot.answer_pre_checkout_query(query.id, ok=True)
        else:
            await bot.answer_pre_checkout_query(
                query.id, ok=False, error_message="Некорректные данные платежа. Повторите попытку."
            )
            logger.warning("pre_checkout rejected: payload=%r", payload)
    except Exception:
        logger.exception("pre_checkout_query answer failed")


# ---- successful_payment: зачисляем дни, записываем в payments ----
@dp.message(lambda m: m.successful_payment is not None)
async def on_successful_payment(message: Message) -> None:
    if not message.successful_payment or not message.from_user:
        return
    sp = message.successful_payment
    tg_id = message.from_user.id
    payload = sp.invoice_payload or ""
    parts = payload.split(":")
    if len(parts) != 3 or parts[0] != "sub":
        logger.error("successful_payment: bad payload=%r from tg_id=%s", payload, tg_id)
        await message.answer("Оплата получена, но возникла ошибка зачисления. Напишите в поддержку — мы всё выдадим вручную.")
        return

    plan_key = parts[1]
    plan = _PLAN_CATALOG.get(plan_key)
    if not plan:
        logger.error("successful_payment: unknown plan=%r payload=%r", plan_key, payload)
        return

    provider_payment_id = sp.provider_payment_charge_id or sp.telegram_payment_charge_id
    amount_rub = (sp.total_amount or 0) / 100.0

    try:
        until = await credit_subscription_payment(
            tg_id=tg_id,
            plan=plan_key,
            days=plan["days"],
            amount_rub=amount_rub,
            provider_payment_id=provider_payment_id,
            notes=f"yookassa shop={YOOKASSA_SHOP_ID or '?'} tg_charge={sp.telegram_payment_charge_id}",
        )
    except Exception:
        logger.exception("Failed to credit payment: tg_id=%s payload=%r", tg_id, payload)
        await message.answer(
            "Оплата получена, но не удалось сразу зачислить дни. "
            f"Напишите в поддержку, укажите ID: <code>{provider_payment_id}</code>.",
            parse_mode="HTML",
        )
        return

    until_str = _fmt_subscription_until(until) if until else "нет данных"
    await message.answer(
        f"✅ Платёж прошёл. Подписка «<b>{plan['label']}</b>» активна до <b>{until_str}</b>.\n\n"
        "Спасибо и удачной практики! Жми «🎙 Начать разговор» в меню.",
        parse_mode="HTML",
    )
    logger.info(
        "Payment credited: tg_id=%s plan=%s days=%d amount=%.2f provider_id=%s",
        tg_id, plan_key, plan["days"], amount_rub, provider_payment_id,
    )


# ─── Battle Mode ─────────────────────────────────────────────────────────────
# Как это работает:
#   1. Юзер в ЛЮБОМ чате пишет `@kmo_ai_english_bot battle` → inline_query.
#      Мы показываем одну "карточку" — при клике она постится в чат.
#   2. На chosen_inline_result дёргаем backend.battle_create (получаем id +
#      тему). Отредактировать уже отправленное inline-сообщение мы можем
#      через inline_message_id, которое Telegram присылает в этом же апдейте.
#   3. Кнопка «⚔️ Принять вызов» — callback_data="battle:accept:<id>".
#      Оппонент жмёт → вызываем battle_accept → даём обоим ссылку на Mini App
#      с deep-link startapp=battle_<id>_<side>.
#   4. Оба записывают по 60 сек в Mini App → запись улетает в backend →
#      LLM-судья → результат постится обратно в чат через edit_message.

BATTLE_INTRO = (
    "⚔️ <b>Battle Mode</b> — англо-дуэль с другом.\n\n"
    "Бросай вызов в любом чате — пишешь <code>@kmo_ai_english_bot battle</code>, "
    "и карточка с темой прилетает в чат. Друг жмёт «Принять», вы оба "
    "записываете 60-секундный аргумент на английском, а ИИ-судья оценивает "
    "грамматику, беглость и аргументацию и называет победителя.\n\n"
    "<b>Что даёт:</b> быстрый способ практиковать speaking без учителя — "
    "и это реально весело. Приглашай друзей, даже если у них нет подписки "
    "(оппоненту бот не нужен)."
)


def _battle_chat_keyboard(battle_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚔️ Принять вызов",
                    callback_data=f"battle:accept:{battle_id}",
                )
            ],
        ]
    )


def _battle_pending_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура в момент, когда только что отправили инлайн-карточку,
    но backend ещё не дал id. Telegram требует что-то отрисовать — сделаем
    заглушку, которая даст «Обновить»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏳ Создаём вызов…",
                    callback_data="battle:noop",
                )
            ],
        ]
    )


def _battle_miniapp_keyboard(battle_id: int, side: str) -> InlineKeyboardMarkup:
    """Кнопка «Записать ответ» → Mini App со startapp=battle_<id>_<side>.

    Telegram WebAppInfo не поддерживает startapp в прямом виде у инлайн-кнопки
    (это для t.me-ссылок). Поэтому даём ссылку t.me/<bot>?startapp=... через
    InlineKeyboardButton.url — Telegram откроет Mini App с нужным payload.
    """
    start_param = f"battle_{battle_id}_{side}"
    url = f"https://t.me/kmo_ai_english_bot?startapp={start_param}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎤 Записать ответ (60 сек)", url=url)],
        ]
    )


@dp.message(Command("battle"))
async def cmd_battle(message: Message) -> None:
    """Объяснить, как бросить вызов."""
    await message.answer(
        text=BATTLE_INTRO,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⚔️ Бросить вызов другу",
                        switch_inline_query="battle",
                    )
                ],
            ]
        ),
    )


@dp.inline_query()
async def inline_battle(query: InlineQuery) -> None:
    """Показываем одну карточку — "Бросить вызов"."""
    q = (query.query or "").strip().lower()
    # Показываем карточку для любого запроса — чтобы юзер не гадал с синтаксисом.
    # Если хочется фильтровать — раскомментируй условие.
    # if q and q != "battle":
    #     await query.answer(results=[], cache_time=1)
    #     return
    _ = q

    article = InlineQueryResultArticle(
        id="battle:new",
        title="⚔️ Бросить вызов на англо-дуэль",
        description="60 сек на английском · ИИ-судья выберет победителя",
        input_message_content=InputTextMessageContent(
            message_text=(
                "⚔️ <b>Вызов на англо-дуэль!</b>\n\n"
                "Тема придёт сразу после принятия. У каждого будет 60 секунд, "
                "чтобы записать аргумент на английском. ИИ-судья оценит "
                "грамматику, беглость и аргументацию.\n\n"
                "<i>Создаём вызов…</i>"
            ),
            parse_mode="HTML",
        ),
        reply_markup=_battle_pending_keyboard(),
    )
    await query.answer(
        results=[article],
        cache_time=1,
        is_personal=True,
    )


@dp.chosen_inline_result()
async def on_inline_chosen(chosen: ChosenInlineResult) -> None:
    """Юзер кликнул по карточке — значит сообщение уже улетело в чат.
    Создаём battle в backend и редактируем inline-сообщение."""
    if chosen.result_id != "battle:new":
        return
    if not chosen.inline_message_id:
        # Telegram не дал inline_message_id — нечего редактировать
        logger.warning("[battle] chosen_inline_result без inline_message_id")
        return
    tg_id = chosen.from_user.id
    result = await backend_client.battle_create(
        initiator_tg_id=tg_id,
        chat_id=None,  # у inline нет конкретного chat_id
        chat_message_id=None,
        inline_message_id=chosen.inline_message_id,
    )
    if result is None:
        try:
            await bot.edit_message_text(
                inline_message_id=chosen.inline_message_id,
                text=(
                    "⚔️ <b>Не удалось создать вызов</b>\n\n"
                    "Возможно, нужна активная подписка или бэкенд недоступен. "
                    "Напиши мне /battle в личку, чтобы разобраться."
                ),
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("[battle] edit_message fail: %s", exc)
        return

    text = (
        f"⚔️ <b>Вызов на англо-дуэль</b>\n\n"
        f"<b>Тема:</b> {result.topic_title_ru}\n\n"
        f"Правила: у каждого 60 секунд, чтобы записать аргумент на "
        f"английском. ИИ-судья оценит грамматику, беглость и аргументацию. "
        f"Призыватель уже в игре — ждём оппонента.\n\n"
        f"<i>У вас 24 часа на принятие.</i>"
    )
    try:
        await bot.edit_message_text(
            inline_message_id=chosen.inline_message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=_battle_chat_keyboard(result.id),
        )
    except Exception as exc:
        logger.warning("[battle] edit_message_text fail: %s", exc)


@dp.callback_query(lambda c: c.data and c.data.startswith("battle:accept:"))
async def cb_battle_accept(callback: CallbackQuery) -> None:
    if not callback.data:
        await callback.answer()
        return
    try:
        battle_id = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("Кривой id", show_alert=True)
        return

    opponent_tg_id = callback.from_user.id
    result = await backend_client.battle_accept(
        battle_id=battle_id, opponent_tg_id=opponent_tg_id,
    )
    if result is None:
        await callback.answer(
            "Вызов уже принят, просрочен или ты сам его создал.",
            show_alert=True,
        )
        return

    if result.initiator_tg_id == opponent_tg_id:
        await callback.answer(
            "Нельзя принимать собственный вызов — позови кого-нибудь.",
            show_alert=True,
        )
        return

    await callback.answer("Вызов принят — лови задание в личке.")

    # Редактируем chat-сообщение: битва принята, оба получили задание
    topic_line = f"<b>Тема:</b> {result.topic_title_ru}"
    chat_text = (
        f"⚔️ <b>Вызов принят</b>\n\n"
        f"{topic_line}\n\n"
        f"Оба участника получили задание в ЛС. После записи обоих "
        f"ответов ИИ-судья объявит победителя прямо здесь."
    )
    try:
        if callback.inline_message_id:
            await bot.edit_message_text(
                inline_message_id=callback.inline_message_id,
                text=chat_text,
                parse_mode="HTML",
            )
        elif callback.message:
            await callback.message.edit_text(
                text=chat_text, parse_mode="HTML",
            )
    except Exception as exc:
        logger.warning("[battle] edit accepted msg fail: %s", exc)

    # Шлём задания в ЛС каждому
    dm_text_common = (
        f"⚔️ <b>Battle #{result.id}</b>\n\n"
        f"<b>Тема:</b> {result.topic_title_ru}\n"
        f"<b>Вопрос:</b> <i>{result.prompt_en}</i>\n\n"
    )
    try:
        await bot.send_message(
            chat_id=result.initiator_tg_id,
            text=(
                dm_text_common
                + f"<b>Твоя позиция:</b> {result.side_a_ru}\n\n"
                + "Открывай Mini App и записывай 60-секундный аргумент."
            ),
            parse_mode="HTML",
            reply_markup=_battle_miniapp_keyboard(result.id, "a"),
        )
    except Exception as exc:
        logger.warning(
            "[battle] не удалось отправить DM инициатору %s: %s",
            result.initiator_tg_id, exc,
        )
    try:
        await bot.send_message(
            chat_id=result.opponent_tg_id,
            text=(
                dm_text_common
                + f"<b>Твоя позиция:</b> {result.side_b_ru}\n\n"
                + "Открывай Mini App и записывай 60-секундный аргумент."
            ),
            parse_mode="HTML",
            reply_markup=_battle_miniapp_keyboard(result.id, "b"),
        )
    except Exception as exc:
        logger.warning(
            "[battle] не удалось отправить DM оппоненту %s: %s",
            result.opponent_tg_id, exc,
        )


@dp.callback_query(lambda c: c.data == "battle:noop")
async def cb_battle_noop(callback: CallbackQuery) -> None:
    await callback.answer("Секунду, создаю вызов…")


async def _set_bot_commands() -> None:
    """Задать список команд, который виден в меню Telegram."""
    await bot.set_my_commands(
        commands=[
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="guide", description="Как заниматься (инструкция)"),
            BotCommand(command="profile", description="Мой профиль"),
            BotCommand(command="subscribe", description="Подписка"),
            BotCommand(command="reminder", description="Напоминания"),
            BotCommand(command="battle", description="Англо-дуэль с другом"),
            BotCommand(command="quest", description="Мой квест дня"),
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
    # Внутренний HTTP-сервер (слушает backend).
    internal_port = int(os.getenv("BOT_INTERNAL_PORT", "8080"))
    try:
        await start_internal_server(bot, port=internal_port)
    except Exception as exc:
        logger.error("Internal HTTP server failed to start: %s", exc)
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
