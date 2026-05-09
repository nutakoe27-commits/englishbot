import asyncio
import logging
import os
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
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
    set_user_learning_goal,
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

# Username бота без @ — для построения deep-link'ов t.me/<bot>?start=...
BOT_USERNAME: str = os.getenv("BOT_USERNAME", "kmo_ai_english_bot").lstrip("@")

# Free Period — промо-период без оплаты. При FREE_PERIOD=1 бот скрывает
# кнопки подписки, /subscribe возвращает уведомление вместо инвойса,
# в /profile нет блока «оформить подписку», лимит 10 минут не показывается.
# Платёжные обработчики (pre_checkout/successful_payment) остаются активными,
# чтобы корректно обработать уже отправленные инвойсы и ручные выдачи.
FREE_PERIOD: bool = os.getenv("FREE_PERIOD", "0") == "1"

FREE_PERIOD_TEXT = (
    "🎁 <b>Сейчас всё бесплатно!</b>\n\n"
    "Доступ к голосовому тьютору, Battle Mode и Daily Quest — без лимитов "
    "и без подписки. Просто открой /start и нажми «🎤 Начать разговор».\n\n"
    "Когда промо-период закончится — мы напишем заранее."
)

# ─── Onboarding learning goal ─────────────────────────────────────────────
# Цель изучения для подмеса в системный промпт тьютора. Пишется в
# users.learning_goal (миграция 0004). Меняется через /goal.
_GOAL_LABELS: dict[str, str] = {
    "travel": "🌍 Путешествия",
    "work": "💼 Работа / карьера",
    "daily": "💬 Повседневное общение",
    "exam": "🎓 Экзамен (IELTS/TOEFL)",
    "fun": "✨ Просто интересно",
}

_GOAL_PROMPT_TEXT = (
    "🎯 <b>Зачем тебе английский?</b>\n\n"
    "Я подкину релевантную лексику и темы под твою цель. "
    "Можно поменять в любой момент: /goal."
)


def _goal_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"goal:{key}")]
        for key, label in _GOAL_LABELS.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

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
    # Deep-link из mini app: «/start subscribe» — сразу показываем экран подписки.
    # В Free Period подписка не нужна — отдаём промо-сообщение.
    payload = (command.args or "").strip().lower()
    if payload == "subscribe":
        if FREE_PERIOD:
            await message.answer(
                text=FREE_PERIOD_TEXT,
                parse_mode="HTML",
                reply_markup=_miniapp_keyboard(),
            )
        else:
            await message.answer(
                text=SUBSCRIBE_TEXT,
                parse_mode="HTML",
                reply_markup=_subscribe_keyboard(),
            )
        return

    # Deep-link из callback'а в чате: «/start battle_accept_<id>» — оппонент
    # не открывал бота, был перенаправлен сюда; принимаем вызов от его имени.
    if payload.startswith("battle_accept_"):
        try:
            battle_id = int(payload[len("battle_accept_"):])
        except ValueError:
            battle_id = 0
        if battle_id > 0:
            await _handle_start_battle_accept(message, battle_id)
            return

    user_name = message.from_user.first_name if message.from_user else "друг"

    # Если у юзера ещё не задана цель — после приветствия показываем
    # goal-клавиатуру. Профиль может быть None (юзер ещё не открывал
    # Mini App) — в этом случае тоже показываем (запишется при клике).
    has_goal = False
    if message.from_user:
        try:
            profile = await asyncio.wait_for(
                get_user_profile(message.from_user.id), timeout=3.0,
            )
            has_goal = bool(profile and profile.get("learning_goal"))
        except Exception:
            has_goal = False

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

    # Onboarding-флоу: предлагаем выбрать цель. Не блокируем — это
    # отдельное сообщение под основным приветствием.
    if not has_goal:
        await message.answer(
            text=_GOAL_PROMPT_TEXT,
            parse_mode="HTML",
            reply_markup=_goal_keyboard(),
        )


