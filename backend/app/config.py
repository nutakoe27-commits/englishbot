from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Режим окружения
    ENVIRONMENT: str = "production"

    # URL Mini App — используется в CORS
    MINIAPP_URL: str = "https://englishbot.krichigindocs.ru"

    # Хост admin-панели — используется в CORS
    ADMIN_HOST: str = "admin-english.krichigindocs.ru"

    # Telegram Bot token — используется для валидации initData
    BOT_TOKEN: Optional[str] = None

    # Gemini AI через Vertex AI.
    # Мы используем Vertex AI (а не Gemini Developer API), потому что сервер
    # расположен в РФ, а Gemini Developer API блокирует российские IP.
    # Аутентификация — через service account JSON
    # (переменная GOOGLE_APPLICATION_CREDENTIALS).
    GOOGLE_CLOUD_PROJECT: Optional[str] = None
    GOOGLE_CLOUD_LOCATION: str = "us-central1"

    # Vertex AI Live API model ID (GA с 12 декабря 2025).
    # Поддержка до 12 декабря 2026.
    GEMINI_MODEL: str = "gemini-live-2.5-flash-native-audio"
    # Доступные голоса: Puck, Charon, Kore, Fenrir, Aoede, Zephyr
    GEMINI_VOICE: str = "Puck"

    @property
    def admin_url(self) -> str:
        return f"https://{self.ADMIN_HOST}"

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"


settings = Settings()

# ─── Системный промпт для AI-репетитора ───────────────────────────────────────
SYSTEM_PROMPT = """You are a friendly English conversation partner for a learner at A2-B1 level.

Rules:
- Speak ONLY in English.
- Use simple vocabulary and short, natural sentences.
- When the learner makes a grammar or pronunciation mistake, gently repeat the correct phrase back in your reply without explicitly pointing out the mistake.
- Ask engaging follow-up questions to keep the conversation going.
- Keep your responses to 1-2 sentences maximum, like in a real chat.
- If the learner asks for a translation or switches to another language, gently steer back to English.
- Be warm, encouraging, and patient.
"""
