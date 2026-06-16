/**
 * LoginScreen.tsx — экран входа для веб-версии (вне Telegram).
 *
 * Telegram — через Login Widget. Google — через серверный OAuth-redirect
 * (GIS не используем: его блокирует webview Telegram, а в браузере он капризен).
 */

import { useEffect, useRef, useState } from "react";
import {
  BOT_USERNAME,
  GOOGLE_CLIENT_ID,
  googleStartUrl,
  loginTelegramWidget,
} from "./auth";

interface Props {
  onAuthed: () => void;
}

declare global {
  interface Window {
    onTelegramAuth?: (user: Record<string, unknown>) => void;
    google?: any;
  }
}

export function LoginScreen({ onAuthed }: Props) {
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const tgBoxRef = useRef<HTMLDivElement | null>(null);

  // ── Telegram Login Widget ──────────────────────────────────────────────
  useEffect(() => {
    window.onTelegramAuth = async (user) => {
      setBusy(true);
      setError("");
      try {
        const ok = await loginTelegramWidget(user);
        if (ok) onAuthed();
        else setError("Не удалось войти через Telegram. Попробуй ещё раз.");
      } catch {
        setError("Ошибка сети. Попробуй ещё раз.");
      } finally {
        setBusy(false);
      }
    };

    const box = tgBoxRef.current;
    if (box && BOT_USERNAME && !box.querySelector("script")) {
      const s = document.createElement("script");
      s.src = "https://telegram.org/js/telegram-widget.js?22";
      s.async = true;
      s.setAttribute("data-telegram-login", BOT_USERNAME);
      s.setAttribute("data-size", "large");
      s.setAttribute("data-radius", "12");
      s.setAttribute("data-onauth", "onTelegramAuth(user)");
      s.setAttribute("data-request-access", "write");
      box.appendChild(s);
    }
    return () => {
      window.onTelegramAuth = undefined;
    };
  }, [onAuthed]);

  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="login-brand">
          <span className="tutor-brand__dot" aria-hidden />
          <span className="login-brand__name">English Tutor</span>
        </div>
        <h1 className="login-title">Вход в English Tutor</h1>
        <p className="login-subtitle">
          Говори, слушай, учи грамматику и слова с AI-репетитором. Войди, чтобы
          сохранять прогресс на любом устройстве.
        </p>

        <div className="login-buttons">
          <div ref={tgBoxRef} className="login-tg" />
          {GOOGLE_CLIENT_ID ? (
            <button
              type="button"
              className="login-google-btn"
              onClick={() => { window.location.href = googleStartUrl(); }}
            >
              <span aria-hidden>🟢</span> Войти через Google
            </button>
          ) : (
            <p className="login-hint">Вход через Google скоро будет доступен.</p>
          )}
        </div>

        {busy && <p className="login-hint">Входим…</p>}
        {error && <p className="login-error">{error}</p>}

        <a
          className="login-channel"
          href="https://t.me/kmo_ai"
          target="_blank"
          rel="noreferrer"
        >
          📣 Новости проекта — @kmo_ai
        </a>
      </div>
    </div>
  );
}