# ─── /goal ───────────────────────────────────────────────────────────────────
@dp.message(Command("goal"))
async def cmd_goal(message: Message) -> None:
    """Показать клавиатуру для смены onboarding-цели."""
    await message.answer(
        text=_GOAL_PROMPT_TEXT,
        parse_mode="HTML",
        reply_markup=_goal_keyboard(),
    )


@dp.callback_query(lambda c: c.data and c.data.startswith("goal:"))
async def cb_goal(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user:
        await callback.answer()
        return
    key = callback.data.split(":", 1)[-1].strip().lower()
    if key not in _GOAL_LABELS:
        await callback.answer("Неизвестная цель", show_alert=True)
        return
    ok = await set_user_learning_goal(callback.from_user.id, key)
    if not ok:
        # Юзер ещё не upsert'нут в БД — попросим открыть Mini App один раз.
        await callback.answer(
            "Сначала открой Mini App — я тебя запомню, потом /goal сработает.",
            show_alert=True,
        )
        return
    await callback.answer(f"Записал: {_GOAL_LABELS[key]}")
    if callback.message:
        try:
            await callback.message.edit_text(
                text=(
                    f"🎯 <b>Цель: {_GOAL_LABELS[key]}</b>\n\n"
                    "Учту в следующей сессии — буду подкидывать релевантные "
                    "слова и темы. Поменять цель — /goal."
                ),
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("[goal] edit msg fail: %s", exc)


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
    lines = [
        "ℹ️ <b>Доступные команды</b>",
        "",
        "/start — главное меню и кнопка запуска разговора",
        "/guide — <b>как правильно заниматься</b> (прочитай обязательно)",
        "/profile — твой прогресс и статистика",
    ]
    if not FREE_PERIOD:
        lines.append("/subscribe — информация о подписке")
    lines += [
        "/reminder — настройка ежедневного напоминания",
        "/battle — англо-дуэль с другом (ИИ-судья оценивает ответы)",
        "/quest — твой квест дня (+30 мин к дневному лимиту)",
        "/help — эта справка",
        "",
        "Чтобы начать практику — нажми «🎤 Начать разговор» в /start. "
        "Для дуэли с другом в любом чате: <code>@kmo_ai_english_bot battle</code>.",
    ]
    await message.answer(text="\n".join(lines), parse_mode="HTML")


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


def _pluralize_days(n: int) -> str:
    """Русское склонение: 1 день / 2 дня / 5 дней."""
    n_abs = abs(n) % 100
    if 11 <= n_abs <= 14:
        return "дней"
    last = n_abs % 10
    if last == 1:
        return "день"
    if 2 <= last <= 4:
        return "дня"
    return "дней"


def _profile_keyboard(has_sub: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="🎤 Начать разговор",
                web_app=WebAppInfo(url=MINIAPP_URL),
            )
        ],
    ]
    # В Free Period кнопку подписки не показываем — она просто не нужна.
    if not FREE_PERIOD:
        sub_text = "⭐ Продлить подписку" if has_sub else "⭐ Оформить подписку"
        rows.append([
            InlineKeyboardButton(text=sub_text, callback_data="profile:subscribe")
        ])
    rows += [
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
    return InlineKeyboardMarkup(inline_keyboard=rows)


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

    # Streak — главный мотиватор «вернись завтра».
    streak_days = int(profile.get("streak_days") or 0)
    best_streak = int(profile.get("best_streak_days") or 0)
    if streak_days > 0:
        if best_streak > streak_days:
            streak_line = (
                f"🔥 <b>Стрик: {streak_days}</b> "
                f"{_pluralize_days(streak_days)} (рекорд {best_streak})"
            )
        else:
            streak_line = (
                f"🔥 <b>Стрик: {streak_days}</b> "
                f"{_pluralize_days(streak_days)} — это твой рекорд!"
            )
        lines.append(streak_line)
        lines.append("")

    # Подписка
    if FREE_PERIOD:
        lines.append("<b>🎁 Бесплатный период</b>")
        lines.append("Все возможности открыты — без лимитов и без подписки.")
    else:
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
    bonus_today = int(profile.get("bonus_seconds_today") or 0)
    lines.append("<b>⏱ Сегодня</b>")
    if FREE_PERIOD or profile["has_subscription"]:
        lines.append(f"Практика: <b>{_fmt_minutes(used_today)}</b> — без лимитов")
    else:
        limit_min = FREE_DAILY_SECONDS // 60
        total_limit = FREE_DAILY_SECONDS + bonus_today
        left_sec = max(0, total_limit - used_today)
        if bonus_today > 0:
            lines.append(
                f"Практика: <b>{_fmt_minutes(used_today)}</b> из "
                f"{limit_min} мин + бонус <b>{_fmt_minutes(bonus_today)}</b>"
            )
        else:
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

    # Battle-статистика
    b_total = int(profile.get("battles_total") or 0)
    b_won = int(profile.get("battles_won") or 0)
    b_draw = int(profile.get("battles_draw") or 0)
    b_lost = int(profile.get("battles_lost") or 0)
    b_inprog = int(profile.get("battles_in_progress") or 0)
    lines.append("<b>🎯 Battle</b>")
    if b_total == 0 and b_inprog == 0:
        lines.append(
            "Ещё не сыграно ни одного батла. Попробуй /battle — "
            "брось вызов другу."
        )
    else:
        lines.append(
            f"Сыграно: <b>{b_total}</b> · побед <b>{b_won}</b> · "
            f"ничьих <b>{b_draw}</b> · поражений <b>{b_lost}</b>"
        )
        if b_inprog > 0:
            lines.append(f"В процессе: <b>{b_inprog}</b>")
    lines.append("")

    # Квесты
    q_total = int(profile.get("quests_completed_total") or 0)
    q_week = int(profile.get("quests_completed_7d") or 0)
    q_active = profile.get("quest_active_title")
    lines.append("<b>✨ Квесты</b>")
    if q_total == 0 and not q_active:
        lines.append("Пока ни одного. Попробуй /quest — получи бонусные минуты.")
    else:
        lines.append(
            f"Выполнено всего: <b>{q_total}</b> · за неделю: <b>{q_week}</b>"
        )
        if q_active:
            lines.append(f"Активный: <b>{q_active}</b>")
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
    if not query.message:
        return
    if FREE_PERIOD:
        await query.message.answer(
            text=FREE_PERIOD_TEXT,
            reply_markup=_miniapp_keyboard(),
            parse_mode="HTML",
        )
        return
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
    if FREE_PERIOD:
        await message.answer(
            text=FREE_PERIOD_TEXT,
            parse_mode="HTML",
            reply_markup=_miniapp_keyboard(),
        )
        return
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
    # Free Period: инвойс не отправляем. Это safety-net на случай, если у юзера
    # остался старый экран /subscribe в Telegram (под флагом эти кнопки не
    # рисуются, но callback из истории чата может прилететь).
    if FREE_PERIOD:
        await callback.answer("Сейчас всё бесплатно — подписка не нужна.", show_alert=True)
        if callback.message:
            await callback.message.answer(
                text=FREE_PERIOD_TEXT,
                parse_mode="HTML",
                reply_markup=_miniapp_keyboard(),
            )
        return

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
    url = f"https://t.me/{BOT_USERNAME}?startapp={start_param}"
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
    logger.info("[battle] inline_query from tg_id=%s query=%r", query.from_user.id, query.query)
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
    logger.info(
        "[battle] chosen_inline_result: result_id=%s tg_id=%s inline_msg_id=%s",
        chosen.result_id, chosen.from_user.id, chosen.inline_message_id,
    )
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


async def _try_send_battle_dm(
    *, tg_id: int, text: str, reply_markup: InlineKeyboardMarkup,
) -> bool:
    """Шлём ЛС участнику battle. True — доставлено, False — юзер не открывал
    бота / заблокировал / chat not found.

    TelegramForbiddenError — бот заблокирован или не запущен.
    TelegramBadRequest — chat not found / bot can't initiate conversation.
    """
    try:
        await bot.send_message(
            chat_id=tg_id, text=text, parse_mode="HTML", reply_markup=reply_markup,
        )
        return True
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        logger.warning("[battle] DM to %s blocked/unreachable: %s", tg_id, exc)
        return False
    except Exception as exc:
        logger.warning("[battle] DM to %s failed: %s", tg_id, exc)
        return False


async def _send_battle_dms_and_render_chat(
    result: backend_client.BattleAcceptResult,
    *,
    inline_message_id: Optional[str] = None,
    chat_id: Optional[int] = None,
    chat_message_id: Optional[int] = None,
) -> bool:
    """Общий хвост успешного accept: DM оппоненту → DM инициатору (best-effort)
    → перерисовать chat-сообщение «принят». Используется и из callback'а
    в чате, и из /start deep-link обработчика.

    Возвращает True если ЛС оппоненту доставлено (вызов «жив»), False иначе —
    тогда вызывающая сторона должна откатить accept.
    """
    initiator_display = await _display_name_for(result.initiator_tg_id)
    opponent_display = await _display_name_for(result.opponent_tg_id)

    dm_text_common = (
        f"⚔️ <b>Battle #{result.id}</b>\n\n"
        f"<b>Тема:</b> {result.topic_title_ru}\n"
        f"<b>Вопрос:</b> <i>{result.prompt_en}</i>\n\n"
    )
    dm_to_opponent = (
        dm_text_common
        + f"<b>Соперник:</b> {initiator_display}\n"
        + f"<b>Твоя позиция:</b> {result.side_b_ru}\n\n"
        + "Открывай Mini App и записывай 60-секундный аргумент."
    )
    dm_to_initiator = (
        dm_text_common
        + f"<b>Соперник:</b> {opponent_display}\n"
        + f"<b>Твоя позиция:</b> {result.side_a_ru}\n\n"
        + "Открывай Mini App и записывай 60-секундный аргумент."
    )

    ok_opp = await _try_send_battle_dm(
        tg_id=result.opponent_tg_id,
        text=dm_to_opponent,
        reply_markup=_battle_miniapp_keyboard(result.id, "b"),
    )
    if not ok_opp:
        return False

    ok_init = await _try_send_battle_dm(
        tg_id=result.initiator_tg_id,
        text=dm_to_initiator,
        reply_markup=_battle_miniapp_keyboard(result.id, "a"),
    )
    if not ok_init:
        logger.warning(
            "[battle] инициатор %s не получил DM — продолжаем без него",
            result.initiator_tg_id,
        )

    chat_text = (
        f"⚔️ <b>Вызов принят</b>\n\n"
        f"<b>Тема:</b> {result.topic_title_ru}\n"
        f"<b>Участники:</b> {initiator_display} vs {opponent_display}\n\n"
        f"Оба участника получили задание в ЛС. После записи обоих "
        f"ответов ИИ-судья объявит победителя прямо здесь."
    )
    try:
        if inline_message_id:
            await bot.edit_message_text(
                inline_message_id=inline_message_id,
                text=chat_text,
                parse_mode="HTML",
            )
        elif chat_id is not None and chat_message_id is not None:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=chat_message_id,
                text=chat_text,
                parse_mode="HTML",
            )
    except Exception as exc:
        logger.warning("[battle] edit accepted msg fail: %s", exc)

    return True


