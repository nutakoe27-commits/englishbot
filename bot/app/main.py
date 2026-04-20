import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
MINIAPP_URL: str = os.getenv("MINIAPP_URL", "https://englishbot.krichigindocs.ru")

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
async def cmd_start(message: Message) -> None:
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
@dp.message(Command("subscribe"))
async def cmd_subscribe(message: Message) -> None:
    await message.answer(
        text=(
            "⭐ <b>Подписка</b>\n\n"
            "⏳ Скоро: расширенный доступ к AI-репетитору. "
            "Следи за обновлениями!"
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
            BotCommand(command="help", description="Справка по командам"),
        ]
    )


# ─── Точка входа ─────────────────────────────────────────────────────────────
async def main() -> None:
    logger.info("Starting bot in long polling mode...")
    await _set_bot_commands()
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
