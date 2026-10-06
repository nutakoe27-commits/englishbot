/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Username Telegram-бота без '@' (передаётся build-арг VITE_BOT_USERNAME). */
  readonly VITE_BOT_USERNAME?: string;
  /** Базовый URL backend API (передаётся build-арг VITE_API_BASE). */
  readonly VITE_API_BASE?: string;
  /** ID счётчика VK MyTracker (web). Пусто — MyTracker выключен. */
  readonly VITE_MYTRACKER_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