async def _accept_battle_for_user(
    *,
    battle_id: int,
    opponent_tg_id: int,
) -> tuple[Optional[backend_client.BattleAcceptResult], Optional[str]]:
    """Дёргает backend.battle_accept + базовая валидация. Возвращает
    (result, error_message). Если result не None — accept на стороне
    backend'а прошёл, остаётся доставить DMs."""
    result = await backend_client.battle_accept(
        battle_id=battle_id, opponent_tg_id=opponent_tg_id,
    )
    if result is None:
        return None, "Вызов уже принят, просрочен или ты сам его создал."
    if result.initiator_tg_id == opponent_tg_id:
        return None, "Нельзя принимать собственный вызов — позови кого-нибудь."
    return result, None


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
    result, err = await _accept_battle_for_user(
        battle_id=battle_id, opponent_tg_id=opponent_tg_id,
    )
    if err is not None:
        await callback.answer(err, show_alert=True)
        return
    assert result is not None

    ok = await _send_battle_dms_and_render_chat(
        result,
        inline_message_id=callback.inline_message_id,
        chat_id=callback.message.chat.id if callback.message else None,
        chat_message_id=callback.message.message_id if callback.message else None,
    )
    if not ok:
        # Оппоненту не доставилось → откатываем accept в backend и
        # перебрасываем юзера в чат с ботом через deep-link. После /start
        # cmd_start примет вызов автоматически.
        await backend_client.battle_revert_accept(
            battle_id=battle_id, opponent_tg_id=opponent_tg_id,
        )
        deep_link = f"https://t.me/{BOT_USERNAME}?start=battle_accept_{battle_id}"
        try:
            await callback.answer(
                text="Открываю бота — нажми Start, и вызов сразу примется.",
                url=deep_link,
            )
        except Exception:
            # Защитный fallback: если по какой-то причине callback с url
            # не поддержан клиентом — показываем alert с подсказкой.
            await callback.answer(
                f"Открой @{BOT_USERNAME}, нажми Start — и возвращайся к "
                "кнопке «Принять».",
                show_alert=True,
            )
        return

    await callback.answer("Вызов принят — лови задание в личке.")


