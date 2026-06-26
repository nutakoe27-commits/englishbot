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

    # Telegram Bot token — используется для валидации initData и Login Widget,
    # а также для отправки сообщений в чат юзеру (PR-6: подтверждение unlink).
    BOT_TOKEN: Optional[str] = None

    # Username бота без '@' — для построения deep-link t.me/<bot>?start=…
    BOT_USERNAME: str = "kmo_ai_english_bot"

    # Общий секрет для аутентификации запросов bot ↔ backend (header
    # X-Bot-Secret). Используется на /api/internal/auth/* endpoint'ах.
    BACKEND_BOT_SECRET: Optional[str] = None

    # ─── Веб-авторизация (миграция 0020 + 0021) ──────────────────────────
    # Секрет для подписи JWT сессий. Генерируй: openssl rand -hex 32
    # Если не задан — выдача/проверка JWT отключена (работает только Mini App).
    AUTH_JWT_SECRET: Optional[str] = None
    # Срок жизни JWT (дни).
    AUTH_JWT_TTL_DAYS: int = 30
    # Публичный URL backend (используется для построения Yandex OAuth redirect URI).
    API_PUBLIC_URL: Optional[str] = None
    WEB_APP_URL: Optional[str] = None

    # ─── Яндекс ID (миграция 0023, PR-7) ─────────────────────────────────
    # Регистрация: https://oauth.yandex.ru → «Веб-сервисы»; redirect URI =
    # <API_PUBLIC_URL>/api/auth/yandex/callback. Scopes: login:email, login:info.
    YANDEX_CLIENT_ID: Optional[str] = None
    YANDEX_CLIENT_SECRET: Optional[str] = None
    # Если пусто — собирается из API_PUBLIC_URL + '/api/auth/yandex/callback'.
    YANDEX_REDIRECT_URI: Optional[str] = None

    # ─── ЮKassa веб-оплата (PR-8) ────────────────────────────────────────
    # Прямой API ЮKassa для оплаты подписки на сайте — параллельно с
    # Telegram Payments в боте (тот идёт через provider_token).
    # YOOKASSA_SHOP_ID — тот же магазин, что у бота.
    # YOOKASSA_SECRET_KEY — API-ключ из ЛК ЮKassa, НЕ provider_token бота.
    YOOKASSA_SHOP_ID: Optional[str] = None
    YOOKASSA_SECRET_KEY: Optional[str] = None
    # Фискализация (54-ФЗ): отправлять чек в ЮKassa. На проде обязательно.
    YOOKASSA_FISCALIZATION: bool = True
    # Ставка НДС: 1=без НДС, 2=0%, 3=10%, 4=20% и т.д.
    YOOKASSA_VAT_CODE: int = 1

    # Цены подписки в рублях (синхронизированы с bot/app/main.py).
    SUBSCRIPTION_PRICE_TRIAL3_RUB: int = 99
    SUBSCRIPTION_PRICE_MONTHLY_RUB: int = 999
    SUBSCRIPTION_PRICE_YEARLY_RUB: int = 5999

    # ─── База данных ──────────────────────────────────────────────────────
    # Формат: mysql+asyncmy://user:password@host:port/dbname?charset=utf8mb4
    # На VPS: mysql+asyncmy://englishbot:PWD@host.docker.internal:3306/englishbot?charset=utf8mb4
    # Старые .env с mysql+aiomysql://… продолжают работать — engine.py
    # автоматически нормализует префикс при инициализации.
    DATABASE_URL: Optional[str] = None

    # ─── Админы ────────────────────────────────────────────────────────────
    # Telegram ID админов через запятую: "123456,789012"
    ADMIN_IDS: str = ""

    # Токен для админ-панели. Передаётся в заголовке X-Admin-Token.
    # Генерируй разовым: openssl rand -hex 32
    ADMIN_TOKEN: Optional[str] = None

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

    # ─── Free Period (промо без оплаты) ──────────────────────────────────
    # Когда True — все юзеры считаются подписчиками, дневной лимит 10 мин
    # снят, кнопка подписки и /subscribe в боте скрыты.
    FREE_PERIOD: bool = False

    # ─── LLM таймаут на один turn (секунды) ──────────────────────────────
    # Защищает WS-сессию от зависшего vLLM. При превышении — отдаём fallback
    # и продолжаем сессию. 0 = без таймаута (не рекомендуется).
    LLM_TIMEOUT_SEC: int = 30

    @property
    def admin_url(self) -> str:
        return f"https://{self.ADMIN_HOST}"

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"


settings = Settings()

# Системный промпт для LLM собирается динамически из SessionSettings — см. tutor_prompt.py
