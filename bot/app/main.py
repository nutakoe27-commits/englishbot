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
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    Update,
    WebAppInfo,
)
from dotenv import load_dotenv

from .internal_http import start_internal_server

import json

from .reminders import (
    credit_subscription_payment,
    get_maintenance_status,
    get_user_profile,
    get_user_reminder,
    is_db_ready,
    mark_bot_activated,
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

# Username бота без @ — для построения deep-link'ов t.me/<bot>?start=...
BOT_USERNAME: str = os.getenv("BOT_USERNAME", "kmo_ai_english_bot").lstrip("@")

# Backend ↔ bot: для вызова internal-эндпоинтов (deep-link авторизация,
# подтверждение unlink). Секрет общий с backend (env BACKEND_BOT_SECRET).
BACKEND_URL: str = os.getenv("BACKEND_URL", "http://backend:8000").rstrip("/")
BACKEND_BOT_SECRET: Optional[str] = os.getenv("BACKEND_BOT_SECRET") or None

# Free Period — промо-период без оплаты. При FREE_PERIOD=1 бот скрывает
# кнопки подписки, /subscribe возвращает уведомление вместо инвойса,
# в /profile нет блока «оформить подписку», лимит 10 минут не показывается.
# Платёжные обработчики (pre_checkout/successful_payment) остаются активными,
# чтобы корректно обработать уже отправленные инвойсы и ручные выдачи.
FREE_PERIOD: bool = os.getenv("FREE_PERIOD", "0") == "1"

FREE_PERIOD_TEXT = (
    "🎁 <b>Сейчас всё бесплатно!</b>\n\n"
    "Доступ к голосовому тьютору, грамматике и подкастам — без лимитов и "
    "без подписки. Просто открой /start и нажми «🎤 Начать разговор».\n\n"
    "Когда промо-период закончится — мы напишем заранее."
)

# Цены синхронизированы с LockScreen/Paywall в mini app.
PRICE_TRIAL3_RUB = int(os.getenv("SUBSCRIPTION_PRICE_TRIAL3_RUB", "99"))
PRICE_MONTHLY_RUB = int(os.getenv("SUBSCRIPTION_PRICE_MONTHLY_RUB", "499"))
PRICE_YEARLY_RUB = int(os.getenv("SUBSCRIPTION_PRICE_YEARLY_RUB", "2999"))

# Дневной лимит для free-тарифа (секунды). Источник истины — settings_kv
# в backend (ключ free_seconds_per_day), здесь держим фолбэк-значение для
# отображения в профиле.
FREE_DAILY_SECONDS = int(os.getenv("FREE_DAILY_SECONDS", "1200"))

# ADMIN_IDS (через запятую) — эти tg_id минуют maintenance-гейт.
_ADMIN_IDS: set[int] = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

# ЮКасса: provider_token выдаёт @BotFather при привязке магазина ЮКассы.
YOOKASSA_PROVIDER_TOKEN: str = os.getenv("YOOKASSA_PROVIDER_TOKEN", "").strip()
YOOKASSA_SHOP_ID: str = os.getenv("YOOKASSA_SHOP_ID", "").strip()
YOOKASSA_FISCALIZATION: bool = os.getenv("YOOKASSA_FISCALIZATION", "1") == "1"
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


# ─── Bot-activation middleware (миграция 0009) ────────────────────────
# Помечаем, что юзер активировал бота в Telegram. Это нужно для
# отдельной метрики в админке «Активировали бота» — до этого в БД
# попадали только те, кто открыл Mini App.
#
# Стоит ПОСЛЕ maintenance, чтобы фейковые/служебные апдейты в режиме
# тех.работ не засчитывались. Best-effort: ошибка БД не блокирует ответ.

@dp.update.outer_middleware()
async def _bot_activation_middleware(handler, event: Update, data: dict):
    user = None
    if event.message and event.message.from_user:
        user = event.message.from_user
    elif event.callback_query and event.callback_query.from_user:
        user = event.callback_query.from_user

    if user is not None and not user.is_bot:
        # Fire-and-forget: не ждём ответа БД, чтобы не задерживать handler.
        # Ошибки логируются внутри mark_bot_activated.
        asyncio.create_task(
            mark_bot_activated(
                tg_id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                language_code=user.language_code,
            )
        )
    return await handler(event, data)


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
async def _post_backend(path: str, payload: dict) -> tuple[int, dict]:
    """POST на backend internal-эндпоинт с X-Bot-Secret. Возвращает (status, json)."""
    import httpx
    headers = {"X-Bot-Secret": BACKEND_BOT_SECRET or ""}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{BACKEND_URL}{path}", json=payload, headers=headers,
            )
        try:
            data = resp.json()
        except Exception:
            data = {"detail": resp.text[:200]}
        return resp.status_code, data
    except Exception as exc:
        logger.warning("[bot→backend] %s failed: %s", path, exc)
        return 0, {"detail": str(exc)}


