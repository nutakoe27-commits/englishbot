import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
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


# ─── /start ──────────────────────────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    user_name = message.from_user.first_name if message.from_user else "друг"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎤 Начать разговор",
                    web_app=WebAppInfo(url=MINIAPP_URL),
                )
            ]
        ]
    )

    await message.answer(
        text=(
            f"Привет, {user_name}! 👋\n\n"
            "Я — твой AI-репетитор английского. "
            "Практикуй разговорный английский в любое время — "
            "просто нажми кнопку ниже и начни диалог.\n\n"
            "📚 Используй /help, чтобы узнать все доступные команды."
        ),
        reply_markup=keyboard,
    )


# ─── /help ───────────────────────────────────────────────────────────────────
@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        text=(
            "ℹ️ <b>Справка по командам</b>\n\n"
            "/start — главное меню и кнопка запуска разговора\n"
            "/help — эта справка\n"
            "/profile — твой прогресс и статистика\n"
            "/subscribe — информация о подписке\n\n"
            "Для практики английского нажми кнопку "
            "<b>🎤 Начать разговор</b> в главном меню."
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
            "⏳ Скоро: расширенный доступ к AI-репетитору "
            "через Telegram Stars. Следи за обновлениями!"
        ),
        parse_mode="HTML",
    )


# ─── Точка входа ─────────────────────────────────────────────────────────────
async def main() -> None:
    logger.info("Starting bot in long polling mode...")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
