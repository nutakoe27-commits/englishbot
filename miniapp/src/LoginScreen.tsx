/**
 * LoginScreen.tsx — экран входа для веб-версии (вне Telegram).
 *
 * Варианты: Telegram Login Widget и Google Identity Services.
 * После успешного входа сохраняем JWT и зовём onAuthed().
 */

import { useEffect, useRef, useState } from "react";
import {
  BOT_USERNAME,
  GOOGLE_CLIENT_ID,
  loginGoogle,
  loginTelegramWidget,
} from "./auth";

interface Props {
  onAuthed: () => void;
}

// Глобальные коллбеки/SDK сторонних виджетов.
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
  const googleBoxRef = useRef<HTMLDivElement | null>(null);

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

  // ── Google Identity Services ───────────────────────────────────────────
  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) return;

    const handleCredential = async (resp: { credential?: string }) => {
      if (!resp?.credential) return;
      setBusy(true);
      setError("");
      try {
        const r = await loginGoogle(resp.credential);
        if (r.ok) onAuthed();
        else if (r.error === "email_taken")
          setError("Этот email уже используется. Войди прежним способом и привяжи Google в настройках.");
        else setError("Не удалось войти через Google.");
      } catch {
        setError("Ошибка сети. Попробуй ещё раз.");
      } finally {
        setBusy(false);
      }
    };

    const render = () => {
      if (!window.google?.accounts?.id || !googleBoxRef.current) return;
      window.google.accounts.id.initialize({
        client_id: GOOGLE_CLIENT_ID,
        callback: handleCredential,
        ux_mode: "popup",
      });
      // Без фикс. width — иначе GIS не рендерит кнопку на узких экранах,
      // где контейнер уже запрошенной ширины (мобильные телефоны).
      window.google.accounts.id.renderButton(googleBoxRef.current, {
        theme: "filled_black",
        size: "large",
        shape: "pill",
        text: "continue_with",
      });
    };

    if (window.google?.accounts?.id) {
      render();
    } else {
      const existing = document.getElementById("gsi-script");
      if (!existing) {
        const s = document.createElement("script");
        s.id = "gsi-script";
        s.src = "https://accounts.google.com/gsi/client";
        s.async = true;
        s.defer = true;
        s.onload = render;
        document.head.appendChild(s);
      } else {
        existing.addEventListener("load", render);
      }
    }
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
            <div ref={googleBoxRef} className="login-google" />
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