async def _handle_start_battle_accept(message: Message, battle_id: int) -> None:
    """Юзер пришёл по deep-link `/start battle_accept_<id>` — принимаем
    вызов от его имени и шлём задание в этот же DM."""
    if not message.from_user:
        return
    opponent_tg_id = message.from_user.id

    result, err = await _accept_battle_for_user(
        battle_id=battle_id, opponent_tg_id=opponent_tg_id,
    )
    if err is not None:
        await message.answer(
            err + "\n\nМожешь бросить новый вызов: /battle.",
            reply_markup=_miniapp_keyboard(),
        )
        return
    assert result is not None

    ok = await _send_battle_dms_and_render_chat(
        result,
        inline_message_id=result.inline_message_id,
        chat_id=result.chat_id,
        chat_message_id=result.chat_message_id,
    )
    if not ok:
        # Маловероятно — мы только что в его DM, бот может писать. Откатываем.
        await backend_client.battle_revert_accept(
            battle_id=battle_id, opponent_tg_id=opponent_tg_id,
        )
        await message.answer(
            "Не удалось доставить задание. Попробуй ещё раз — открой чат "
            "с другом и нажми «Принять» снова.",
        )
        return

    await message.answer(
        "✅ <b>Вызов принят!</b> Задание уже выше — открывай Mini App "
        "и записывай ответ.",
        parse_mode="HTML",
    )


