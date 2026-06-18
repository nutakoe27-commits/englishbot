/**
 * LoginScreen.tsx — экран входа для веб-версии (вне Telegram).
 *
 * Основной вход: Яндекс ID (OAuth 2.0, миграция 0023). Email/пароль —
 * вторичный вариант. Telegram как способ входа на сайте УБРАН (юр.
 * ограничения РФ); привязка Telegram-аккаунта остаётся в Аккаунте.
 */

import { useEffect, useState } from "react";
import {
  extractYandexCallback,
  loginNative,
  registerNative,
  startYandexFlow,
} from "./auth";

interface Props {
  onAuthed: () => void;
}

type Tab = "login" | "register";

export function LoginScreen({ onAuthed }: Props) {
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [tab, setTab] = useState<Tab>("login");
  const [email, setEmail] = useState<string>("");
  const [password, setPassword] = useState<string>("");
  const [firstName, setFirstName] = useState<string>("");

  // ── Обработать возврат с Яндекс OAuth ──────────────────────────────────
  useEffect(() => {
    const r = extractYandexCallback();
    if (!r) return;
    if (r.jwt && r.mode === "login") {
      onAuthed();
      return;
    }
    if (r.error) {
      setError(_yandexErrorMessage(r.error));
    }
  }, [onAuthed]);

  const startYandex = async () => {
    if (busy) return;
    setError(""); setBusy(true);
    try {
      const r = await startYandexFlow("login");
      if (!r) {
        setError("Не удалось запустить вход через Яндекс. Попробуй ещё раз.");
        return;
      }
      window.location.href = r.url;
    } finally { setBusy(false); }
  };

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

        <button
          type="button"
          className="btn btn--primary login-yandex-btn"
          onClick={startYandex}
          disabled={busy}
        >
          🟡 Войти через Яндекс ID
        </button>
        <p className="login-hint">Быстрый вход через аккаунт Яндекса.</p>

        <div className="login-divider"><span>или войти по email</span></div>

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
            className="btn btn--ghost login-submit"
            disabled={busy}
          >
            {busy ? "…" : tab === "login" ? "Войти" : "Зарегистрироваться"}
          </button>
        </form>

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

function _yandexErrorMessage(code: string): string {
  switch (code) {
    case "state_invalid":
      return "Ссылка устарела. Попробуй войти ещё раз.";
    case "exchange_failed":
    case "userinfo_failed":
      return "Не получилось проверить аккаунт Яндекса. Попробуй ещё раз.";
    case "access_denied":
      return "Доступ к аккаунту Яндекса не разрешён.";
    case "identity_conflict":
      return "Этот Яндекс уже привязан к другому аккаунту, и у обоих есть свои способы входа. Сначала отвяжи лишний способ в одном из аккаунтов.";
    default:
      return "Не удалось войти через Яндекс. Попробуй ещё раз.";
  }
}