async def _handle_auth_deeplink(message: Message, token: str) -> None:
    """Юзер пришёл по t.me/<bot>?start=<login|link|auth>_<token>.

    Вызывает backend /api/internal/auth/apply-telegram. Отвечает понятным
    сообщением в зависимости от kind.
    """
    if not message.from_user:
        return
    code, data = await _post_backend("/api/internal/auth/apply-telegram", {
        "token": token,
        "tg_id": message.from_user.id,
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name,
        "username": message.from_user.username,
        "language_code": message.from_user.language_code,
    })
    if code != 200:
        await message.answer(
            "⚠️ Ссылка устарела или уже использована. Открой сайт ещё раз и нажми войти/привязать.",
        )
        return
    kind = data.get("kind")
    if kind == "login":
        await message.answer(
            "✅ Вход через Telegram выполнен.\n\n"
            "Возвращайся на сайт — страница обновится автоматически.",
            parse_mode="HTML",
        )
    elif kind == "link":
        if data.get("merged"):
            await message.answer(
                "✅ <b>Аккаунты объединены.</b>\n\n"
                "Прогресс, словарь и подписка сохранены. Возвращайся на сайт.",
                parse_mode="HTML",
            )
        else:
            await message.answer(
                "✅ <b>Telegram привязан к твоему аккаунту.</b>\n\n"
                "Теперь можно входить на сайте и через Telegram, и через "
                "email/пароль. Возвращайся на сайт.",
                parse_mode="HTML",
            )
    else:
        await message.answer("✅ Готово. Возвращайся на сайт.")


# ─── Подтверждение unlink email/пароля (PR-6) ──────────────────────────────
# Backend инициирует через send_bot_message: «Подтверди отвязку» с двумя
# inline-кнопками `cu:<token>` (confirm) / `cn:<token>` (cancel).

@dp.callback_query(lambda c: c.data and c.data.startswith("cu:"))
async def cb_confirm_unlink_native(query: CallbackQuery) -> None:
    if not query.from_user or not query.data:
        return
    token = query.data.split(":", 1)[1]
    code, data = await _post_backend(
        "/api/internal/auth/apply-unlink-native",
        {"token": token, "tg_id": query.from_user.id},
    )
    if code == 200:
        await query.answer("Отвязано ✓")
        try:
            if query.message:
                await query.message.edit_text(
                    "✅ <b>Email-вход отвязан.</b>\n\n"
                    "Теперь войти можно только через Telegram. Если захочешь "
                    "снова добавить email/пароль — открой настройки в mini app.",
                    parse_mode="HTML",
                )
        except Exception:
            pass
    elif code == 403:
        await query.answer("⚠️ Это подтверждение не для тебя.", show_alert=True)
    elif code == 404:
        await query.answer("Ссылка устарела.", show_alert=True)
        try:
            if query.message:
                await query.message.edit_text(
                    "⚠️ Ссылка устарела или уже использована.",
                )
        except Exception:
            pass
    else:
        await query.answer("Что-то пошло не так. Попробуй ещё раз.", show_alert=True)


@dp.callback_query(lambda c: c.data and c.data.startswith("cn:"))
async def cb_cancel_unlink_native(query: CallbackQuery) -> None:
    if not query.data:
        return
    token = query.data.split(":", 1)[1]
    await _post_backend("/api/internal/auth/cancel", {"token": token})
    await query.answer("Отменено")
    try:
        if query.message:
            await query.message.edit_text(
                "❌ Отмена. Email-вход остался без изменений.",
            )
    except Exception:
        pass


