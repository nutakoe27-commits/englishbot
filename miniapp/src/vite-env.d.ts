/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Username Telegram-бота без '@' (передаётся build-арг VITE_BOT_USERNAME). */
  readonly VITE_BOT_USERNAME?: string;
  /** Базовый URL backend API (передаётся build-арг VITE_API_BASE). */
  readonly VITE_API_BASE?: string;
  /** Google OAuth Client ID (тип Web) для входа на вебе. */
  readonly VITE_GOOGLE_CLIENT_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
