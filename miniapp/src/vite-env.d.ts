/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Username Telegram-бота без '@' (передаётся build-арг VITE_BOT_USERNAME). */
  readonly VITE_BOT_USERNAME?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