@dp.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject) -> None:
    # Deep-link от сайта: «/start <login|link|auth>_<token>» — авторизация через бот.
    # Токен base64url — case-sensitive, .lower() не делаем.
    payload_raw = (command.args or "").strip()
    for prefix in ("login_", "link_", "auth_"):
        if payload_raw.startswith(prefix):
            token = payload_raw[len(prefix):]
            await _handle_auth_deeplink(message, token)
            return

    # Deep-link из mini app: «/start subscribe» — сразу показываем экран подписки.
    # В Free Period подписка не нужна — отдаём промо-сообщение.
    payload = payload_raw.lower()
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

    user_name = message.from_user.first_name if message.from_user else "друг"

    await message.answer(
        text=(
            f"Привет, {user_name}! 👋\n\n"
            "Я — твой AI-репетитор английского. В mini app четыре режима:\n"
            "🎙 <b>Разговор</b> — голосом или текстом, как с живым носителем.\n"
            "🎧 <b>Слушание</b> — персональный подкаст под твою тему и слова.\n"
            "📝 <b>Грамматика</b> — 50 уроков от A1 до C1 или разбор твоих ошибок.\n"
            "📚 <b>Слова</b> — карточки на повтор: новое слово через 1 → 3 → 7 → 14 → 30 дней.\n\n"
            "<b>Прежде чем начать — прочитай короткую инструкцию /guide.</b> "
            "Это пара минут, но сильно поменяет то, как ты будешь учиться. "
            "Без неё многие пытаются «переводить в голове» вместо того, чтобы "
            "<i>думать на английском</i>, и быстро бросают.\n\n"
            "Когда будешь готов — жми «🎤 Начать разговор»."
        ),
        parse_mode="HTML",
        reply_markup=_miniapp_keyboard(),
    )


