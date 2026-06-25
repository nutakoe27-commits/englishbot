/**
 * LoginScreen.tsx — экран входа для веб-версии (вне Telegram).
 *
 * Основной вход: Яндекс ID (OAuth 2.0, миграция 0023). Email/пароль —
 * вторичный вариант. Telegram как способ входа на сайте УБРАН (юр.
 * ограничения РФ); привязка Telegram-аккаунта остаётся в Аккаунте.
 *
 * UI v2: warm cream surface, Source Serif heading, sage CTA для Яндекс,
 * lucide-иконки.
 */

import { useEffect, useState } from "react";
import {
  extractYandexCallback,
  loginNative,
  registerNative,
  startYandexFlow,
} from "./auth";
import { Button } from "./ds-react/Button";
import { LogoBox } from "./ds-react/LogoBox";
import { SerifH } from "./ds-react/typography";
import { Icon } from "./ds-react/Icon";
import { useLucide } from "./lucide";

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

  useLucide(`${tab}-${busy}-${!!error}`);

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
    <div className="login-v2">
      <div className="login-v2__card">
        <div className="login-v2__brand">
          <LogoBox size={44} />
          <div className="login-v2__brand-text">
            <span className="login-v2__brand-name">English Tutor</span>
            <span className="login-v2__brand-tag">AI-репетитор</span>
          </div>
        </div>

        <SerifH as="h1" size={28} className="login-v2__title">
          Вход в English Tutor
        </SerifH>
        <p className="login-v2__subtitle">
          Говори, слушай, учи грамматику и слова с AI-репетитором. Войди, чтобы
          сохранять прогресс на любом устройстве.
        </p>

        <button
          type="button"
          className="login-v2__yandex"
          onClick={startYandex}
          disabled={busy}
        >
          <span className="login-v2__yandex-mark" aria-hidden>Я</span>
          <span>Войти через Яндекс ID</span>
        </button>
        <p className="login-v2__hint">Быстрый вход через аккаунт Яндекса.</p>

        <div className="login-v2__divider"><span>или войти по email</span></div>

        <div className="login-v2__tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "login"}
            className={`login-v2__tab ${tab === "login" ? "is-active" : ""}`}
            onClick={() => { setTab("login"); setError(""); }}
          >Вход</button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "register"}
            className={`login-v2__tab ${tab === "register" ? "is-active" : ""}`}
            onClick={() => { setTab("register"); setError(""); }}
          >Регистрация</button>
        </div>

        <form className="login-v2__form" onSubmit={submit}>
          {tab === "register" && (
            <input
              className="login-v2__input"
              type="text"
              placeholder="Имя (необязательно)"
              value={firstName}
              onChange={(e) => setFirstName(e.target.value)}
              maxLength={64}
              autoComplete="given-name"
            />
          )}
          <input
            className="login-v2__input"
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
            className="login-v2__input"
            type="password"
            placeholder={tab === "register" ? "Пароль (от 8 символов)" : "Пароль"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete={tab === "register" ? "new-password" : "current-password"}
            minLength={8}
            maxLength={128}
          />
          <Button type="submit" variant="secondary" fullWidth disabled={busy}>
            {busy ? "…" : tab === "login" ? "Войти" : "Зарегистрироваться"}
          </Button>
        </form>

        {error && <p className="login-v2__error">{error}</p>}

        <a
          className="login-v2__channel"
          href="https://t.me/kmo_ai"
          target="_blank"
          rel="noreferrer"
        >
          <Icon name="megaphone" size={14} /> Новости проекта — @kmo_ai
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
