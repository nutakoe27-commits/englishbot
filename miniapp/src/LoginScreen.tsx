/**
 * LoginScreen.tsx — экран входа для веб-версии (вне Telegram).
 *
 * Способы: Telegram Login Widget + нативная email/password регистрация
 * (миграция 0021). Google убран. VK ID — позже.
 */

import { useEffect, useRef, useState } from "react";
import {
  BOT_USERNAME,
  loginNative,
  loginTelegramWidget,
  registerNative,
} from "./auth";

interface Props {
  onAuthed: () => void;
}

declare global {
  interface Window {
    onTelegramAuth?: (user: Record<string, unknown>) => void;
  }
}

type Tab = "login" | "register";

export function LoginScreen({ onAuthed }: Props) {
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [tab, setTab] = useState<Tab>("login");
  const [email, setEmail] = useState<string>("");
  const [password, setPassword] = useState<string>("");
  const [firstName, setFirstName] = useState<string>("");
  const tgBoxRef = useRef<HTMLDivElement | null>(null);

  // ── Telegram Login Widget ──────────────────────────────────────────────
  useEffect(() => {
    window.onTelegramAuth = async (user) => {
      setBusy(true); setError("");
      try {
        const ok = await loginTelegramWidget(user);
        if (ok) onAuthed();
        else setError("Не удалось войти через Telegram. Попробуй ещё раз.");
      } catch {
        setError("Ошибка сети. Попробуй ещё раз.");
      } finally { setBusy(false); }
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
    return () => { window.onTelegramAuth = undefined; };
  }, [onAuthed]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setError(""); setBusy(true);
    try {
      const r = tab === "login"
        ? await loginNative(email, password)
        : await registerNative(email, password, firstName.trim() || undefined);
      if (r.ok) { onAuthed(); return; }
      if (r.error === "bad_credentials") setError("Неверный email или пароль.");
      else if (r.error === "email_taken") setError("Этот email уже зарегистрирован. Войди по нему.");
      else if (r.error === "bad_email") setError("Введи корректный email.");
      else if (r.error === "weak_password") setError("Пароль слишком короткий — минимум 8 символов.");
      else setError("Что-то пошло не так. Попробуй ещё раз.");
    } catch { setError("Ошибка сети. Попробуй ещё раз."); }
    finally { setBusy(false); }
  };

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

        <div className="login-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "login"}
            className={`login-tab ${tab === "login" ? "is-active" : ""}`}
            onClick={() => { setTab("login"); setError(""); }}
          >Вход</button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "register"}
            className={`login-tab ${tab === "register" ? "is-active" : ""}`}
            onClick={() => { setTab("register"); setError(""); }}
          >Регистрация</button>
        </div>

        <form className="login-form" onSubmit={submit}>
          {tab === "register" && (
            <input
              className="login-input"
              type="text"
              placeholder="Имя (необязательно)"
              value={firstName}
              onChange={(e) => setFirstName(e.target.value)}
              maxLength={64}
              autoComplete="given-name"
            />
          )}
          <input
            className="login-input"
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
            inputMode="email"
            maxLength={255}
          />
          <input
            className="login-input"
            type="password"
            placeholder={tab === "register" ? "Пароль (от 8 символов)" : "Пароль"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete={tab === "register" ? "new-password" : "current-password"}
            minLength={8}
            maxLength={128}
          />
          <button
            type="submit"
            className="btn btn--primary login-submit"
            disabled={busy}
          >
            {busy ? "…" : tab === "login" ? "Войти" : "Зарегистрироваться"}
          </button>
        </form>

        <div className="login-divider"><span>или</span></div>

        <div className="login-buttons">
          <div ref={tgBoxRef} className="login-tg" />
          <p className="login-hint">VK ID — добавим позже.</p>
        </div>

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