# ─── /guide ──────────────────────────────────────────────────────────────────
GUIDE_TEXT = (
    "📖 <b>Как заниматься английским с этим ботом</b>\n\n"
    "Главный принцип: <b>я — носитель английского, который не знает русского</b>. "
    "Я не переведу слово и не объясню грамматику по-русски — но объясню любое "
    "непонятное слово простым английским, нарисую картинку словами, дам пример "
    "в контексте. Так учатся реально думать на языке, а не «переводить в голове».\n\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "🎯 <b>4 правила, которые делают всю разницу</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n\n"
    "<b>1. Не лезь в переводчик.</b>\n"
    "Услышал незнакомое слово — не переводи. Спроси у меня прямо в чате:\n"
    "<i>— What does «overwhelmed» mean?</i>\n"
    "<i>— Can you explain it with simple words?</i>\n"
    "<i>— Give me an example, please.</i>\n\n"
    "<b>2. Говори, даже если с ошибками.</b>\n"
    "Не молчи в поисках идеальной фразы. Скажи как можешь — я пойму и мягко "
    "переформулирую правильно. Ошибки — это материал для роста, "
    "а не провал.\n\n"
    "<b>3. Если совсем ступор — переходи на русский в одной фразе.</b>\n"
    "Можно сказать по-русски, что хотел выразить. Я отвечу на английском и "
    "помогу собрать фразу. Но не злоупотребляй — цель в том, чтобы со временем "
    "русский тебе вообще не понадобился.\n\n"
    "<b>4. Регулярность важнее длительности.</b>\n"
    "10 минут каждый день дадут больше, чем час раз в неделю. Я пришлю "
    "напоминание вечером — не игнорируй его (/reminder).\n\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "🛠 <b>Четыре режима внутри mini app</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n\n"
    "<b>🎙 Разговор</b> — основное. Push-to-talk: удерживай кнопку, скажи "
    "фразу, отпусти — я отвечу голосом. Тренирует слух и произношение. "
    "Есть и текстовый чат — для шумных мест.\n\n"
    "<b>🎧 Слушание</b> — генерация подкаста под тебя. Выбираешь длительность "
    "(1–15 мин), тему и уровень. Можно включить «учитывать мои слова» — и я "
    "вплету слова из твоего словаря в речь.\n\n"
    "<b>📝 Грамматика</b> — два трека.\n"
    "  • <i>Учить правила</i>: 50 уроков от A1 до C1. Правило → 8 упражнений "
    "→ следующая тема открывается при ≥70%.\n"
    "  • <i>Проверить себя</i>: ноль настроек. Беру твои реальные ошибки из "
    "разговоров и собираю по ним 10 упражнений.\n\n"
    "<b>📚 Слова</b> — интервальное повторение. Карточка показывает слово, "
    "ты вспоминаешь перевод и жмёшь «Знаю / Не знаю». Знаешь — увидишь снова "
    "через 1 → 3 → 7 → 14 → 30 дней. Не знаешь — повторится в той же сессии.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "📚 <b>Личный словарь</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Тапни 📖 в шапке mini app — это твой словарь активной лексики. "
    "Слова оттуда подмешиваются в речь тьютора и в подкасты, чтобы ты "
    "встречал их в контексте. Они же — карточки в режиме «📚 Слова».\n\n"
    "Если в разговоре или подкасте тапнуть на слово — покажу перевод и "
    "кнопку «+ В словарь». Один тап — и слово уже учится.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "📊 <b>Прогресс</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Тапни 📊 в шапке mini app или /profile в боте: стрик, минуты по режимам, "
    "пройденные темы грамматики, медали.\n\n"
    "Стрик растёт за любую практику — голосом, текстом, подкастом или "
    "грамматикой. Главное — каждый день.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "⚙️ <b>Настройки под тебя</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n\n"
    "В mini app шестерёнка → подгони бота:\n"
    "• <b>Уровень</b> (A2–C1) — насколько простым языком я буду говорить\n"
    "• <b>Роль</b> — учитель, бариста, интервьюер, друг и др.\n"
    "• <b>Длина ответов</b> — короткие или развёрнутые\n"
    "• <b>Исправления</b> — указывать ли на ошибки сразу или дать выговориться\n\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "💡 <b>С чего начать прямо сейчас</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n\n"
    "• <i>«Let's just chat. Ask me about my day.»</i>\n"
    "• <i>«I want to practice ordering food at a restaurant.»</i>\n"
    "• <i>«Teach me 5 useful phrases for a job interview.»</i>\n"
    "• <i>«Pretend you're a tourist in Moscow and I'm helping you.»</i>\n\n"
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
        "/help — эта справка",
        "",
        "Чтобы начать практику — нажми «🎤 Начать разговор» в /start.",
    ]
    await message.answer(text="\n".join(lines), parse_mode="HTML")


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
    speaking_today = int(profile.get("speaking_seconds_today") or 0)
    lines.append("<b>⏱ Сегодня</b>")
    if FREE_PERIOD or profile["has_subscription"]:
        lines.append(f"Практика: <b>{_fmt_minutes(used_today)}</b> — без лимитов")
    else:
        free_speaking = int(profile.get("free_seconds_per_day") or FREE_DAILY_SECONDS)
        free_listening = int(profile.get("free_listening_per_day") or 2)
        free_grammar = int(profile.get("free_grammar_per_day") or 3)
        limit_min = free_speaking // 60
        total_limit = free_speaking + bonus_today
        left_sec = max(0, total_limit - speaking_today)
        if bonus_today > 0:
            lines.append(
                f"🗣 Разговор: <b>{_fmt_minutes(speaking_today)}</b> из "
                f"{limit_min} мин + бонус <b>{_fmt_minutes(bonus_today)}</b>"
            )
        else:
            lines.append(
                f"🗣 Разговор: <b>{_fmt_minutes(speaking_today)}</b> из {limit_min} мин"
            )
        if left_sec > 0:
            lines.append(
                f"Осталось разговора: <b>{_fmt_minutes(left_sec)}</b> (сброс в 00:00 МСК)"
            )
        else:
            lines.append(
                "Лимит разговора исчерпан — продолжи завтра или оформи подписку."
            )
        lines.append(
            f"🎧 Слушание: <b>{free_listening}</b>/день · "
            f"📝 грамматика: <b>{free_grammar}</b>/день · "
            "📚 слова — <b>без лимита</b>"
        )
    lines.append("")

    # Всего практики
    total_sec = profile["used_seconds_total"]
    lines.append("<b>📈 Всего практики</b>")
    lines.append(f"<b>{_fmt_total_practice(total_sec)}</b> за всё время")
    lines.append("")

    # Разбивка по режимам
    speaking_min = int(profile.get("speaking_minutes") or 0)
    listening_min = int(profile.get("listening_minutes") or 0)
    grammar_min = int(profile.get("grammar_minutes") or 0)
    srs_min = int(profile.get("srs_minutes") or 0)
    if speaking_min or listening_min or grammar_min or srs_min:
        lines.append("<b>🎯 По режимам</b>")
        lines.append(f"🎙 Разговор: <b>{speaking_min}</b> мин")
        lines.append(f"🎧 Слушание: <b>{listening_min}</b> мин")
        lines.append(f"📝 Грамматика: <b>{grammar_min}</b> мин")
        lines.append(f"📚 Слова: <b>{srs_min}</b> мин")
        lines.append("")

    # Грамматика — пройдено тем
    gtopics_done = int(profile.get("grammar_topics_done") or 0)
    gtopics_total = int(profile.get("grammar_topics_total") or 0)
    if gtopics_total > 0:
        lines.append("<b>📝 Грамматика</b>")
        if gtopics_done > 0:
            lines.append(
                f"Пройдено тем: <b>{gtopics_done}</b> из {gtopics_total}"
            )
        else:
            lines.append(
                f"Доступно <b>{gtopics_total}</b> тем — начни с любого уровня "
                "в mini app."
            )
        lines.append("")

    # Словарь и медали
    words_count = int(profile.get("total_words") or 0)
    ach_earned = int(profile.get("achievements_earned") or 0)
    ach_total = int(profile.get("achievements_total") or 0)
    if words_count or ach_total:
        lines.append("<b>📚 Достижения</b>")
        if words_count:
            lines.append(f"Слов в словаре: <b>{words_count}</b>")
        if ach_total:
            lines.append(f"Медалей: <b>{ach_earned}</b> из {ach_total}")
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
    "На бесплатном тарифе в день доступно: <b>20 минут разговора</b>, "
    "<b>2 подкаста</b> и <b>3 урока грамматики</b>. Словарь и повторение слов — "
    "всегда бесплатно. Лимиты сбрасываются в полночь по МСК.\n\n"
    "С подпиской — <b>без лимитов</b> и круглые сутки:\n"
    f"• <b>{PRICE_TRIAL3_RUB} ₽ / 3 дня</b>\n"
    f"• <b>{PRICE_MONTHLY_RUB} ₽ / месяц</b>\n"
    f"• <b>{PRICE_YEARLY_RUB} ₽ / год</b> (выгоднее ~50%)\n\n"
    "Оплата картой, SberPay или ЮМани — через ЮКассу прямо в Telegram. "
    "<i>Чек будет отправлен на указанный email.</i>\n\n"
    "💛 <b>Нет возможности оплатить?</b> Это не повод бросать английский. "
    "Напиши «прошу доступ» в комментариях под любым постом в канале "
    "@kmo_ai — и я выдам подписку бесплатно, без условий и лишних вопросов. "
    "Учиться должны все, у кого есть желание."
)


