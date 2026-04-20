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

    # ─── База данных ──────────────────────────────────────────────────────
    # Формат: mysql+aiomysql://user:password@host:port/dbname?charset=utf8mb4
    # На VPS: mysql+aiomysql://englishbot:PWD@host.docker.internal:3306/englishbot?charset=utf8mb4
    DATABASE_URL: Optional[str] = None

    # ─── Админы ────────────────────────────────────────────────────────────
    # Telegram ID админов через запятую: "123456,789012"
    ADMIN_IDS: str = ""

    @property
    def admin_ids_list(self) -> list[int]:
        if not self.ADMIN_IDS:
            return []
        out: list[int] = []
        for part in self.ADMIN_IDS.split(","):
            part = part.strip()
            if part.isdigit():
                out.append(int(part))
        return out

    # ─── LLM (vLLM на V100) ──────────────────────────────────────────────
    # OpenAI-совместимый endpoint. В проде host.docker.internal через
    # SSH-reverse-tunnel до V100: http://host.docker.internal:23333/v1
    VLLM_BASE_URL: Optional[str] = None

    # Имя модели, как его выставил vLLM (--served-model-name).
    # Пример: "QuantTrio/Qwen3.5-35B-A3B-AWQ"
    VLLM_MODEL_NAME: Optional[str] = None

    # API-ключ vLLM. По умолчанию vLLM его не требует — используем "not-needed".
    VLLM_API_KEY: Optional[str] = None

    # ─── STT (Whisper на V100) ───────────────────────────────────────────
    # URL WebSocket-endpoint faster-whisper сервера.
    # В проде: ws://host.docker.internal:23334/ws
    WHISPER_STT_URL: Optional[str] = None

    # Язык распознавания для Whisper. "en" — принудительно английский.
    # None/"" — авто-детект (медленнее).
    WHISPER_STT_LANGUAGE: str = "en"

    # ─── TTS (Kokoro-82M на V100) ────────────────────────────────────────
    # URL WebSocket-endpoint Kokoro-сервера.
    # В проде: ws://host.docker.internal:23335/ws
    KOKORO_TTS_URL: Optional[str] = None

    # Голос Kokoro. Список и оценки качества:
    # https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md
    # af_heart (A), af_bella (A-) — женские US; am_michael (C+) — мужской US;
    # bf_emma (B-) — женский UK.
    KOKORO_TTS_VOICE: str = "af_heart"

    # Скорость речи Kokoro (0.5 — медленно, 1.0 — обычно, 2.0 — быстро).
    KOKORO_TTS_SPEED: float = 1.0

    @property
    def admin_url(self) -> str:
        return f"https://{self.ADMIN_HOST}"

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"


settings = Settings()

# Системный промпт для LLM собирается динамически из SessionSettings — см. tutor_prompt.py