async def _display_name_for(tg_id: int) -> str:
    """Возвращает красивое имя для показа: @username > First Last > Player <id>."""
    if not tg_id:
        return "Player"
    try:
        chat = await bot.get_chat(tg_id)
        if getattr(chat, "username", None):
            return f"@{chat.username}"
        parts = [p for p in (getattr(chat, "first_name", None), getattr(chat, "last_name", None)) if p]
        if parts:
            return " ".join(parts)
    except Exception as exc:
        logger.debug("[battle] get_chat(%s) failed: %s", tg_id, exc)
    return f"Player {tg_id}"


@dp.callback_query(lambda c: c.data and c.data.startswith("battle:revanche:"))
async def cb_battle_revanche(callback: CallbackQuery) -> None:
    """Реванш: кнопка под результатом judged-battle. Создаёт новый
    battle с теми же двумя tg_id'ами в статусе accepted, шлёт обоим
    задание в ЛС, оверрайдит исходное сообщение результатом."""
    if not callback.data or not callback.from_user:
        await callback.answer()
        return
    try:
        old_battle_id = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("Кривой id", show_alert=True)
        return

    requester_tg_id = callback.from_user.id
    result = await backend_client.battle_revanche(
        old_battle_id=old_battle_id, requester_tg_id=requester_tg_id,
    )
    if result is None:
        await callback.answer(
            "Не получилось запустить реванш — старый бой не отсужен либо "
            "ты не участвовал.",
            show_alert=True,
        )
        return

    # Пробуем доставить ЛС оппоненту. Если он закрыл бот — об этом узнаем
    # сразу (TelegramForbiddenError), и сообщим инициатору реванша.
    initiator_display = await _display_name_for(result.initiator_tg_id)
    opponent_display = await _display_name_for(result.opponent_tg_id)

    dm_common = (
        f"⚔️ <b>Реванш! Battle #{result.id}</b>\n\n"
        f"<b>Тема:</b> {result.topic_title_ru}\n"
        f"<b>Вопрос:</b> <i>{result.prompt_en}</i>\n\n"
    )
    ok_opp = await _try_send_battle_dm(
        tg_id=result.opponent_tg_id,
        text=(
            dm_common
            + f"<b>Соперник:</b> {initiator_display}\n"
            + f"<b>Твоя позиция:</b> {result.side_b_ru}\n\n"
            + "Открывай Mini App и записывай 60-секундный аргумент."
        ),
        reply_markup=_battle_miniapp_keyboard(result.id, "b"),
    )
    if not ok_opp:
        await callback.answer(
            "Соперник закрыл бот — реванш сейчас не получится. Попробуй позже.",
            show_alert=True,
        )
        # Реванш-battle создан в БД, но недоставлен. Можно просто оставить
        # его — он сам expire через 24ч.
        return

    ok_init = await _try_send_battle_dm(
        tg_id=result.initiator_tg_id,
        text=(
            dm_common
            + f"<b>Соперник:</b> {opponent_display}\n"
            + f"<b>Твоя позиция:</b> {result.side_a_ru}\n\n"
            + "Открывай Mini App и записывай 60-секундный аргумент."
        ),
        reply_markup=_battle_miniapp_keyboard(result.id, "a"),
    )
    if not ok_init:
        logger.warning("[battle][revanche] инициатор %s не получил DM", result.initiator_tg_id)

    # Оверрайдим исходное сообщение в чате — убираем кнопку «Реванш»
    # (бой уже идёт) и показываем что реванш запущен.
    chat_text = (
        f"⚔️ <b>Реванш запущен (Battle #{result.id})</b>\n\n"
        f"<b>Тема:</b> {result.topic_title_ru}\n"
        f"<b>Участники:</b> {initiator_display} vs {opponent_display}\n\n"
        f"Оба получили задание в ЛС. Результат прилетит сюда после "
        f"записей обоих ответов."
    )
    try:
        if callback.inline_message_id:
            await bot.edit_message_text(
                inline_message_id=callback.inline_message_id,
                text=chat_text,
                parse_mode="HTML",
            )
        elif callback.message:
            await callback.message.edit_text(text=chat_text, parse_mode="HTML")
    except Exception as exc:
        logger.warning("[battle][revanche] edit msg fail: %s", exc)

    await callback.answer("Реванш запущен — задание уже у обоих в ЛС.")