def _subscribe_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💳 3 дня — {PRICE_TRIAL3_RUB} ₽",
                    callback_data="subscribe:trial3",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"💳 Месяц — {PRICE_MONTHLY_RUB} ₽",
                    callback_data="subscribe:monthly",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"💳 Год — {PRICE_YEARLY_RUB} ₽",
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
    "trial3": {
        "title": "English Tutor — 3 дня",
        "description": "Безлимитный доступ ко всем режимам на 3 дня.",
        "amount_rub": PRICE_TRIAL3_RUB,
        "days": 3,
        "label": "3 дня",
    },
    "monthly": {
        "title": "English Tutor — подписка на месяц",
        "description": "Безлимитный доступ ко всем режимам на 30 дней.",
        "amount_rub": PRICE_MONTHLY_RUB,
        "days": 30,
        "label": "месяц",
    },
    "yearly": {
        "title": "English Tutor — подписка на год",
        "description": "Безлимитный доступ ко всем режимам на 365 дней (выгоднее ~50%).",
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
        # Win-back: реактивирует юзеров, не заходивших 3+ дня (миграция 0007).
        from .reminders import winback_loop
        asyncio.create_task(winback_loop(bot, MINIAPP_URL))
        logger.info("Starting winback loop (DB ready)")
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
    allowed = [
        "message", "edited_message", "callback_query",
        "pre_checkout_query", "my_chat_member",
    ]
    await dp.start_polling(bot, skip_updates=True, allowed_updates=allowed)


if __name__ == "__main__":
    asyncio.run(main())
