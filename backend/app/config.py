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

    # ─── Yandex Cloud (SpeechKit + YandexGPT) ─────────────────────────────
    # Мы используем Yandex, потому что сервер в РФ.
    # Gemini/Vertex либо блокирует РФ-IP, либо требует активного billing.
    # Yandex работает напрямую и принимает оплату в рублях.

    # API-ключ сервисного аккаунта с ролями:
    #   ai.speechkit-stt.user, ai.speechkit-tts.user, ai.languageModels.user
    YC_API_KEY: Optional[str] = None

    # ID каталога Yandex Cloud (формат b1g... или bpf...).
    # Нужен для YandexGPT modelUri и для биллинга.
    YC_FOLDER_ID: Optional[str] = None

    # Голос для TTS.
    # Английские голоса: john, nick, alyss (вежливые, нейтральные).
    # Русские голоса: alena, filipp, ermil, zahar, jane, oksana.
    # Для английского репетитора по умолчанию — john.
    YC_TTS_VOICE: str = "john"

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