@dp.callback_query(lambda c: c.data == "battle:noop")
async def cb_battle_noop(callback: CallbackQuery) -> None:
    await callback.answer("Секунду, создаю вызов…")


async def _set_bot_commands() -> None:
    """Задать список команд, который виден в меню Telegram."""
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="guide", description="Как заниматься (инструкция)"),
        BotCommand(command="profile", description="Мой профиль"),
    ]
    if not FREE_PERIOD:
        commands.append(BotCommand(command="subscribe", description="Подписка"))
    commands += [
        BotCommand(command="reminder", description="Напоминания"),
        BotCommand(command="goal", description="Зачем учу английский"),
        BotCommand(command="battle", description="Англо-дуэль с другом"),
        BotCommand(command="quest", description="Мой квест дня"),
        BotCommand(command="help", description="Справка по командам"),
    ]
    await bot.set_my_commands(commands=commands)


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
    # Явно запрашиваем все типы апдейтов — критично для chosen_inline_result,
    # который Telegram не шлёт по умолчанию без явного указания.
    allowed = [
        "message", "edited_message", "callback_query",
        "inline_query", "chosen_inline_result",
        "pre_checkout_query", "my_chat_member",
    ]
    await dp.start_polling(bot, skip_updates=True, allowed_updates=allowed)


if __name__ == "__main__":
    asyncio.run(main())
