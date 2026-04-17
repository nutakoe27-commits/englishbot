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

    # Gemini AI (опционально до Phase 2)
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-2.5-flash"

    @property
    def admin_url(self) -> str:
        return f"https://{self.ADMIN_HOST}"

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"


settings = Settings()
